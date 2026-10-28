from rest_framework.exceptions import PermissionDenied
from .models import Membership, Client
from .app_roles import is_demo, is_operator

def allowed_client(request):
    code=request.query_params.get('client') or request.data.get('client')
    if not code:raise PermissionDenied('Select an authorized client.')
    if is_demo(request.user) or is_operator(request.user):
        client=Client.objects.filter(code=code).first()
        if not client:raise PermissionDenied('Unknown client.')
        return client
    m=Membership.objects.select_related('client').filter(user=request.user,client__code=code).first()
    if not m:raise PermissionDenied('You are not authorized for this client.')
    return m.client

def can_edit(request,client):
    if not is_operator(request.user):raise PermissionDenied('Only super users may modify data.')
