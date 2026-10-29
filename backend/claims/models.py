from datetime import time as datetime_time
from django.conf import settings
from django.db import models
from django.utils import timezone

class Client(models.Model):
    code=models.CharField(max_length=16,unique=True)
    name=models.CharField(max_length=150)
    def __str__(self): return self.code

class ClientFolderConfig(models.Model):
    client = models.OneToOneField(Client, on_delete=models.CASCADE, related_name='folder_config')
    error_reports_path = models.TextField(blank=True)
    edi_835_path = models.TextField(blank=True)
    edi_837_path = models.TextField(blank=True)

    # Original Windows folder paths configured through Axiom Settings
    windows_error_reports_path = models.TextField(blank=True)
    windows_835_path = models.TextField(blank=True)
    windows_837_path = models.TextField(blank=True)
    windows_recon_path = models.TextField(blank=True)
    recon_path = models.TextField(blank=True)
    windows_recon_musgrove_path = models.TextField(blank=True)
    windows_recon_abc_path = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.client.code} folder configuration'

class Membership(models.Model):
    user=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,related_name='client_memberships')
    client=models.ForeignKey(Client,on_delete=models.CASCADE,related_name='members')
    role=models.CharField(max_length=20,default='analyst',choices=[('admin','Admin'),('analyst','Analyst'),('viewer','Viewer')])
    class Meta: constraints=[models.UniqueConstraint(fields=['user','client'],name='unique_user_client')]

class ImportBatch(models.Model):
    client=models.ForeignKey(Client,on_delete=models.PROTECT)
    source=models.CharField(max_length=12,choices=[('lag','LAG'),('error','Error report'),('835','835'),('837','837')])
    filename=models.CharField(max_length=255)
    original_file=models.BinaryField(null=True,blank=True) # only uploaded LAG workbook; never store entire 835/837
    report_date=models.DateField(null=True,blank=True)
    file_path=models.TextField(blank=True) # folder-indexed reports
    fingerprint=models.CharField(max_length=64,blank=True,db_index=True)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(default=timezone.now)
    processed_count=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=30,default='processing')
    error=models.TextField(blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['client','source','fingerprint'],condition=~models.Q(fingerprint=''),name='unique_source_fingerprint')]

class Claim(models.Model):
    client=models.ForeignKey(Client,on_delete=models.PROTECT,related_name='claims')
    claim_number=models.CharField(max_length=80) # claim submitter id, or fallback DCN
    dcn=models.CharField(max_length=80,blank=True,db_index=True)
    fund=models.CharField(max_length=150,blank=True)
    tpa=models.CharField(max_length=100,blank=True)
    tpa_age=models.PositiveIntegerField(default=0)
    total_age=models.PositiveIntegerField(default=0)
    report_date=models.DateField()
    source_sheet=models.CharField(max_length=32,blank=True)
    lag_batch=models.ForeignKey(ImportBatch,null=True,blank=True,on_delete=models.SET_NULL)
    error_message=models.TextField(blank=True)
    amount=models.DecimalField(max_digits=13,decimal_places=2,default=0)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['client','claim_number'],name='unique_client_claim')]
        indexes=[models.Index(fields=['client','report_date']),models.Index(fields=['client','tpa_age']),models.Index(fields=['client','total_age']),models.Index(fields=['client','dcn'])]

class LagClaimSnapshot(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE)
    batch=models.ForeignKey(ImportBatch,on_delete=models.CASCADE,related_name='snapshots')
    claim=models.ForeignKey(Claim,on_delete=models.CASCADE)
    row_number=models.PositiveIntegerField()
    sheet_name=models.CharField(max_length=32)
    row_data=models.JSONField(default=dict)
    class Meta: constraints=[models.UniqueConstraint(fields=['batch','sheet_name','row_number'],name='unique_lag_source_row')]

