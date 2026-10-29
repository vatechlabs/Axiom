"""Separate folder poller for RECON. No linkage to Claim, EdiFile, or ImportBatch.

WGS samples have a CSV DCN column; Highmark .PA7 samples are 150-character
fixed-width records without a supplied layout. For PA7, the first 15 chars
are a provisional record identifier, NOT a validated claim-number mapping.
Every source row is preserved in raw_data to prevent information loss.
"""
import csv
import hashlib
import io
from datetime import datetime
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from .models import ClientFolderConfig, ReconFile, ReconRecord
from .rule_engine import rules_for, has_rules, rule_files

CSV_SUFFIXES={'.csv'}
PA7_SUFFIXES={'.pa7'}
SUPPORTED=CSV_SUFFIXES|PA7_SUFFIXES


def decode(blob):
    for enc in ('utf-8-sig', 'cp1252'):
        try:return blob.decode(enc)
        except UnicodeDecodeError:pass
    return blob.decode('latin-1')


def parse_date(value):
    if not value:return None
    value=str(value).strip()
    for fmt in ('%Y%m%d', '%Y-%m-%d', '%m/%d/%Y'):
        try:return datetime.strptime(value,fmt).date()
        except ValueError:pass
    return None


def parse_records(path):
    """Yield (row number, identifier, date, exact row content)."""
    text=decode(path.read_bytes())
    suffix=path.suffix.lower()
    if suffix == '.csv':
        reader=csv.DictReader(io.StringIO(text, newline=''))
        names=[(name or '').strip() for name in (reader.fieldnames or [])]
        if not names or 'DCN' not in names:
            raise ValueError('Unsupported RECON CSV: DCN column is required.')
        for number,raw in enumerate(reader,start=2):
            row={(k or '').strip():str(v or '').strip() for k,v in raw.items() if k is not None}
            claim=row.get('DCN','')
            if not claim:continue
            date=None  # LST-PROC-DT is processing date, NOT proven claim date
            yield number,claim,date,row
    elif suffix == '.pa7':
        for number,line in enumerate(text.splitlines(),start=1):
            if not line.strip():continue
            if len(line)!=150:
                raise ValueError(f'PA7 row {number} is {len(line)} characters; expected 150.')
            # Layout documentation required to interpret individual PA7 columns.
            yield number,line[:15].strip(),None,{'raw_line':line,'layout':'PA7_UNMAPPED','record_key_unverified':line[:15].strip()}
    else:
        raise ValueError('Unsupported RECON extension (supported: .csv, .PA7).')


def source_folders(client):
    cfg=ClientFolderConfig.objects.filter(client=client).first()
    if not cfg and not any(has_rules(client,k) for k in ('recon_abc','recon_musgrove','recon')):return []
    if client.code.upper()=='WGS':
        return [('MUSGROVE','recon_musgrove'),('ABC','recon_abc')]
    if client.code.upper()=='HIGHMARK':
        return [('', 'recon')]
    return []


def scan_recon_files(client):
    """Poll each enabled RECON folder rule; preserve legacy source fallback."""
    import os
    from django.conf import settings
    if not source_folders(client):
        return
    approved=Path(settings.EDI_ROOT).resolve()
    now=timezone.now().timestamp()
    seen_paths=set()
    for sub_client,folder_key in source_folders(client):
        rules=rules_for(client,folder_key) if has_rules(client,folder_key) else [None]
        for rule in rules:
            if rule:
                try:
                    sources=sorted(rule_files(rule))
                except (OSError,ValueError) as exc:
                    print(f'RECON folder rule {rule.id}: {exc}',flush=True)
                    continue
            else:
                cfg=ClientFolderConfig.objects.filter(client=client).first()
                field={'recon_abc':'windows_recon_abc_path',
                       'recon_musgrove':'windows_recon_musgrove_path',
                       'recon':'windows_recon_path'}[folder_key]
                original=getattr(cfg,field,'') if cfg else ''
                folder=(Path(original) if os.name=='nt' and original
                        else approved/client.code/folder_key).resolve()
                if not folder.is_dir():
                    continue
                sources=sorted(folder.rglob('*'))
            for source in sources:
                if source.is_symlink() or not source.is_file() or source.suffix.lower() not in SUPPORTED:
                    continue
                identity=(sub_client,str(source.resolve()).casefold())
                if identity in seen_paths:
                    continue
                seen_paths.add(identity)
                if client.code.upper()=='HIGHMARK' and not source.name.upper().startswith('RECON'):
                    continue
                try:
                    source=source.resolve()
                    if rule is None and not source.is_relative_to(folder):
                        continue
                    stat=source.stat()
                    if now-stat.st_mtime<60:
                        continue
                    current=ReconFile.objects.filter(client=client,sub_client=sub_client,path=str(source)).first()
                    if current and current.status=='indexed' and current.size==stat.st_size and current.modified_ns==stat.st_mtime_ns:
                        continue
                    payload=list(parse_records(source))
                    if len(payload)>300000:
                        raise ValueError('File exceeds 300,000 rows; split before importing.')
                    after=source.stat()
                    if (stat.st_size,stat.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                        continue
                    digest=hashlib.sha256(source.read_bytes()).hexdigest()
                    duplicate=ReconFile.objects.filter(client=client,sub_client=sub_client,
                        fingerprint=digest,status='indexed').exclude(path=str(source)).first()
                    if duplicate:
                        # Identical RECON CSV/PA7 moved into Archive: keep the
                        # existing ReconRecord rows, but repair its file path.
                        if not Path(duplicate.path).is_file():
                            with transaction.atomic():
                                occupied = ReconFile.objects.filter(
                                    client=client, sub_client=sub_client,
                                    path=str(source)
                                ).exclude(pk=duplicate.pk).exists()
                                if not occupied:
                                    duplicate.path = str(source)
                                    duplicate.filename = source.name
                                    duplicate.size = stat.st_size
                                    duplicate.modified_ns = stat.st_mtime_ns
                                    duplicate.save(update_fields=[
                                        'path', 'filename', 'size', 'modified_ns'
                                    ])
                        continue
                    with transaction.atomic():
                        rf,_=ReconFile.objects.select_for_update().get_or_create(
                            client=client,sub_client=sub_client,path=str(source),
                            defaults={'filename':source.name})
                        rf.records.all().delete()
                        rf.filename=source.name
                        rf.fingerprint=digest
                        rf.size=stat.st_size
                        rf.modified_ns=stat.st_mtime_ns
                        rf.status='indexed'
                        rf.error=''
                        rf.row_count=len(payload)
                        rf.indexed_at=timezone.now()
                        rf.save()
                        for start in range(0,len(payload),1000):
                            ReconRecord.objects.bulk_create([
                                ReconRecord(source=rf,row_number=n,claim_number=claim,claim_date=date,raw_data=raw)
                                for n,claim,date,raw in payload[start:start+1000]
                            ],batch_size=1000)
                except Exception as exc:
                    ReconFile.objects.update_or_create(client=client,sub_client=sub_client,path=str(source),defaults={
                        'filename':source.name,'status':'failed','error':str(exc)[:1500]})
