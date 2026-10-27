from django.contrib import admin
from .models import Client, ClientFolderConfig, Membership, Claim, ErrorHistory, ErrorObservation, ClaimNote, EdiDocument, EdiFile, EdiClaimIndex, ImportBatch

for cls in [Client, ClientFolderConfig, Membership, Claim, ErrorHistory, ClaimNote, EdiDocument, ImportBatch]:
    admin.site.register(cls)

@admin.register(ErrorObservation)
class ErrorObservationAdmin(admin.ModelAdmin):
    list_display=('client','dcn','claim_submitter_id','report_date','source_line')
    list_filter=('client','report_date')
    search_fields=('dcn','claim_submitter_id','message')
    readonly_fields=('client','batch','dcn','claim_submitter_id','report_date','message','reject_code','source_line')

@admin.register(EdiFile)
class EdiFileAdmin(admin.ModelAdmin):
    list_display=('client','kind','filename','bytes_size','indexed_at')
    list_filter=('client','kind')
    search_fields=('filename','path')

@admin.register(EdiClaimIndex)
class EdiClaimIndexAdmin(admin.ModelAdmin):
    list_display=('edi_file','claim_identifier','identifier_type','segment_number','start_byte')
    search_fields=('claim_identifier',)
