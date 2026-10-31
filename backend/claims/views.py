import csv,io
from collections import Counter
from datetime import date
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.db.models import Q,Count,Prefetch
from django.http import FileResponse,HttpResponse
from django.shortcuts import get_object_or_404
from openpyxl import Workbook,load_workbook
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view,permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from .models import Claim,ClaimNote,ErrorHistory,EdiDocument,ImportBatch,Membership,ClientFolderConfig
from .serializers import ClientSerializer,ClaimSerializer,ClaimDetailSerializer,ClaimNoteSerializer
from .querying import snapshot_query, highmark_snapshot_query, filter_query, rows_for
from .permissions import allowed_client,can_edit
from .app_roles import role_name,is_demo,is_operator
from .importers import import_lag,parse_lag
from .folder_paths import client_paths, validate_path, allowed_roots

def _available_clients(user):
 from .models import Client
 if is_demo(user) or is_operator(user):
  return ClientSerializer(Client.objects.all().order_by('code'),many=True).data
 return ClientSerializer([m.client for m in Membership.objects.select_related('client').filter(user=user)],many=True).data

@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
 u=authenticate(username=request.data.get('username',''),password=request.data.get('password',''))
 if not u:return Response({'detail':'Invalid credentials'},status=400)
 token,_=Token.objects.get_or_create(user=u)
 return Response({'token':token.key,'username':u.username,'role':role_name(u),'clients':_available_clients(u)})

@api_view(['POST'])
def logout(request):
 if request.auth:request.auth.delete()
 return Response({'ok':True})

@api_view(['GET'])
def me(request):
 return Response({'username':request.user.username,'role':role_name(request.user),'clients':_available_clients(request.user)})

@api_view(['POST'])
def change_password(request):
 if is_demo(request.user):return Response({'detail':'Demo credentials cannot be changed.'},status=403)
 u=request.user
 if not u.check_password(request.data.get('current_password','')):return Response({'detail':'Current password is incorrect'},status=400)
 if request.data.get('new_password')!=request.data.get('confirm_password'):return Response({'detail':'Passwords do not match'},status=400)
 try:validate_password(request.data.get('new_password',''),u)
 except Exception as exc:return Response({'detail':list(exc.messages) if hasattr(exc,'messages') else str(exc)},status=400)
 u.set_password(request.data['new_password']);u.save()
 Token.objects.filter(user=u).delete()
 return Response({'ok':True,'detail':'Password changed; sign in again.'})

# A daily LAG is an immutable batch of snapshot rows, not the mutable Claim table.
# Older imports retain col_N fields; the known ABC/WGS workbook uses A/B/E/I.
def snapshot_values(snapshot, report_date):
 raw=snapshot.row_data or {}
 v=raw.get('_snapshot') or {}
 def number(key):
  try:return int(float(raw.get(key) or 0))
  except (ValueError,TypeError):return 0
 return {'id':snapshot.claim_id,'claim_number':str(v.get('claim_number') or raw.get('col_2') or ''),
         'dcn':str(v.get('dcn') or ''),'fund':str(v.get('fund') or raw.get('col_9') or ''),
         'tpa':str(v.get('tpa') or ''),'tpa_age':int(v['tpa_age']) if 'tpa_age' in v else number('col_5'),
         'total_age':int(v['total_age']) if 'total_age' in v else number('col_1'),
         'source_sheet':snapshot.sheet_name,'report_date':str(report_date),
         'amount':str(raw.get('col_15') or '0')}

def is_highmark(client):
 return client.code.upper() in ('HIGHMARK','HMRK','HM')

def active_rows(request,client,batch):
 if is_highmark(client):
  raw=request.query_params.get('lag_date','')
  try: asof=date.fromisoformat(raw) if raw else None
  except ValueError:raise ValueError('lag_date must be YYYY-MM-DD')
  return highmark_snapshot_query(client,asof)
 return snapshot_query(client,batch)

def lag_batches(client):
 return ImportBatch.objects.defer('original_file').filter(client=client,source='lag',status='processed',report_date__isnull=False).order_by('-report_date','-created_at','-id')

