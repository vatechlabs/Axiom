"""Compatibility command: use the same audited and locked processing pipeline."""
import time
from django.core.management.base import BaseCommand,CommandError
from django.db import close_old_connections
from claims.models import Client,FolderScanSchedule,FolderScanRun
from claims.folder_processing import queue_scan,process_client

class Command(BaseCommand):
    help='Queue and process one client folder scan; supports --watch. Uses processing history and reconciliation.'
    def add_arguments(self,parser):
        parser.add_argument('--client',required=True)
        parser.add_argument('--watch',action='store_true')
        parser.add_argument('--interval',type=int,default=900)
    def handle(self,*args,**options):
        client=Client.objects.filter(code=options['client']).first()
        if not client:raise CommandError('Unknown client '+options['client'])
        FolderScanSchedule.objects.get_or_create(client=client)
        while True:
            close_old_connections()
            run,_=queue_scan(client,'cli')
            process_client(client)
            run.refresh_from_db()
            self.stdout.write(f'Run #{run.pk}: {run.status}. {run.message}')
            if not options['watch']:break
            time.sleep(max(60,options['interval']))
