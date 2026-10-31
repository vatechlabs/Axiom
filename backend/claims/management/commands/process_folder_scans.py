import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections, models
from claims.models import Client, FolderScanSchedule, FileSourceRule
from claims.folder_processing import process_client
from claims.recon_processing import scan_recon_files

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
            # AXIOM_RECON_SCHEDULE_V1: Scheduled clients scan RECON inside
            # perform_scan(), so never poll them independently between runs.
            # Preserve legacy RECON polling only for clients with no enabled schedule.
            for client in Client.objects.filter(models.Q(folder_config__isnull=False) | models.Q(file_source_rules__isnull=False)).distinct().order_by('id'):
                schedule=FolderScanSchedule.objects.filter(client=client).first()
                if schedule and schedule.enabled:
                    continue
                try:
                    scan_recon_files(client)
                except Exception as exc:
                    self.stderr.write(f'{client.code} RECON: {exc}')
            if not options['watch']:break
            time.sleep(max(10,options['interval']))
