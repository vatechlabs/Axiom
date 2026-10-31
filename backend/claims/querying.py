"""Database-side daily snapshot filtering; enrich only the requested page."""
from django.db.models import Q, F, Value, OuterRef, Subquery, TextField, IntegerField, FloatField, Count, Min
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Coalesce, NullIf, Cast
from rest_framework.exceptions import ValidationError
from .models import LagClaimSnapshot, ErrorHistory, EdiDocument


def snapshot_text(key, fallback=None):
    current = NullIf(KeyTextTransform(key, 'row_data___snapshot'), Value(''))
    values = [current]
    if fallback:
        values.append(NullIf(KeyTextTransform(fallback, 'row_data'), Value('')))
    return Coalesce(*values, Value(''), output_field=TextField())


def snapshot_query(client, batch):
    if batch is None:
        return LagClaimSnapshot.objects.none()
    base = LagClaimSnapshot.objects.filter(client=client, batch=batch).annotate(
        s_number=snapshot_text('claim_number', 'col_2'),
        s_dcn=snapshot_text('dcn'), s_fund=snapshot_text('fund', 'col_9'),
        s_tpa=snapshot_text('tpa'),
        s_tpa_age=Cast(Cast(Coalesce(NullIf(snapshot_text('tpa_age', 'col_5'), Value('')), Value('0')), FloatField()), IntegerField()),
        s_total_age=Cast(Cast(Coalesce(NullIf(snapshot_text('total_age', 'col_1'), Value('')), Value('0')), FloatField()), IntegerField()),
    ).exclude(s_number='')
    first_ids = base.order_by().values('s_number').annotate(first=Min('id')).values('first')
    latest = ErrorHistory.objects.filter(claim_id=OuterRef('claim_id'), report_date__lte=batch.report_date).order_by('-report_date', '-id')
    return base.filter(id__in=Subquery(first_ids)).annotate(
        s_error=Coalesce(Subquery(latest.values('message')[:1]), Value(''), output_field=TextField())
    )


def highmark_snapshot_query(client, selected_date=None):
    """Latest batch per fund+report type, never merge WGS selection semantics."""
    from .models import ImportBatch
    from datetime import date
    from django.db.models import Max
    batches=ImportBatch.objects.filter(client=client,source='lag',status='processed')
    if selected_date:
        batches=batches.filter(report_date__lte=selected_date)
    batch_ids=list(batches.order_by('-report_date','-created_at','-id').values_list('id',flat=True))
    if not batch_ids:return LagClaimSnapshot.objects.none()
    all_rows=LagClaimSnapshot.objects.filter(client=client,batch_id__in=batch_ids).select_related('batch').order_by('-batch__report_date','-batch__created_at','-batch_id','id')
    # Fund and report type identified on each snapshot at upload time. This
    # prevents one adjustment batch from replacing an inventory batch.
    latest={}
    for s in all_rows.only('id','batch_id','row_data','batch__report_type','batch__report_date').iterator(chunk_size=2000):
        meta=(s.row_data or {}).get('_snapshot') or {}
        fund=meta.get('fund','Not Found')
        typ=s.batch.report_type or (s.row_data or {}).get('_highmark_type') or 'Daily Lag'
        key=(fund,typ)
        if key not in latest: latest[key]=s.batch_id
    allowed=set(latest.values())
    # A batch may contain multiple funds. Select only snapshots associated
    # with the winning (fund, report_type) pair.
    accepted=[]
    for s in all_rows.only('id','batch_id','row_data','batch__report_type').iterator(chunk_size=2000):
        meta=(s.row_data or {}).get('_snapshot') or {}
        key=(meta.get('fund','Not Found'),s.batch.report_type or (s.row_data or {}).get('_highmark_type') or 'Daily Lag')
        if latest.get(key)==s.batch_id:accepted.append(s.id)
    base=LagClaimSnapshot.objects.filter(client=client,id__in=accepted).select_related('batch').annotate(
        s_number=snapshot_text('claim_number','col_2'),
        s_dcn=snapshot_text('dcn'),s_fund=snapshot_text('fund','col_9'),
        s_tpa=snapshot_text('tpa'),
        s_tpa_age=Cast(Cast(Coalesce(NullIf(snapshot_text('tpa_age','col_5'),Value('')),Value('0')),FloatField()),IntegerField()),
        s_total_age=Cast(Cast(Coalesce(NullIf(snapshot_text('total_age','col_1'),Value('')),Value('0')),FloatField()),IntegerField()),
    ).exclude(s_number='')
    latest_errors=ErrorHistory.objects.filter(claim_id=OuterRef('claim_id'))
    if selected_date:latest_errors=latest_errors.filter(report_date__lte=selected_date)
    return base.annotate(s_error=Coalesce(Subquery(latest_errors.order_by('-report_date','-id').values('message')[:1]),Value(''),output_field=TextField()))

def filter_query(qs, params):
    def many(key):
        if hasattr(params, 'getlist'):
            return params.getlist(key)
        value = params.get(key, [])
        return value if isinstance(value, list) else [value] if value else []
    funds = many('fund')
    errors = many('error')
    if funds:
        qs = qs.filter(s_fund__in=funds)
    if errors:
        conditions = Q(s_error__in=[e for e in errors if e != 'none'])
        if 'none' in errors:
            conditions |= Q(s_error='')
        qs = qs.filter(conditions)
    search = params.get('search', '').strip()
    if search:
        qs = qs.filter(Q(s_number__icontains=search) | Q(s_dcn__icontains=search) | Q(s_fund__icontains=search) | Q(s_tpa__icontains=search) | Q(s_error__icontains=search))
    for field in ('tpa_age', 'total_age'):
        bounds = {}
        for suffix, lookup in (('min', 'gte'), ('max', 'lte')):
            raw = params.get(f'{field}_{suffix}', '')
            if raw == '':
                continue
            try:
                number = int(raw)
            except (ValueError, TypeError):
                raise ValidationError({'detail': f'{field}_{suffix} must be a nonnegative integer'})
            if number < 0:
                raise ValidationError({'detail': f'{field}_{suffix} must be a nonnegative integer'})
            bounds[suffix] = number
            qs = qs.filter(**{f's_{field}__{lookup}': number})
        if bounds.get('min', 0) > bounds.get('max', float('inf')):
            raise ValidationError({'detail': f'{field}: minimum exceeds maximum'})
    return qs.order_by('-s_total_age', 's_number', 'id')


def rows_for(queryset, report_date):
    snapshots = list(queryset)
    ids = [s.claim_id for s in snapshots]
    counts = {}
    if ids:
        for item in EdiDocument.objects.filter(claim_id__in=ids).order_by().values('claim_id', 'kind').annotate(n=Count('id')):
            counts[(item['claim_id'], item['kind'])] = item['n']
    return [dict(id=s.claim_id, claim_number=s.s_number, claim_submitter_id=s.s_number,
                 dcn=s.s_dcn, fund=s.s_fund, tpa=s.s_tpa, tpa_age=s.s_tpa_age,
                 total_age=s.s_total_age, error_message=s.s_error,
                 source_sheet=s.sheet_name, report_date=str(s.batch.report_date),
                 report_type=((s.row_data or {}).get('_highmark_type') or (s.batch.report_type if s.batch.report_type else s.sheet_name)),
                 amount=str((s.row_data or {}).get('col_15') or '0'),
                 edi_835_count=counts.get((s.claim_id,'835'),0),
                 edi_837_count=counts.get((s.claim_id,'837'),0)) for s in snapshots]
