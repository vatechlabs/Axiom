"""Client-scoped administrator folder rule CRUD."""
from pathlib import Path
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import FileSourceRule
from .app_roles import is_operator
from .permissions import allowed_client
from .rule_engine import TYPES, DEFAULT_EXT, valid_root

FIELDS=('file_type','folder_path','starts_with','ends_with','extensions','recursive','include_subfolders','exclude_subfolders','enabled')
def serialize(rule):return {'id':rule.pk,**{f:getattr(rule,f) for f in FIELDS}}

@api_view(['GET','POST','PUT','DELETE'])
def folder_rules(request):
    client=allowed_client(request)
    if request.method=='GET':
        return Response({'rules':[serialize(r) for r in FileSourceRule.objects.filter(client=client).order_by('file_type','id')],'editable':is_operator(request.user)})
    if not is_operator(request.user):return Response({'detail':'Only super users can manage folder rules.'},status=403)
    try:
        if request.method=='DELETE':
            rule=FileSourceRule.objects.get(pk=int(request.data.get('id')),client=client)
            rule.delete()
            return Response({'ok':True})
        rule=(FileSourceRule.objects.get(pk=int(request.data.get('id')),client=client) if request.method=='PUT' else FileSourceRule(client=client))
        kind=str(request.data.get('file_type',rule.file_type)).strip()
        if kind not in TYPES:return Response({'detail':'Invalid file type.'},status=400)
        folder=str(request.data.get('folder_path',rule.folder_path)).strip()
        if len(folder)>2000 or not Path(folder).is_absolute() or any(x in folder for x in ('*','?')):
            return Response({'detail':'Enter an absolute folder path without wildcards.'},status=400)
        if not valid_root(folder):return Response({'detail':'Folder outside approved EDI_ALLOWED_ROOTS.'},status=400)
        rule.file_type=kind;rule.folder_path=str(Path(folder))
        for field in ('starts_with','ends_with','extensions','include_subfolders','exclude_subfolders'):
            value=request.data.get(field,getattr(rule,field) or (DEFAULT_EXT[kind] if field=='extensions' else ''))
            if not isinstance(value,str) or len(value)>500:return Response({'detail':field+' must be text under 500 characters.'},status=400)
            setattr(rule,field,value.strip())
        for field in ('enabled','recursive'):
            value=request.data.get(field,getattr(rule,field))
            if type(value)!=bool:return Response({'detail':field+' must be boolean.'},status=400)
            setattr(rule,field,value)
        rule.save()
        return Response(serialize(rule),status=201 if request.method=='POST' else 200)
    except (FileSourceRule.DoesNotExist,ValueError,TypeError):return Response({'detail':'Folder rule not found.'},status=404)
