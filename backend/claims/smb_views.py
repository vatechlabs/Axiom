"""Restricted settings for WGS EDI network credentials."""
import re
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .app_roles import is_operator
from .permissions import allowed_client
from .models import SmbCredential
from .smb_auth import encrypt_password, DEFAULT_SHARE

@api_view(['GET', 'PUT'])
def smb_credentials(request):
    client=allowed_client(request)
    if not is_operator(request.user) or client.code.upper()!='WGS':
        return Response({'detail':'Only WGS administrators can manage SMB credentials.'},status=403)
    cfg=SmbCredential.objects.filter(client=client).first()
    if request.method=='GET':
        return Response({'share_path':cfg.share_path if cfg else DEFAULT_SHARE,
                         'username':cfg.username if cfg else '',
                         'has_password':bool(cfg), 'configured':bool(cfg)})
    if not request.is_secure():
        return Response({'detail':'Password updates require HTTPS. Use the local server credential command until HTTPS is configured.'},status=403)
    share=str(request.data.get('share_path') or '').strip().rstrip('\\')
    username=str(request.data.get('username') or '').strip()
    password=request.data.get('password','')
    # Deliberately restrict credentials to the known Bicc share, preventing
    # an admin from using this endpoint to authenticate to arbitrary hosts.
    if share.casefold()!=DEFAULT_SHARE.casefold():
        return Response({'detail':'Only the approved Bicc network share is supported.'},status=400)
    if not username or len(username)>255 or '\n' in username or '\r' in username:
        return Response({'detail':'Enter a valid SMB username.'},status=400)
    if not isinstance(password,str) or len(password)>1024:
        return Response({'detail':'Invalid SMB password.'},status=400)
    if not password and not cfg:
        return Response({'detail':'Enter a password to create credentials.'},status=400)
    try:
        encrypted=encrypt_password(password) if password else bytes(cfg.password_ciphertext)
    except (OSError,ValueError,RuntimeError) as exc:
        return Response({'detail':'SMB encryption key unavailable or invalid. Contact server administrator.'},status=503)
    SmbCredential.objects.update_or_create(client=client,defaults={
        'share_path':DEFAULT_SHARE,'username':username,'password_ciphertext':encrypted})
    return Response({'saved':True,'configured':True,'has_password':True,
                     'share_path':DEFAULT_SHARE,'username':username})
