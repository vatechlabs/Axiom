"""Independent, client-scoped RECON file/record browse and XLSX export."""
import io
import json
from datetime import date

from django.http import HttpResponse
from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import ReconFile, ReconRecord
from .permissions import allowed_client


def _scope(request):
    client=allowed_client(request)
    qs=ReconFile.objects.filter(client=client)
    subtype=request.query_params.get('sub_client','').strip().upper()
    if client.code.upper()=='WGS':
        if subtype not in ('MUSGROVE','ABC'):
            return client,qs.none(),False
        qs=qs.filter(sub_client=subtype)
    else:
        qs=qs.filter(sub_client='')
    return client,qs,True


def _records(request):
    client,sources,valid=_scope(request)
    if not valid:return client,ReconRecord.objects.none(),False
    qs=ReconRecord.objects.filter(source__in=sources.filter(status='indexed')).select_related('source')
    filename=request.query_params.get('file_id','').strip()
    if filename:
        try: fid=int(filename)
        except ValueError:return client,qs.none(),False
        qs=qs.filter(source_id=fid)
    q=request.query_params.get('search','').strip()
    if q:qs=qs.filter(claim_number__icontains=q)
    when=request.query_params.get('claim_date','').strip()
    if when:
        try:qs=qs.filter(claim_date=date.fromisoformat(when))
        except ValueError:return client,qs.none(),False
    return client,qs.order_by('-source__indexed_at','source_id','row_number'),True


def _file_dict(f):
    return {'id':f.id,'filename':f.filename,'sub_client':f.sub_client,'indexed_at':f.indexed_at,
            'status':f.status,'rows':f.row_count,'error':f.error}


@api_view(['GET'])
def files(request):
    _,qs,valid=_scope(request)
    if not valid:return Response({'detail':'Select MUSGROVE or ABC for WGS.'},status=400)
    return Response({'files':[_file_dict(f) for f in qs.order_by('-indexed_at','-id')[:200]]})


@api_view(['GET'])
def records(request):
    client,qs,valid=_records(request)
    if not valid:return Response({'detail':'Invalid RECON filter or sub-client.'},status=400)
    try:page=max(1,int(request.query_params.get('page','1')))
    except ValueError:page=1
    size=100
    count=qs.count()
    if page>(count+size-1)//size+1:return Response({'count':count,'results':[],'page':page,'total_pages':(count+size-1)//size})
    results=[{'id':r.id,'file_id':r.source_id,'filename':r.source.filename,'claim_number':r.claim_number,
              'claim_date':r.claim_date,'row_number':r.row_number,'raw_data':r.raw_data} for r in qs[(page-1)*size:page*size]]
    return Response({'count':count,'page':page,'page_size':size,'total_pages':(count+size-1)//size,'results':results})


@api_view(['GET'])
def export(request):
    """Export original RECON fields into individual Excel columns, not JSON.

    For one source file the header order is exactly the imported CSV header order.
    For multiple files the first file's header order is retained and additional
    unique columns are appended in encounter order.
    """
    client, qs, valid = _records(request)
    if not valid:
        return Response({'detail': 'Invalid RECON filter or sub-client.'}, status=400)
    ids = request.query_params.get('ids', '').strip()
    if ids:
        try:
            keys = [int(x) for x in ids.split(',') if x.strip()]
        except ValueError:
            return Response({'detail': 'Invalid selected IDs.'}, status=400)
        if len(keys) > 10000:
            return Response({'detail': 'Too many selected IDs.'}, status=400)
        qs = qs.filter(id__in=keys)
    count = qs.count()
    if count > 100000:
        return Response({'detail': 'Export exceeds 100,000 rows; filter by file or date.'}, status=400)

    from openpyxl import Workbook
    from openpyxl.utils.exceptions import IllegalCharacterError

    # Query twice to avoid retaining all row payloads in RAM. Dict order is
    # preserved when importing CSV and when round-tripping PostgreSQL JSONB
    # only insofar as the DB retains it; use original source header when present.
    columns = []
    seen = set()
    checked_sources = set()
    for record in qs.iterator(chunk_size=500):
        # JSONB can reorder object keys. Read the original CSV header from the
        # approved local folder to recover the source's column sequence.
        if record.source_id not in checked_sources:
            checked_sources.add(record.source_id)
            from pathlib import Path
            import csv
            source_path = Path(record.source.path).resolve()
            approved = Path('/data/clients').resolve()
            if source_path.is_relative_to(approved) and source_path.suffix.lower() == '.csv' and source_path.is_file():
                try:
                    with source_path.open('r', encoding='utf-8-sig', newline='') as fh:
                        header = next(csv.reader(fh), [])
                except UnicodeDecodeError:
                    with source_path.open('r', encoding='cp1252', newline='') as fh:
                        header = next(csv.reader(fh), [])
                for key in (value.strip() for value in header):
                    if key and key not in seen:
                        seen.add(key)
                        columns.append(key)
        for key in (record.raw_data or {}).keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)

    # Stable metadata is placed AFTER the raw columns, never in their place.
    wb = Workbook(write_only=True)
    ws = wb.create_sheet('RECON')
    ws.append(columns + ['AXIOM_SOURCE_FILE', 'AXIOM_SOURCE_ROW', 'AXIOM_CLIENT', 'AXIOM_SUB_CLIENT'])
    def cell(value):
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        text = str(value)
        # A source claim number must be exported as TEXT, never coerced to a
        # numeric type (leading zeros must survive).
        return ''.join(ch for ch in text if ch in '\t\n\r' or ord(ch) >= 32)

    for record in qs.iterator(chunk_size=500):
        raw = record.raw_data or {}
        ws.append([cell(raw.get(key, '')) for key in columns] + [
            record.source.filename,
            record.row_number,
            client.code,
            record.source.sub_client or 'Highmark',
        ])
    output = io.BytesIO()
    wb.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="RECON_{client.code}.xlsx"'
    return response
