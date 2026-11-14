from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import FolderScanSchedule, FolderScanRun, EdiDocument, ErrorHistory, Membership
from .app_roles import is_operator
from .permissions import allowed_client,can_edit
from .folder_processing import queue_scan


def schedule_data(schedule):
    return {'enabled':schedule.enabled,'time':schedule.daily_time.strftime('%H:%M'),
            'days':schedule.weekdays,'timezone':schedule.timezone_name}

@api_view(['GET','PUT'])
def processing_schedule(request):
    client=allowed_client(request)
    schedule,_=FolderScanSchedule.objects.get_or_create(client=client)
    editable=is_operator(request.user)
    if request.method=='PUT':
        if not editable:return Response({'detail':'Only client administrators may change the schedule.'},status=403)
        data=request.data
        enabled=data.get('enabled')
        days=data.get('days')
        if not isinstance(enabled,bool) or not isinstance(days,list) or any(type(d) is not int or not 0<=d<=6 for d in days):
            return Response({'detail':'Provide enabled (boolean) and days (Monday=0 through Sunday=6).'},status=400)
        if enabled and not days:return Response({'detail':'Choose at least one day.'},status=400)
        try:
            value=data.get('time','')
            if len(value)!=5 or value[2]!=':':raise ValueError()
            daily=time.fromisoformat(value)
            name=data.get('timezone','')
            if not isinstance(name,str) or len(name)>64:raise ValueError()
            ZoneInfo(name)
        except (ValueError,TypeError,ZoneInfoNotFoundError):
            return Response({'detail':'Enter time as HH:MM and a valid IANA timezone, such as Asia/Kolkata or America/New_York.'},status=400)
        with transaction.atomic():
            schedule=FolderScanSchedule.objects.select_for_update().get(pk=schedule.pk)
            # A changed timezone starts a new local-day schedule; otherwise do not rerun today twice.
            if schedule.timezone_name!=name:schedule.last_scheduled_date=None
            schedule.enabled=enabled;schedule.daily_time=daily;schedule.weekdays=sorted(set(days));schedule.timezone_name=name;schedule.save()
    return Response({'schedule':schedule_data(schedule),'editable':editable,'can_run':is_operator(request.user)})

@api_view(['POST'])
def processing_run(request):
    client=allowed_client(request);can_edit(request,client)
    FolderScanSchedule.objects.get_or_create(client=client)
    run,created=queue_scan(client)
    return Response({'id':run.pk,'status':run.status,'created':created},status=202)

@api_view(['GET'])
def processing_status(request):
    client=allowed_client(request)
    schedule,_=FolderScanSchedule.objects.get_or_create(client=client)
    recent=list(FolderScanRun.objects.filter(client=client).values('id','trigger','status','created_at','started_at','finished_at','processed','skipped','deferred','failed','message')[:10])
    age=(timezone.now()-schedule.worker_seen_at).total_seconds() if schedule.worker_seen_at else None
    result={'revision':schedule.revision,'worker_seen_at':schedule.worker_seen_at,'worker_online':age is not None and age<180,'runs':recent}
    if request.query_params.get('summary')=='1':return Response(result)
    run_id=request.query_params.get('run')
    if run_id:
        if not run_id.isdigit():return Response({'detail':'Invalid run ID'},status=400)
        run=get_object_or_404(FolderScanRun,pk=run_id,client=client)
    else:run=FolderScanRun.objects.filter(client=client).first()
    try:page=max(1,int(request.query_params.get('page','1')))
    except ValueError:return Response({'detail':'Invalid page'},status=400)
    items=[];total=0
    if run:
        # AXIOM_HISTORY_OUTCOMES_ONLY_V1: Only newly indexed and failed
        # entries belong in the detailed table. Unchanged/deferred totals
        # remain visible in the summary above it.
        visible_items=run.items.filter(status__in=('indexed','failed'))
        total=visible_items.count()
        records=list(visible_items[(page-1)*25:page*25])
        file_ids=[r.edi_file_id for r in records if r.edi_file_id]
        batch_ids=[r.import_batch_id for r in records if r.import_batch_id]
        edi_counts={r['edi_file_id']:r['n'] for r in EdiDocument.objects.filter(claim__client=client,edi_file_id__in=file_ids).order_by().values('edi_file_id').annotate(n=Count('claim_id',distinct=True))}
        error_counts={r['observation__batch_id']:r['n'] for r in ErrorHistory.objects.filter(claim__client=client,observation__batch_id__in=batch_ids).order_by().values('observation__batch_id').annotate(n=Count('claim_id',distinct=True))}
        for item in records:
            matched=edi_counts.get(item.edi_file_id,0) if item.edi_file_id else error_counts.get(item.import_batch_id,0)
            items.append({'id':item.pk,'kind':item.kind,'filename':item.filename,'status':item.status,'indexed_count':item.indexed_count,'matched_claims':matched,'message':item.message})
    result.update(selected_run=run.pk if run else None,items=items,count=total,page=page,total_pages=(total+24)//25)
    return Response(result)


@api_view(['GET'])
def upload_history(request):
    """Admin-only, client-scoped retained audit. Links reflect current data."""
    from django.db.models import OuterRef, Subquery, IntegerField, Value
    from django.db.models.functions import Coalesce
    from .models import UploadEvent
    if not is_operator(request.user):
        return Response({'detail': 'Only administrators may view upload history.'}, status=403)
    client = allowed_client(request)
    try:
        page = int(request.query_params.get('page', '1'))
        if page < 1:
            raise ValueError()
    except (TypeError, ValueError):
        return Response({'detail': 'Page must be a positive integer.'}, status=400)
    end = timezone.now()
    start = request.query_params.get('date_from') or None
    qs = UploadEvent.objects.filter(client=client, occurred_at__lte=end)
    from datetime import date
    from django.db.models import Q
    for param, lookup in (('date_from', 'gte'), ('date_to', 'lte')):
        raw = request.query_params.get(param, '')
        if raw:
            try:
                value = date.fromisoformat(raw)
            except ValueError:
                return Response({'detail': param + ' must be YYYY-MM-DD'}, status=400)
            qs = qs.filter(**{'occurred_at__date__' + lookup: value})
    if request.query_params.get('date_from', '') > (request.query_params.get('date_to') or '9999-12-31'):
        return Response({'detail': 'From date must not exceed to date.'}, status=400)
    search = request.query_params.get('search', '').strip()
    if search:
        qs = qs.filter(Q(filename__icontains=search) | Q(kind__icontains=search) |
                       Q(status__icontains=search) | Q(sub_client__icontains=search) |
                       Q(trigger__icontains=search) | Q(message__icontains=search))
    for field in ('kind', 'status', 'sub_client'):
        value = request.query_params.get(field, '').strip()
        if value:
            qs = qs.filter(**{field: value})
    count = qs.count()
    links = EdiDocument.objects.filter(edi_file_id=OuterRef('edi_file_id'), claim__client=client).order_by().values('edi_file_id').annotate(n=Count('claim_id', distinct=True)).values('n')
    records = qs.annotate(linked=Coalesce(Subquery(links, output_field=IntegerField()), Value(0)))[(page-1)*50:page*50]
    return Response({'count': count, 'page': page, 'page_size': 50, 'total_pages': (count+49)//50,
                     'from': start, 'to': end, 'results': [
        {'id': e.pk, 'occurred_at': e.occurred_at, 'filename': e.filename,
         'kind': e.kind, 'sub_client': e.sub_client, 'trigger': e.trigger,
         'status': e.status, 'record_count': e.record_count,
         'linked_claims': e.linked if e.edi_file_id else None, 'message': e.message}
        for e in records]})
