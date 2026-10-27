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
from .importers import index_edi, import_errors, reconcile_client
from .processing_locks import client_processing_lock, ClientProcessingBusy


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
    for key,kind in [('error_reports','error'),('835','835'),('837','837')]:
        folder=Path(paths[key]).resolve()
        try:
            if not any(folder.is_relative_to(root) for root in roots) or not folder.is_dir():
                raise ValueError('Folder missing, inaccessible, or outside approved roots')
            candidates=sorted(folder.rglob('*'))
        except Exception as exc:
            _item(run,kind,folder,'failed',message=str(exc));failed+=1;continue
        for path in candidates:
            if path.name.startswith('.'):
                continue
            suffix=path.suffix.lower()
            if kind=='error' and suffix!='.txt':
                continue
            if kind!='error' and suffix not in ('.edi','.'+kind,'.txt'):
                continue
            try:
                path=path.resolve()
                if not any(path.is_relative_to(root) for root in roots):
                    raise ValueError('File is outside approved roots')
                if not path.is_file():
                    continue
                before=path.stat()
                # Ignore transfers still settling; the next scheduled/manual run retries.
                if timezone.now().timestamp()-before.st_mtime<60:
                    _item(run,kind,path,'deferred',message='Recently modified; waiting for transfer to finish. Run again after 60 seconds.');deferred+=1;continue
                digest=hashlib.sha256(str(path).encode('utf-8')).hexdigest()
                state=FolderFileState.objects.filter(client=client,kind=kind,path_hash=digest).first()
                if state and (state.bytes_size,state.modified_ns)==(before.st_size,before.st_mtime_ns):
                    skipped+=1;continue
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
