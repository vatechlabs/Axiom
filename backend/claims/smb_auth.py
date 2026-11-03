"""Windows SMB authentication for configured WGS 835/837 sources.

Scheduler and downloads share a persistent connection guard. This uses Windows' native WNet API,
not shell commands, and never logs passwords.
"""
import ctypes
import os
import sqlite3
from contextlib import closing
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
    """Reserve one attempt before contacting Windows, even across processes."""
    if os.name != 'nt' or client.code.upper() != 'WGS':
        return False
    cfg = SmbCredential.objects.filter(client=client).first()
    if cfg is None:
        raise RuntimeError('WGS SMB credentials not configured in Settings.')
    password = decrypt_password(cfg.password_ciphertext)
    host = cfg.share_path.lstrip('\\').split('\\', 1)[0].casefold()
    guard = key_path().parent / 'axiom_smb_guard.sqlite3'
    with closing(sqlite3.connect(guard)) as db:
        with db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS blocked (host TEXT PRIMARY KEY)'
            )
            reserved = db.execute(
                'INSERT OR IGNORE INTO blocked VALUES (?)', (host,)
            ).rowcount
        if not reserved:
            raise OSError(f'SMB connection blocked for {host}; contact an administrator.')
        # Commit before the network call. A crash leaves the host blocked.
        _connect_windows(cfg.share_path, cfg.username, password)
        with db:
            db.execute('DELETE FROM blocked WHERE host = ?', (host,))
    return True


def _connect_windows(share, username, password):
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
        code=func(ctypes.byref(resource),password,username,0)
    finally:
        password=None
    if code:
        raise OSError(f'SMB connection failed (Windows error {code}) for {share}; verify credentials and share permissions.')


def connect_for_edi_path(client, path):
    """Connect only for WGS EDI files beneath the configured share."""
    if os.name!='nt' or client.code.upper()!='WGS':return
    cfg=SmbCredential.objects.filter(client=client).first()
    if not cfg:return
    target=str(path).replace('/','\\').casefold()
    share=cfg.share_path.rstrip('\\').casefold()
    if target.startswith(share+'\\'):
        connect_share(client)
