"""Record outcomes without retaining credentials or uploaded file contents."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import FolderScanItem, UploadEvent


def sub_client(kind, path):
    text = str(path).upper()
    if kind == 'rec_mus' or 'MUSGROVE' in text or 'MUSGROW' in text:
        return 'MUSGROVE'
    if kind == 'rec_abc' or 'ABC' in text:
        return 'ABC'
    return ''


@receiver(post_save, sender=FolderScanItem, dispatch_uid='axiom_upload_outcome')
def record_scan_item(sender, instance, created, **kwargs):
    if not created:
        return
    run = instance.run
    UploadEvent.objects.get_or_create(source_key=f'scan:{instance.pk}', defaults={
        'client_id': run.client_id, 'filename': instance.filename,
        'kind': instance.kind, 'sub_client': sub_client(instance.kind, instance.path),
        'trigger': run.trigger, 'status': instance.status,
        'record_count': instance.indexed_count, 'message': instance.message,
        'edi_file_id': instance.edi_file_id, 'batch_id': instance.import_batch_id,
    })


def record_lag_attempt(request, response, key, batch=None):
    from .permissions import allowed_client
    upload = request.FILES.get('file')
    data = response.data
    UploadEvent.objects.create(
        source_key=key, client=allowed_client(request),
        filename=(upload.name if upload else '(no workbook)')[:255], kind='lag',
        trigger='manual', status=('duplicate' if data.get('duplicate') else 'uploaded') if response.status_code < 400 else 'failed',
        record_count=batch.processed_count if batch else 0, batch=batch,
        message=str(data.get('detail', ''))[:3000],
    )