class ErrorObservation(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE)
    batch=models.ForeignKey(ImportBatch,on_delete=models.CASCADE)
    dcn=models.CharField(max_length=80,blank=True,db_index=True)
    claim_submitter_id=models.CharField(max_length=80,blank=True,db_index=True)
    report_date=models.DateField()
    message=models.TextField()
    reject_code=models.CharField(max_length=32,blank=True)
    source_line=models.PositiveIntegerField()
    class Meta:
        constraints=[models.UniqueConstraint(fields=['batch','source_line'],name='unique_error_source_line')]
        indexes=[models.Index(fields=['client','dcn','report_date']),models.Index(fields=['client','claim_submitter_id','report_date'])]

class ErrorHistory(models.Model):
    claim=models.ForeignKey(Claim,on_delete=models.CASCADE,related_name='error_history')
    observation=models.ForeignKey(ErrorObservation,null=True,blank=True,on_delete=models.SET_NULL)
    report_date=models.DateField()
    message=models.TextField(blank=True)
    class Meta:
        ordering=['-report_date','-id']
        indexes=[models.Index(fields=['claim','-report_date','-id'],name='err_claim_date_latest')]
        constraints=[models.UniqueConstraint(fields=['claim','observation'],condition=models.Q(observation__isnull=False),name='unique_claim_observation')]

class ClaimNote(models.Model):
    claim=models.ForeignKey(Claim,on_delete=models.CASCADE,related_name='notes')
    author=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    body=models.TextField()
    created_at=models.DateTimeField(default=timezone.now)
    class Meta: ordering=['-created_at']

class EdiFile(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE)
    kind=models.CharField(max_length=3,choices=[('835','835'),('837','837')])
    path=models.TextField()
    filename=models.CharField(max_length=255)
    fingerprint=models.CharField(max_length=64)
    bytes_size=models.BigIntegerField()
    modified_ns=models.BigIntegerField()
    indexed_at=models.DateTimeField(default=timezone.now)
    class Meta: constraints=[models.UniqueConstraint(fields=['client','kind','path'],name='unique_client_edi_file')]

class EdiDocument(models.Model):
    # indexed relationship only: no file contents in database
    claim=models.ForeignKey(Claim,on_delete=models.CASCADE,related_name='edi_documents')
    edi_file=models.ForeignKey(EdiFile,on_delete=models.CASCADE,related_name='documents')
    edi_index=models.ForeignKey('EdiClaimIndex',null=True,blank=True,on_delete=models.CASCADE,related_name='linked_documents')
    kind=models.CharField(max_length=3)
    original_name=models.CharField(max_length=255)
    created_at=models.DateTimeField(default=timezone.now)
    class Meta:
        ordering=['-created_at']
        indexes=[models.Index(fields=['claim','kind'],name='edi_claim_kind_count')]
        constraints=[models.UniqueConstraint(fields=['claim','edi_index'],condition=models.Q(edi_index__isnull=False),name='unique_claim_edi_occurrence')]

class EdiClaimIndex(models.Model):
    edi_file=models.ForeignKey(EdiFile,on_delete=models.CASCADE,related_name='claims_indexed')
    claim_identifier=models.CharField(max_length=80,db_index=True)
    identifier_type=models.CharField(max_length=32,default='submitter')
    segment_number=models.PositiveIntegerField(default=0)
    start_byte=models.BigIntegerField(null=True,blank=True)
    class Meta:
        indexes=[models.Index(fields=['claim_identifier','identifier_type'])]
        constraints=[models.UniqueConstraint(fields=['edi_file','claim_identifier','identifier_type','segment_number'],name='unique_edi_claim_occurrence')]

class FolderScanSchedule(models.Model):
    client=models.OneToOneField(Client,on_delete=models.CASCADE,related_name='scan_schedule')
    enabled=models.BooleanField(default=False)
    daily_time=models.TimeField(default=datetime_time(18,0))
    weekdays=models.JSONField(default=list)
    timezone_name=models.CharField(max_length=64,default='Asia/Kolkata')
    last_scheduled_date=models.DateField(null=True,blank=True)
    worker_seen_at=models.DateTimeField(null=True,blank=True)
    revision=models.PositiveBigIntegerField(default=0)
    needs_reconcile=models.BooleanField(default=False)

