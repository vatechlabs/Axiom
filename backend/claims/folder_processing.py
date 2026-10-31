"""Durable scan queue, schedules, per-file results, and reconciliation."""
import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from .models import (Client, FolderScanSchedule, FolderScanRun, FolderScanItem,
                     FolderFileState, EdiDocument, ErrorHistory)
from .folder_paths import client_paths, allowed_roots
from .rule_engine import rules_for, has_rules, rule_files, scan_rule_entries, wgs_835_year_allowed, wgs_837_year_allowed
from .importers import index_edi, import_errors, reconcile_client
from .processing_locks import client_processing_lock, lag_upload_lock, ClientProcessingBusy
from .smb_auth import connect_share
from .recon_processing import scan_recon_files  # AXIOM_RECON_SCHEDULE_V1
from .highmark_837 import HIGHMARK_CODES, highmark_extensionless_837


def queue_scan(client, trigger='manual'):
    with transaction.atomic():
        # All queue producers acquire the same row lock before creating a run.
        Client.objects.select_for_update().get(pk=client.pk)
        active=FolderScanRun.objects.filter(client=client,status__in=['queued','running']).first()
        if active:
            return active,False
        return FolderScanRun.objects.create(client=client,trigger=trigger),True


def due_today(schedule, now):
    local=now.astimezone(ZoneInfo(schedule.timezone_name))
    return (schedule.enabled and local.weekday() in schedule.weekdays
            and local.time().replace(tzinfo=None)>=schedule.daily_time
            and schedule.last_scheduled_date!=local.date())


def _item(run,kind,path,status,**kwargs):
    return FolderScanItem.objects.create(run=run,kind=kind,filename=path.name[:255],path=str(path),status=status,**kwargs)


