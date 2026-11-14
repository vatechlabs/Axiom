from datetime import timedelta
from django.db import migrations
from django.db.models import Count
from django.utils import timezone


def backfill(apps, schema_editor):
    Event = apps.get_model('claims', 'UploadEvent')
    Item = apps.get_model('claims', 'FolderScanItem')
    Batch = apps.get_model('claims', 'ImportBatch')
    Edi = apps.get_model('claims', 'EdiFile')
    Recon = apps.get_model('claims', 'ReconFile')
    db = schema_editor.connection.alias
    cutoff = timezone.now() - timedelta(days=30)

    def subtype(kind, path):
        text = str(path).upper()
        if kind == 'rec_mus' or 'MUSGROVE' in text or 'MUSGROW' in text:
            return 'MUSGROVE'
        if kind == 'rec_abc' or 'ABC' in text:
            return 'ABC'
        return ''

    def save(key, **values):
        Event.objects.using(db).get_or_create(source_key=key, defaults=values)

    for item in Item.objects.using(db).filter(run__created_at__gte=cutoff).select_related('run').iterator(chunk_size=500):
        save(f'scan:{item.pk}', client_id=item.run.client_id,
             occurred_at=item.run.started_at or item.run.created_at,
             filename=item.filename, kind=item.kind, sub_client=subtype(item.kind,item.path),
             trigger=item.run.trigger, status=item.status, record_count=item.indexed_count,
             message=(item.message+' [Historical scan time; exact per-file time was not recorded.]').strip(),
             edi_file_id=item.edi_file_id, batch_id=item.import_batch_id)
    for batch in Batch.objects.using(db).filter(source='lag',created_at__gte=cutoff).iterator(chunk_size=500):
        save(f'legacy-lag:{batch.pk}', client_id=batch.client_id, occurred_at=batch.created_at,
             filename=batch.filename, kind='lag', trigger='manual',
             status='uploaded' if batch.status=='processed' else batch.status,
             record_count=batch.processed_count, batch_id=batch.pk, message=batch.error)
    for edi in Edi.objects.using(db).filter(indexed_at__gte=cutoff).exclude(pk__in=Item.objects.using(db).exclude(edi_file_id=None).values('edi_file_id')).annotate(n=Count('claims_indexed')).iterator(chunk_size=500):
        save(f'legacy-edi:{edi.pk}',client_id=edi.client_id,occurred_at=edi.indexed_at,
             filename=edi.filename,kind=edi.kind,sub_client=subtype(edi.kind,edi.path),
             trigger='historical',status='indexed',record_count=edi.n,edi_file_id=edi.pk,
             message='Restored from existing file index; original scan source unavailable.')
    from django.db.models import OuterRef, Exists
    seen=Item.objects.using(db).filter(run__client_id=OuterRef('client_id'),path=OuterRef('path'),kind__in=('recon','rec_mus','rec_abc'))
    for recon in Recon.objects.using(db).filter(indexed_at__gte=cutoff).annotate(seen=Exists(seen)).filter(seen=False).iterator(chunk_size=500):
        kind={'MUSGROVE':'rec_mus','ABC':'rec_abc'}.get(recon.sub_client,'recon')
        save(f'legacy-recon:{recon.pk}',client_id=recon.client_id,occurred_at=recon.indexed_at,
             filename=recon.filename,kind=kind,sub_client=recon.sub_client,trigger='historical',
             status=recon.status,record_count=recon.row_count,message=recon.error)


class Migration(migrations.Migration):
    dependencies = [('claims', '0014_uploadevent')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
