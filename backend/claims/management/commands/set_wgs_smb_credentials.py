from getpass import getpass
from django.core.management.base import BaseCommand, CommandError
from claims.models import Client, SmbCredential
from claims.smb_auth import encrypt_password, DEFAULT_SHARE

class Command(BaseCommand):
    help='Save WGS Bicc credentials securely using the local server console (never CLI args).'
    def handle(self, *args, **kwargs):
        try:
            client=Client.objects.get(code='WGS')
        except Client.DoesNotExist as exc:
            raise CommandError('WGS client missing.') from exc
        self.stdout.write('Configuring WGS EDI access to '+DEFAULT_SHARE)
        username=input('SMB username [americanbenefit\\ediuser]: ').strip() or r'americanbenefit\ediuser'
        password=getpass('SMB password (hidden): ')
        if not password:raise CommandError('Empty password not allowed.')
        try:encrypted=encrypt_password(password)
        except Exception as exc:raise CommandError('Encryption key missing/invalid; check server key file.') from exc
        SmbCredential.objects.update_or_create(client=client,defaults={
            'share_path':DEFAULT_SHARE, 'username':username, 'password_ciphertext':encrypted})
        self.stdout.write(self.style.SUCCESS('WGS SMB credentials encrypted and saved.'))