def perform_scan(run,schedule):
    client=run.client
    paths=client_paths(client)
    roots=allowed_roots()
    processed=skipped=deferred=failed=0
    seen_files=set()
    for key,kind in [('error_reports','error'),('837','837'),('835','835')]:
        if client.code.upper()=='WGS' and key in ('835','837'):
            try:
                connect_share(client)
            except (OSError, RuntimeError) as exc:
                failed += 1
                _item(run,kind,Path('WGS_'+kind),'failed',message=str(exc)[:3000])
                continue
        rules=rules_for(client,key) if has_rules(client,key) else [None]
        for rule in rules:
            folder=Path(rule.folder_path if rule else paths[key])
            try:
                folder=folder.resolve()
                if rule:
                    candidates=scan_rule_entries(rule)
                else:
                    if not any(folder.is_relative_to(root) for root in roots) or not folder.is_dir():
                        raise ValueError('Folder missing, inaccessible, or outside approved roots')
                    candidates=((path,None) for path in folder.rglob('*'))
            except Exception as exc:
                _item(run,kind,folder,'failed',message=str(exc));failed+=1;continue
            def safe_candidates():
                nonlocal failed
                try:
                    yield from candidates
                except (OSError, ValueError) as exc:
                    failed += 1
                    _item(run,kind,folder,'failed',message=str(exc)[:3000])
                    FolderScanRun.objects.filter(pk=run.pk).update(failed=failed)
            FolderScanRun.objects.filter(pk=run.pk).update(message='Scanning '+kind+' files...')
            for path,known_stat in safe_candidates():
                identity=(kind,str(path).casefold())
                if identity in seen_files:continue
                seen_files.add(identity)
                if path.name.startswith('.'):
                    continue
                suffix=path.suffix.lower()
                if kind=='835' and client.code.upper()=='WGS' and not wgs_835_year_allowed(path):
                    continue
                if kind=='837' and client.code.upper()=='WGS' and not wgs_837_year_allowed(path):
                    continue
                if kind=='error' and suffix!='.txt':
                    continue
                if (kind!='error' and suffix not in ('.edi','.'+kind,'.txt')
                        and not (kind=='837' and client.code.upper() in HIGHMARK_CODES
                                 and highmark_extensionless_837(path))):
                    continue
                try:
                    if rule is None:
                        path=path.resolve()
                        if not any(path.is_relative_to(root) for root in roots):
                            raise ValueError('File is outside approved roots')
                        if not path.is_file():continue
                    before=known_stat if known_stat is not None else path.stat()
                    # Ignore transfers still settling; the next scheduled/manual run retries.
                    if timezone.now().timestamp()-before.st_mtime<60:
                        _item(run,kind,path,'deferred',message='Recently modified; waiting for transfer to finish. Run again after 60 seconds.');deferred+=1;continue
                    digest=hashlib.sha256(str(path).encode('utf-8')).hexdigest()
                    state=FolderFileState.objects.filter(client=client,kind=kind,path_hash=digest).first()
                    if state and (state.bytes_size,state.modified_ns)==(before.st_size,before.st_mtime_ns):
                        skipped+=1
                        if skipped % 50 == 0:
                            FolderScanRun.objects.filter(pk=run.pk).update(skipped=skipped)
                            FolderScanSchedule.objects.filter(pk=schedule.pk).update(worker_seen_at=timezone.now())
                        continue
                    # Persist before indexing: a crash must not lose the need to reconcile.
                    FolderScanSchedule.objects.filter(pk=schedule.pk).update(needs_reconcile=True,worker_seen_at=timezone.now())
                    if kind=='error':
                        with transaction.atomic():
                            obj,unchanged=import_errors(client,path,reconcile=False)
                            after=path.stat()
                            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                                raise ValueError('File changed during import; rolled back and will retry')
                        count=obj.processed_count
                        refs={'import_batch':obj}
                    else:
                        obj,unchanged=index_edi(client,path,kind)
                        count=obj.claims_indexed.count()
                        refs={'edi_file':obj}
                    FolderFileState.objects.update_or_create(client=client,kind=kind,path_hash=digest,defaults={'bytes_size':before.st_size,'modified_ns':before.st_mtime_ns})
                    _item(run,kind,path,'unchanged' if unchanged else 'indexed',indexed_count=count,**refs)
                    if unchanged:skipped+=1
                    else:processed+=1
                except Exception as exc:
                    failed+=1
                    _item(run,kind,path,'failed',message=str(exc)[:3000])
                FolderScanRun.objects.filter(pk=run.pk).update(processed=processed,skipped=skipped,deferred=deferred,failed=failed)
    # RECON ingestion is independent of claims/EDI and uses the existing
    # client-specific CSV/PA7 importer. Scan once as part of this scheduled/manual
    # run, not via a second timer. Failures are visible in Processing History.
    try:
        recon = scan_recon_files(client, run=run)
        processed += recon['processed']
        skipped += recon['skipped']
        deferred += recon['deferred']
        failed += recon['failed']
    except Exception as exc:
        failed += 1
        _item(run,'recon',Path('RECON'), 'failed', message=str(exc)[:3000])
    FolderScanRun.objects.filter(pk=run.pk).update(
        processed=processed, skipped=skipped, deferred=deferred, failed=failed)
    # Reconciliation runs under the LAG lock so it cannot overlap claims import.
    # Re-read the flag after taking the lock to include uploads during the scan.
    with lag_upload_lock(client.pk, wait=True):
        schedule.refresh_from_db()
        if schedule.needs_reconcile:
            FolderScanRun.objects.filter(pk=run.pk).update(message='Indexing complete. Refreshing claim links…')
            reconcile_client(client)
            FolderScanSchedule.objects.filter(pk=schedule.pk).update(needs_reconcile=False,revision=F('revision')+1)
    message=f'Processed {processed} new or changed files.' if processed else ('No files processed successfully.' if failed or deferred else 'No new files found.')
    if deferred:message+=f' {deferred} files deferred until transfer completes.'
    if failed:message+=f' {failed} files or folders failed; review results and use Run now to retry.'
    FolderScanRun.objects.filter(pk=run.pk).update(status='partial' if failed else 'completed',processed=processed,skipped=skipped,deferred=deferred,failed=failed,message=message,finished_at=timezone.now())


def process_client(client, now=None):
    now=now or timezone.now()
    try:
        with client_processing_lock(client.pk):
            schedule,_=FolderScanSchedule.objects.get_or_create(client=client)
            FolderScanSchedule.objects.filter(pk=schedule.pk).update(worker_seen_at=now)
            # A prior process died if we can acquire its per-client advisory lock.
            interrupted=FolderScanRun.objects.filter(client=client,status='running')
            if interrupted.exists():
                interrupted.update(status='failed',message='Worker interrupted. A recovery scan has been queued.',finished_at=now)
                queue_scan(client,'recovery')
            with transaction.atomic():
                schedule=FolderScanSchedule.objects.select_for_update().get(pk=schedule.pk)
                if due_today(schedule,now):
                    queue_scan(client,'scheduled')
                    local=now.astimezone(ZoneInfo(schedule.timezone_name))
                    schedule.last_scheduled_date=local.date()
                    schedule.save(update_fields=['last_scheduled_date'])
                run=FolderScanRun.objects.filter(client=client,status='queued').order_by('id').first()
                if not run:
                    return False
                run.status='running';run.started_at=now;run.message='Checking configured folders…';run.save(update_fields=['status','started_at','message'])
            try:
                perform_scan(run,schedule)
            except Exception as exc:
                FolderScanRun.objects.filter(pk=run.pk).update(status='failed',message='Scan failed: '+str(exc)[:3000],finished_at=timezone.now())
            return True
    except ClientProcessingBusy:
        return False
