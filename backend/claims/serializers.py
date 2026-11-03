from rest_framework import serializers
from .models import Client,Claim,ClaimNote,ErrorHistory,EdiDocument,MirRecord
class ClientSerializer(serializers.ModelSerializer):
 class Meta: model=Client;fields=['id','code','name']
class ErrorHistorySerializer(serializers.ModelSerializer):
 class Meta: model=ErrorHistory;fields=['id','report_date','message']
class ClaimNoteSerializer(serializers.ModelSerializer):
 author_name=serializers.CharField(source='author.username',read_only=True)
 class Meta: model=ClaimNote;fields=['id','author_name','body','created_at']
class EdiSerializer(serializers.ModelSerializer):
 source_index=serializers.IntegerField(source='edi_index.segment_number',read_only=True,allow_null=True)
 source_index_id=serializers.IntegerField(source='edi_index_id',read_only=True)
 class Meta: model=EdiDocument;fields=['id','kind','original_name','created_at','source_index','source_index_id']
class ClaimSerializer(serializers.ModelSerializer):
 edi_835_count=serializers.SerializerMethodField()
 edi_837_count=serializers.SerializerMethodField()
 def get_edi_835_count(self,obj):return getattr(obj,'edi_835_count',None) if getattr(obj,'edi_835_count',None) is not None else obj.edi_documents.filter(kind='835').count()
 def get_edi_837_count(self,obj):return getattr(obj,'edi_837_count',None) if getattr(obj,'edi_837_count',None) is not None else obj.edi_documents.filter(kind='837').count()
 history_count=serializers.IntegerField(source='error_history.count',read_only=True)
 claim_submitter_id=serializers.CharField(source='claim_number',read_only=True)
 class Meta: model=Claim;fields=['id','claim_number','claim_submitter_id','dcn','fund','tpa','tpa_age','total_age','report_date','source_sheet','error_message','amount','history_count','edi_835_count','edi_837_count']
class MirRecordSerializer(serializers.ModelSerializer):
 filename=serializers.CharField(source='source.filename',read_only=True)
 source_type=serializers.CharField(source='source.source_type',read_only=True)
 sub_client=serializers.CharField(source='source.sub_client',read_only=True)
 source_date=serializers.DateField(source='source.source_date',read_only=True)
 class Meta:
  model=MirRecord
  fields=['id','filename','source_type','sub_client','source_date','row_number','claim_identifier','error_text','raw_line']

class ClaimDetailSerializer(ClaimSerializer):
 mir_records=serializers.SerializerMethodField()
 def get_mir_records(self,obj):
  return MirRecordSerializer(obj.mir_records.select_related('source').order_by('-source__source_date','-id'),many=True).data
 error_history=ErrorHistorySerializer(many=True,read_only=True)
 notes=ClaimNoteSerializer(many=True,read_only=True)
 edi_documents=EdiSerializer(many=True,read_only=True)
 class Meta(ClaimSerializer.Meta):fields=ClaimSerializer.Meta.fields+['error_history','notes','edi_documents','mir_records']
