import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from claims.models import Client, FolderScanSchedule
from claims.folder_processing import process_client

class Command(BaseCommand):
    help='Process due schedules and queued manual scans. Run continuously or every minute through Task Scheduler.'
    def add_arguments(self,parser):
        parser.add_argument('--watch',action='store_true')
        parser.add_argument('--once',action='store_true')
        parser.add_argument('--interval',type=int,default=30)
    def handle(self,*args,**options):
        if options['watch'] and options['once']:
            self.stderr.write('Choose --watch or --once, not both.');return
        while True:
            close_old_connections()
            # Only configured clients need a heartbeat or scheduled processing.
            for client in Client.objects.filter(scan_schedule__isnull=False).order_by('id'):
                try:
                    ran=process_client(client)
                    if ran:self.stdout.write(f'Finished scan attempt for {client.code}')
                except Exception as exc:
                    self.stderr.write(f'{client.code}: {exc}')
            if not options['watch']:break
            time.sleep(max(10,options['interval']))
