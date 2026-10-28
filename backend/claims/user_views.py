"""User provisioning, restricted to the designated main super user."""
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .app_roles import is_main,is_demo

@api_view(['GET','POST','PATCH'])
def superusers(request):
    if not is_main(request.user):
        return Response({'detail':'Only the main super user can manage super users.'},status=403)
    User=get_user_model()
    if request.method=='GET':
        return Response({'users':[{'id':u.pk,'username':u.username,'email':u.email,'active':u.is_active} for u in User.objects.filter(is_superuser=True).order_by('username')]})
    if request.method=='POST':
        username=str(request.data.get('username','')).strip()
        password=str(request.data.get('password',''))
        email=str(request.data.get('email','')).strip()
        if not username or len(username)>150 or username.lower()=='demo':
            return Response({'detail':'Provide a valid, non-demo username.'},status=400)
        if User.objects.filter(username__iexact=username).exists():
            return Response({'detail':'Username is already in use.'},status=400)
        try:validate_password(password)
        except Exception as exc:return Response({'detail':list(exc.messages) if hasattr(exc,'messages') else str(exc)},status=400)
        with transaction.atomic():
            u=User.objects.create_user(username=username,email=email,password=password)
            u.is_staff=True;u.is_superuser=True;u.save(update_fields=['is_staff','is_superuser'])
        return Response({'id':u.pk,'username':u.username,'active':True},status=201)
    try: target=User.objects.get(pk=int(request.data.get('id')))
    except (User.DoesNotExist,ValueError,TypeError):return Response({'detail':'Unknown user.'},status=404)
    if not target.is_superuser or target.pk==request.user.pk or is_demo(target) or target.groups.filter(name='axiom_main_superuser').exists():
        return Response({'detail':'Cannot modify the main administrator or this account.'},status=403)
    if not isinstance(request.data.get('active'),bool):
        return Response({'detail':'active must be true or false.'},status=400)
    target.is_active=request.data['active'];target.save(update_fields=['is_active'])
    if not target.is_active:
        from rest_framework.authtoken.models import Token
        Token.objects.filter(user=target).delete()
    return Response({'id':target.pk,'active':target.is_active})


from django.db.models.deletion import ProtectedError
from rest_framework.authtoken.models import Token

def _manageable_user(request, user_id):
    User = get_user_model()
    try:
        target = User.objects.get(pk=int(user_id))
    except (User.DoesNotExist, ValueError, TypeError):
        return None, Response({'detail': 'Super user not found.'}, status=404)
    if (not target.is_superuser or target.pk == request.user.pk
            or is_demo(target)
            or target.groups.filter(name='axiom_main_superuser').exists()):
        return None, Response({'detail': 'This account cannot be modified.'}, status=403)
    return target, None

@api_view(['POST'])
def reset_superuser_password(request):
    if not is_main(request.user):
        return Response({'detail': 'Only main super user can change passwords.'}, status=403)
    target, error = _manageable_user(request, request.data.get('id'))
    if error is not None: return error
    password = request.data.get('new_password', '')
    if not isinstance(password, str) or password != request.data.get('confirm_password', ''):
        return Response({'detail': 'Passwords do not match.'}, status=400)
    try: validate_password(password, target)
    except Exception as exc:
        return Response({'detail': list(exc.messages) if hasattr(exc, 'messages') else str(exc)}, status=400)
    with transaction.atomic():
        target.set_password(password)
        target.save(update_fields=['password'])
        Token.objects.filter(user=target).delete()
    return Response({'detail': 'Password changed. Previous API token revoked.'})

@api_view(['POST'])
def delete_superuser(request):
    if not is_main(request.user):
        return Response({'detail': 'Only main super user can delete accounts.'}, status=403)
    target, error = _manageable_user(request, request.data.get('id'))
    if error is not None: return error
    if request.data.get('confirm_username') != target.username:
        return Response({'detail': 'Username confirmation does not match.'}, status=400)
    try:
        with transaction.atomic(): target.delete()
    except ProtectedError:
        return Response({'detail': 'Protected historical records prevent permanent deletion. Disable this account instead.'}, status=409)
    return Response({'detail': 'Super user deleted.'})
