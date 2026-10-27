from rest_framework.exceptions import PermissionDenied
from .models import Membership

def allowed_client(request):
 code=request.query_params.get('client') or request.data.get('client')
 if not code:raise PermissionDenied('Select an authorized client.')
 m=Membership.objects.select_related('client').filter(user=request.user,client__code=code).first()
 if not m:raise PermissionDenied('You are not authorized for this client.')
 return m.client

def can_edit(request,client):
 if not Membership.objects.filter(user=request.user,client=client,role__in=['admin','analyst']).exists():raise PermissionDenied('Read-only client access.')