def selected_batch(request,client):
 batches=lag_batches(client)
 date_value=request.query_params.get('lag_date')
 if date_value:
  try:desired=date.fromisoformat(date_value)
  except ValueError:raise ValueError('lag_date must be YYYY-MM-DD')
  return batches.filter(report_date=desired).first()
 return batches.first()

def snapshot_rows(request,client,batch):
 if not batch:return []
 return rows_for(filter_query(active_rows(request,client,batch),request.query_params),batch.report_date)

@api_view(['GET'])
def lag_dates(request):
 client=allowed_client(request)
 batches=lag_batches(client)
 return Response({'dates':list(dict.fromkeys(str(d) for d in batches.values_list('report_date',flat=True))),
                  'latest_date':str(batches.first().report_date) if batches.exists() else None,
                  'batches':[{'id':b.id,'report_date':str(b.report_date),'filename':b.filename,'created_at':b.created_at} for b in batches[:120]]})

@api_view(['GET'])
def claims(request):
 client=allowed_client(request)
 try:batch=selected_batch(request,client)
 except ValueError as e:return Response({'detail':str(e)},status=400)
 qs=filter_query(active_rows(request,client,batch),request.query_params) if batch else None
 try:
  page=int(request.query_params.get('page','1'))
  page_size=int(request.query_params.get('page_size','100'))
 except (TypeError,ValueError):return Response({'detail':'page and page_size must be integers'},status=400)
 if page<1 or page_size<1 or page_size>200:return Response({'detail':'page must be positive; page_size must be 1-200'},status=400)
 total=qs.count() if qs is not None else 0
 start=(page-1)*page_size
 rows=rows_for(qs[start:start+page_size],batch.report_date) if batch else []
 return Response({'count':total,'page':page,'page_size':page_size,'total_pages':(total+page_size-1)//page_size,'results':rows})

@api_view(['GET'])
def dashboard(request):
 client=allowed_client(request)
 try:batch=selected_batch(request,client)
 except ValueError as e:return Response({'detail':str(e)},status=400)
 if not batch:
  return Response({'lag_date':None,'lag_batch_id':None,'total':0,'over_60':0,'with_errors':0,'top_errors':[],'fund_counts':[],'filter_options':{'funds':[],'errors':[]},'attention':[]})
 base=active_rows(request,client,batch)
 qs=filter_query(base,request.query_params)
 totals=qs.aggregate(total=Count('id'),over_60=Count('id',filter=Q(s_total_age__gt=60)),with_errors=Count('id',filter=~Q(s_error='')))
 funds=list(qs.order_by().exclude(s_fund='').values('s_fund').annotate(n=Count('id')).order_by('-n','s_fund'))
 errors=list(qs.order_by().exclude(s_error='').values('s_error').annotate(n=Count('id')).order_by('-n','s_error'))
 return Response({'lag_date':str(batch.report_date),'lag_batch_id':batch.id,**totals,
                  'top_errors':[{'error_message':x['s_error'],'count':x['n']} for x in errors[:5]],
                  'fund_counts':[{'fund':x['s_fund'],'count':x['n']} for x in funds[:5]],
                  'filter_options':{'funds':sorted(x['s_fund'] for x in funds),'errors':sorted(x['s_error'] for x in errors)},
                  'attention':rows_for(qs[:6],batch.report_date)})

@api_view(['GET'])
def claim_detail(request,pk):
 client=allowed_client(request)
 c=get_object_or_404(Claim.objects.prefetch_related(Prefetch('notes',queryset=ClaimNote.objects.select_related('author')), 'error_history', Prefetch('edi_documents',queryset=EdiDocument.objects.select_related('edi_index'))),pk=pk,client=client)
 return Response(ClaimDetailSerializer(c).data)

@api_view(['POST'])
def add_note(request,pk):
 client=allowed_client(request);can_edit(request,client)
 c=get_object_or_404(Claim,pk=pk,client=client)
 body=str(request.data.get('body','')).strip()
 if not body or len(body)>5000:return Response({'detail':'Note must be 1–5000 characters.'},status=400)
 note=ClaimNote.objects.create(claim=c,author=request.user,body=body)
 return Response(ClaimNoteSerializer(note).data,status=201)

@api_view(['GET'])
def history(request,pk):
 client=allowed_client(request);c=get_object_or_404(Claim,pk=pk,client=client)
 q=c.error_history.all()
 if request.query_params.get('lag_date') and request.query_params.get('include_later')!='1':
  try:dt=date.fromisoformat(request.query_params['lag_date'])
  except ValueError:return Response({'detail':'Invalid lag_date'},status=400)
  q=q.filter(report_date__lte=dt)
 return Response(list(q.values('report_date','message')))

@api_view(['GET'])
def edi_download(request,pk,doc_id):
 client=allowed_client(request)
 c=get_object_or_404(Claim,pk=pk,client=client)
 doc=get_object_or_404(EdiDocument.objects.select_related('edi_file','edi_index'),pk=doc_id,claim=c,edi_file__client=client)
 from pathlib import Path
 from .edi_excerpt import extract_claim_excerpt,extract_transaction
 from .smb_auth import connect_for_edi_path
 try:
  connect_for_edi_path(client,doc.edi_file.path)
 except (OSError,RuntimeError):
  return Response({'detail':'EDI network share unavailable; check server credentials.'},status=503)
 path=Path(doc.edi_file.path).resolve()
 if not any(path.is_relative_to(root) for root in allowed_roots()):
  from .models import FileSourceRule
  from .smb_auth import DEFAULT_SHARE
  candidate=str(path).casefold()
  allowed_unc=(client.code.upper()=='WGS' and candidate.startswith(DEFAULT_SHARE.casefold()+chr(92)) and
               any(candidate.startswith(str(Path(r.folder_path)).casefold().rstrip(chr(92))+chr(92))
                   for r in FileSourceRule.objects.filter(client=client,file_type=doc.kind,enabled=True)))
  if not allowed_unc:return Response({'detail':'EDI path not permitted'},status=403)
 if not path.is_file():
  return Response({'detail':'Indexed EDI file is no longer accessible'},status=404)
 if doc.edi_index_id is None or doc.edi_index.start_byte is None:
  return Response({'detail':'Legacy EDI reference requires reindexing the source file'},status=409)
 st=path.stat()
 if st.st_size!=doc.edi_file.bytes_size or st.st_mtime_ns!=doc.edi_file.modified_ns:
  return Response({'detail':'Source file changed; please reindex before viewing'},status=409)
 mode=request.query_params.get('mode','claim')
 if mode not in ('claim','transaction','original'):
  return Response({'detail':'Unknown EDI view mode'},status=400)
 if mode=='original':
  # FileResponse streams the original directly; never load a huge file in RAM.
  return FileResponse(path.open('rb'),as_attachment=True,
                      filename=doc.edi_file.filename,content_type='application/octet-stream')
 try:
  extracted=(extract_transaction(path,doc.edi_index,doc.kind) if mode=='transaction'
             else extract_claim_excerpt(path,doc.edi_index,doc.kind))
 except (OSError,ValueError) as exc:
  return Response({'detail':str(exc)},status=409)
 suffix=f'{doc.id}'
 return FileResponse(io.BytesIO(extracted),as_attachment=request.query_params.get('download','0')=='1',
                     filename=f'{c.claim_number}_{doc.kind}_{suffix}_{mode}.txt',
                     content_type='text/plain; charset=utf-8')

@api_view(['GET'])
def export_claims(request):
 client=allowed_client(request)
 try:batch=selected_batch(request,client)
 except ValueError as e:return Response({'detail':str(e)},status=400)
 rows=snapshot_rows(request,client,batch)
 ids=request.query_params.get('ids','')
 if ids:
  selected={int(x) for x in ids.split(',') if x.strip().isdigit()}
  rows=[r for r in rows if r['id'] in selected]
 wb=Workbook();ws=wb.active;ws.title='Claims'
 columns=['claim_number','dcn','fund','tpa','tpa_age','total_age','error_message','report_date','source_sheet','amount']
 ws.append(['Claim Number','DCN','Fund','TPA','TPA Age','Total Age','Error Message','Report Date','Source Sheet','Amount'])
 for r in rows:ws.append([r.get(k,'') for k in columns])
 stream=io.BytesIO();wb.save(stream);stream.seek(0)
 return FileResponse(stream,as_attachment=True,filename='claims.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@api_view(['POST'])
def upload_lag(request):
 client=allowed_client(request);can_edit(request,client)
 uploaded=request.FILES.get('file')
 if not uploaded:return Response({'detail':'Attach an Excel workbook'},status=400)
 if not uploaded.name.lower().endswith('.xlsx'):return Response({'detail':'Only .xlsx accepted'},status=400)
 if uploaded.size>30*1024*1024:return Response({'detail':'Workbook exceeds 30 MB'},status=400)
 from datetime import datetime
 from django.utils import timezone
 date_text=request.data.get('report_date','')
 if date_text:
  try:rd=date.fromisoformat(date_text)
  except ValueError:return Response({'detail':'report_date must be YYYY-MM-DD'},status=400)
 else:
  rd=timezone.localdate()
 password=request.data.get('password','')
 try:
  batch,duplicate=import_lag(client,uploaded.read(),uploaded.name,rd,password,request.user)
 except ValueError as exc:return Response({'detail':str(exc)},status=400)
 except Exception:
  import logging
  logging.getLogger(__name__).exception('LAG import error')
  return Response({'detail':'Import failed. See server logs.'},status=500)
 return Response({'processed':batch.processed_count,'duplicate':duplicate,'batch_id':batch.pk,'report_date':str(batch.report_date),'detail':('Duplicate Highmark LAG report: already uploaded; no records changed.' if duplicate and is_highmark(client) else 'LAG report indexed.')},status=200 if duplicate else 201)

@api_view(['GET','PUT'])
def highmark_fund_settings(request):
 client=allowed_client(request)
 if not is_highmark(client):return Response({'detail':'Highmark only'},status=404)
 from .models import HighmarkFundMap
 from .importers import DEFAULT_HM_FUNDS
 current=list(HighmarkFundMap.objects.filter(client=client).values('prefix','fund').order_by('prefix'))
 if request.method=='GET':
  return Response({'mappings':current or [{'prefix':p,'fund':v} for p,v in DEFAULT_HM_FUNDS.items()], 'editable':is_operator(request.user)})
 if not is_operator(request.user):return Response({'detail':'Only administrators may edit mappings'},status=403)
 data=request.data.get('mappings')
 if not isinstance(data,list) or len(data)>100:return Response({'detail':'Provide up to 100 mappings'},status=400)
 seen=set();clean=[]
 for row in data:
  if not isinstance(row,dict):return Response({'detail':'Invalid mapping'},status=400)
  prefix=str(row.get('prefix','')).strip().upper();fund=str(row.get('fund','')).strip()
  if not prefix.isalnum() or len(prefix)>12 or not fund or len(fund)>100 or prefix in seen:
   return Response({'detail':'Invalid or duplicate prefix/fund mapping'},status=400)
  seen.add(prefix);clean.append((prefix,fund))
 with transaction.atomic():
  HighmarkFundMap.objects.filter(client=client).delete()
  HighmarkFundMap.objects.bulk_create([HighmarkFundMap(client=client,prefix=p,fund=f) for p,f in clean])
 return Response({'mappings':[{'prefix':p,'fund':f} for p,f in clean]})

@api_view(['GET', 'PUT'])
def folder_settings(request):
 client=allowed_client(request)
 if request.method=='GET':
  return Response({'client':client.code, 'paths':client_paths(client), 'editable':is_operator(request.user)})
 if not is_operator(request.user):
  return Response({'detail':'Only client administrators may edit folder paths.'},status=403)
 paths=request.data.get('paths')
 if not isinstance(paths,dict) or set(paths)!={'error_reports','835','837'}:
  return Response({'detail':'Provide error_reports, 835 and 837 paths.'},status=400)
 checked={kind:validate_path(paths[kind],kind) for kind in ('error_reports','835','837')}
 cfg,_=ClientFolderConfig.objects.get_or_create(client=client)
 cfg.error_reports_path=checked['error_reports'];cfg.edi_835_path=checked['835'];cfg.edi_837_path=checked['837'];cfg.save()
 return Response({'client':client.code, 'paths':client_paths(client), 'editable':True})
