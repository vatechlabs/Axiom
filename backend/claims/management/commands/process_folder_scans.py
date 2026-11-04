"""Scheduled scan worker and per-client supervisor.

Each client receives its own child process so an inaccessible network share or
slow reconciliation for one client cannot stop polling another client's queue.
The child uses the existing per-client DB advisory lock and scanning functions.
"""
import re
import subprocess
import sys
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections, models
from claims.models import Client, FolderScanSchedule
from claims.folder_processing import process_client
from claims.recon_processing import scan_recon_files


def _worker_clients():
    """Configured clients, including those using legacy RECON-only polling."""
    return Client.objects.filter(
        models.Q(scan_schedule__isnull=False)
        | models.Q(folder_config__isnull=False)
        | models.Q(file_source_rules__isnull=False)
    ).distinct().order_by('id')


def _run_single_client(client, out, err):
    """Preserve the old worker's scan and legacy RECON behavior for one client."""
    if FolderScanSchedule.objects.filter(client=client).exists():
        try:
            ran = process_client(client)
            if ran:
                out.write('Finished scan attempt for %s\n' % client.code)
        except Exception as exc:
            err.write('%s: %s\n' % (client.code, exc))
    # Preserve legacy RECON polling, but isolate it in this client's process.
    if (Client.objects.filter(pk=client.pk).filter(
            models.Q(folder_config__isnull=False)
            | models.Q(file_source_rules__isnull=False)).exists()
            and not FolderScanSchedule.objects.filter(client=client, enabled=True).exists()):
        try:
            scan_recon_files(client)
        except Exception as exc:
            err.write('%s RECON: %s\n' % (client.code, exc))


class Command(BaseCommand):
    help = 'Process queued/due scans. In --watch mode, isolate each client in its own worker process.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--once', action='store_true')
        parser.add_argument('--interval', type=int, default=30)
        parser.add_argument('--client-id', type=int, default=None,
                            help='Process only this client in the current process (worker child).')

    def handle(self, *args, **options):
        if options['watch'] and options['once']:
            raise CommandError('Choose --watch or --once, not both.')
        if options['watch'] and options['client_id'] is not None:
            raise CommandError('--client-id can only be used with --once.')
        if options['client_id'] is not None:
            client = Client.objects.filter(pk=options['client_id']).first()
            if client is None:
                raise CommandError('Unknown client ID')
            _run_single_client(client, self.stdout, self.stderr)
            return
        if not options['watch']:
            # Backward compatible --once: same client order and behavior.
            close_old_connections()
            for client in _worker_clients():
                _run_single_client(client, self.stdout, self.stderr)
            return

        # Supervise at most one child process per client. A blocked child stays
        # isolated; another client's scan can continue independently.
        interval = max(10, options['interval'])
        workers = {}
        logs_dir = Path.cwd() / 'logs' / 'folder_scans'
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.stdout.write('Axiom isolated folder-scan supervisor started.')
        try:
            while True:
                close_old_connections()
                for cid, (process, code) in list(workers.items()):
                    rc = process.poll()
                    if rc is not None:
                        if rc != 0:
                            self.stderr.write('Worker %s exited with code %s (see folder_scans logs).' % (code, rc))
                        del workers[cid]
                try:
                    clients = list(_worker_clients().values_list('id', 'code'))
                except Exception as exc:
                    self.stderr.write('Cannot query configured scan clients: %s' % exc)
                    time.sleep(interval)
                    continue
                for cid, code in clients:
                    if cid in workers:
                        continue
                    # Windows-compatible: inherit normal service permissions,
                    # environment and Python venv. No shell and no detached jobs.
                    safe_code = re.sub(r'[^A-Za-z0-9_-]', '_', code)[:48]
                    log_path = logs_dir / ('client_%s_%s.log' % (cid, safe_code))
                    command = [sys.executable, '-u', 'manage.py', 'process_folder_scans',
                               '--once', '--client-id', str(cid)]
                    try:
                        with log_path.open('a', encoding='utf-8') as logfile:
                            child = subprocess.Popen(command, cwd=str(Path.cwd()),
                                                     stdout=logfile, stderr=subprocess.STDOUT)
                        workers[cid] = (child, code)
                    except OSError as exc:
                        self.stderr.write('Cannot start worker %s: %s' % (code, exc))
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write('Supervisor stopping. Existing child scans are not force-killed.')