class FolderScanRun(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE)
    trigger=models.CharField(max_length=16,default='manual')
    status=models.CharField(max_length=16,default='queued')
    created_at=models.DateTimeField(default=timezone.now)
    started_at=models.DateTimeField(null=True,blank=True)
    finished_at=models.DateTimeField(null=True,blank=True)
    processed=models.PositiveIntegerField(default=0)
    skipped=models.PositiveIntegerField(default=0)
    deferred=models.PositiveIntegerField(default=0)
    failed=models.PositiveIntegerField(default=0)
    message=models.TextField(blank=True)
    class Meta:
        ordering=['-id']
        constraints=[models.UniqueConstraint(fields=['client'],condition=models.Q(status__in=['queued','running']),name='one_active_folder_scan')]

class FolderScanItem(models.Model):
    run=models.ForeignKey(FolderScanRun,on_delete=models.CASCADE,related_name='items')
    kind=models.CharField(max_length=8)
    filename=models.CharField(max_length=255)
    path=models.TextField(blank=True)
    status=models.CharField(max_length=16)
    indexed_count=models.PositiveIntegerField(default=0)
    message=models.TextField(blank=True)
    edi_file=models.ForeignKey(EdiFile,null=True,blank=True,on_delete=models.SET_NULL)
    import_batch=models.ForeignKey(ImportBatch,null=True,blank=True,on_delete=models.SET_NULL)
    class Meta:
        ordering=['id']

class FolderFileState(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE)
    kind=models.CharField(max_length=8)
    path_hash=models.CharField(max_length=64)
    bytes_size=models.BigIntegerField()
    modified_ns=models.BigIntegerField()
    class Meta:
        constraints=[models.UniqueConstraint(fields=['client','kind','path_hash'],name='unique_folder_file_state')]


class ReconFile(models.Model):
    """One independent RECON source file; no LAG/EDI claim linkage."""
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='recon_files')
    sub_client = models.CharField(max_length=24, default='', blank=True)
    path = models.TextField()
    filename = models.CharField(max_length=255)
    fingerprint = models.CharField(max_length=64, blank=True)
    size = models.BigIntegerField(default=0)
    modified_ns = models.BigIntegerField(default=0)
    status = models.CharField(max_length=20, default='pending')
    row_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    indexed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['client', 'sub_client', 'path'], name='uniq_recon_file_location')]
        indexes = [models.Index(fields=['client','sub_client','indexed_at'], name='recon_client_sub_dt_idx')]


class ReconRecord(models.Model):
    """Exact RECON row values saved separately. Raw data retained for audit."""
    source = models.ForeignKey(ReconFile, on_delete=models.CASCADE, related_name='records')
    row_number = models.PositiveIntegerField()
    claim_number = models.CharField(max_length=100, db_index=True, blank=True)
    claim_date = models.DateField(null=True, blank=True)
    raw_data = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['source','row_number'], name='uniq_recon_file_row')]
        indexes = [models.Index(fields=['claim_date','claim_number'], name='recon_date_claim_idx')]


class FileSourceRule(models.Model):
    client=models.ForeignKey(Client,on_delete=models.CASCADE,related_name='file_source_rules')
    file_type=models.CharField(max_length=30)
    folder_path=models.TextField()
    starts_with=models.CharField(max_length=500,blank=True)
    ends_with=models.CharField(max_length=500,blank=True)
    extensions=models.CharField(max_length=500,blank=True)
    recursive=models.BooleanField(default=False)
    include_subfolders=models.TextField(blank=True)
    exclude_subfolders=models.TextField(blank=True)
    enabled=models.BooleanField(default=True)
    class Meta:
        indexes=[models.Index(fields=['client','file_type','enabled'],name='file_rule_client_idx')]

class SmbCredential(models.Model):
    """Encrypted share credentials. The encryption key is stored outside the DB."""
    client=models.OneToOneField(Client,on_delete=models.CASCADE,related_name='smb_credential')
    share_path=models.CharField(max_length=255)
    username=models.CharField(max_length=255)
    password_ciphertext=models.BinaryField()
    updated_at=models.DateTimeField(auto_now=True)
