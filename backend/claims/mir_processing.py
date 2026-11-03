"""Highmark MIR records and exact, unambiguous claim links."""
import hashlib
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from .models import Claim, EdiDocument, MirFile, MirRecord, FolderScanItem, FolderScanRun, FolderScanSchedule
from .rule_engine import rules_for, scan_rule_entries
from .smb_auth import connect_for_edi_path


def parse_mir(blob, received):
    """Positions are one-based bytes: identifier 3–19, returned errors 208–217."""
    prefix = b'MI' if received else b'MO'
    # Legacy Windows exports may end with a DOS EOF byte after the last row.
    # Strip only a terminal marker; an embedded marker remains a parse error.
    tail = blob.rstrip(b'\r\n')
    if tail.endswith(b'\x1a'):
        blob = tail[:-1]
    rows = []
    controls = 0
    for number, line in enumerate(blob.split(b'\n'), 1):
        line = line.rstrip(b'\r')
        if not line.strip():
            continue
        if not line.startswith(prefix):
            # Carrier files include binary control records containing NUL/0x9f.
            if b'\x00' in line or line.startswith(b'\x9f'):
                controls += 1
                continue
            raise ValueError(f'MIR row {number}: unexpected record prefix')
        if len(line) < (217 if received else 19):
            raise ValueError(f'MIR row {number}: truncated record')
        identifier = line[2:19].decode('ascii').strip()
        if not identifier or not identifier.isalnum():
            raise ValueError(f'MIR row {number}: invalid claim identifier')
        error = line[207:217].decode('cp1252').strip() if received else ''
        rows.append((number, identifier, error, line.decode('cp1252')))
    if not rows and not controls:
        raise ValueError('No MIR claim records found')
    return rows, controls


def source_date(path):
    match = re.fullmatch(r'MIROUT(?:\.D)?(\d{6})(?:\..+)', path.name, re.IGNORECASE)
    if not match:
        raise ValueError('MIR filename must contain its YYMMDD source date')
    return datetime.strptime(match.group(1), '%y%m%d').date()


def relink_mir(client):
    """Relink after EDI reconciliation and later LAG uploads."""
    if client.code.upper() not in ('HIGHMARK', 'HMRK', 'HM'):
        return
    candidates = defaultdict(set)
    for claim in Claim.objects.filter(client=client).only('id', 'claim_number', 'dcn', 'fund'):
        group = 'CPT' if claim.fund.strip().upper() == 'CPT' else 'ABC'
        for identifier in (claim.claim_number, claim.dcn):
            if identifier:
                candidates[(group, identifier.strip())].add(claim.pk)
    for doc in EdiDocument.objects.filter(claim__client=client, edi_index__isnull=False).select_related('edi_index'):
        group = 'CPT' if doc.kind == '835' else 'ABC'
        candidates[(group, doc.edi_index.claim_identifier.strip())].add(doc.claim_id)
    pending = []
    for record in MirRecord.objects.filter(source__client=client).select_related('source').iterator(chunk_size=1000):
        matches = candidates.get((record.source.sub_client, record.claim_identifier), set())
        claim_id = next(iter(matches)) if len(matches) == 1 else None
        if record.claim_id != claim_id:
            record.claim_id = claim_id
            pending.append(record)
        if len(pending) >= 1000:
            MirRecord.objects.bulk_update(pending, ['claim'], batch_size=1000)
            pending = []
    if pending:
        MirRecord.objects.bulk_update(pending, ['claim'], batch_size=1000)


def scan_mir_files(client, run):
    totals = dict(processed=0, skipped=0, deferred=0, failed=0)
    if client.code.upper() not in ('HIGHMARK', 'HMRK', 'HM'):
        return totals
    for source_type in ('mir_sent', 'mir_received', 'mir_cpt'):
        kind = {'mir_sent': 'mir_sent', 'mir_received': 'mir_recv', 'mir_cpt': 'mir_cpt'}[source_type]
        for rule in rules_for(client, source_type):
            folder = Path(rule.folder_path)
            FolderScanRun.objects.filter(pk=run.pk).update(message=f'Scanning {source_type} files...')
            try:
                connect_for_edi_path(client, folder)
                for path, stat in scan_rule_entries(rule):
                    FolderScanSchedule.objects.filter(client=client).update(worker_seen_at=timezone.now())
                    try:
                        if timezone.now().timestamp() - stat.st_mtime < 60:
                            status, count, message = 'deferred', 0, 'File is still settling'
                        else:
                            blob = path.read_bytes()
                            after = path.stat()
                            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                                raise ValueError('File changed while reading; retry next scan')
                            fingerprint = hashlib.sha256(blob).hexdigest()
                            key = dict(client=client, source_type=source_type,
                                       path_hash=hashlib.sha256(str(path).casefold().encode()).hexdigest())
                            existing = MirFile.objects.filter(**key).first()
                            if existing and existing.fingerprint == fingerprint:
                                status, count, message = 'unchanged', existing.records.count(), ''
                            else:
                                rows, controls = parse_mir(blob, source_type == 'mir_received')
                                group = 'CPT' if source_type == 'mir_cpt' or 'cpt_teamsters' in str(path).casefold() else 'ABC'
                                with transaction.atomic():
                                    source, _ = MirFile.objects.update_or_create(**key, defaults=dict(
                                        path=str(path), filename=path.name, fingerprint=fingerprint,
                                        sub_client=group, source_date=source_date(path), indexed_at=timezone.now()))
                                    source.records.all().delete()
                                    MirRecord.objects.bulk_create([
                                        MirRecord(source=source, row_number=n, claim_identifier=identifier,
                                                  error_text=error, raw_line=line)
                                        for n, identifier, error, line in rows
                                    ], batch_size=1000)
                                status, count = 'indexed', len(rows)
                                message = f'{controls} carrier control records skipped' if controls else ''
                        totals[{'indexed': 'processed', 'unchanged': 'skipped', 'deferred': 'deferred'}[status]] += 1
                        FolderScanItem.objects.create(run=run, kind=kind, path=str(path), filename=path.name[:255],
                                                      status=status, indexed_count=count, message=message)
                    except Exception as exc:
                        totals['failed'] += 1
                        FolderScanItem.objects.create(run=run, kind=kind, path=str(path), filename=path.name[:255],
                                                      status='failed', message=str(exc)[:3000])
            except Exception as exc:
                totals['failed'] += 1
                FolderScanItem.objects.create(run=run, kind=kind, path=str(folder), filename=folder.name[:255],
                                              status='failed', message=str(exc)[:3000])
    relink_mir(client)
    return totals
