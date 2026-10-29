"""Windows SMB authentication for configured WGS 835/837 sources.

Only the scheduler calls connect_share(). This uses Windows' native WNet API,
not shell commands, and never logs passwords.
"""
import ctypes
import os
from pathlib import Path
from ctypes import wintypes
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken
from .models import SmbCredential

DEFAULT_SHARE = r'\\10.68.5.65\Bicc'

def key_path():
    return Path(os.environ.get('AXIOM_SMB_KEY_FILE') or
                (r'C:\AxiomApp\secrets\axiom_smb.key' if os.name == 'nt' else str(settings.BASE_DIR / 'axiom_smb.key')))

def cipher():
    path=key_path()
    if not path.is_file():
        raise RuntimeError('SMB encryption key is missing. Configure AXIOM_SMB_KEY_FILE or C:\\AxiomApp\\secrets\\axiom_smb.key.')
    return Fernet(path.read_bytes().strip())

def encrypt_password(password):
    return cipher().encrypt(password.encode('utf-8'))

def decrypt_password(value):
    try:
        return cipher().decrypt(bytes(value)).decode('utf-8')
    except InvalidToken as exc:
        raise RuntimeError('Cannot decrypt SMB password; check the encryption key file.') from exc

def connect_share(client):
    """Authenticate within the process performing network I/O, never in a shell session.

    This connection is kept for the lifetime of the process so subsequent reads
    and API download operations can use it. Separate backend process must also
    establish its own session before reading EDI files.
    """
    if os.name != 'nt' or client.code.upper() != 'WGS':
        return False
    cfg=SmbCredential.objects.filter(client=client).first()
    if cfg is None:
        raise RuntimeError('WGS SMB credentials not configured in Settings.')
    password=decrypt_password(cfg.password_ciphertext)
    share=cfg.share_path
    class NETRESOURCEW(ctypes.Structure):
        _fields_=[('dwScope',wintypes.DWORD),('dwType',wintypes.DWORD),
                  ('dwDisplayType',wintypes.DWORD),('dwUsage',wintypes.DWORD),
                  ('lpLocalName',wintypes.LPWSTR),('lpRemoteName',wintypes.LPWSTR),
                  ('lpComment',wintypes.LPWSTR),('lpProvider',wintypes.LPWSTR)]
    resource=NETRESOURCEW()
    resource.dwType=1  # RESOURCETYPE_DISK
    resource.lpRemoteName=share
    func=ctypes.windll.mpr.WNetAddConnection2W
    func.argtypes=[ctypes.POINTER(NETRESOURCEW),wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD]
    func.restype=wintypes.DWORD
    try:
        code=func(ctypes.byref(resource),password,cfg.username,0)
    finally:
        password=None
    if code not in (0,85):
        # ERROR_ALREADY_ASSIGNED (85) can represent an existing connection;
        # an actual folder-read check remains necessary to prove accessibility.
        raise OSError(f'SMB connection failed (Windows error {code}) for {share}; verify credentials and share permissions.')
    return True

def connect_for_edi_path(client, path):
    """Connect only for WGS EDI files beneath the configured share."""
    if os.name!='nt' or client.code.upper()!='WGS':return
    cfg=SmbCredential.objects.filter(client=client).first()
    if not cfg:return
    target=str(path).replace('/','\\').casefold()
    share=cfg.share_path.rstrip('\\').casefold()
    if target.startswith(share+'\\'):
        connect_share(client)
