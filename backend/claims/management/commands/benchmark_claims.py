"""Read-only first-page timings against the installed database."""
from time import perf_counter
from statistics import median
from types import SimpleNamespace
from django.core.management.base import BaseCommand, CommandError
from django.http import QueryDict
from django.db import connection
from django.test.utils import CaptureQueriesContext
from claims.models import Client
from claims.views import selected_batch
from claims.querying import snapshot_query, filter_query, rows_for

class Command(BaseCommand):
    help = 'Read-only timings for claims page queries. Does not change any records.'
    def add_arguments(self, parser):
        parser.add_argument('--client', required=True)
        parser.add_argument('--lag-date', default='')
        parser.add_argument('--page-size', type=int, default=100)
        parser.add_argument('--runs', type=int, default=3)
    def handle(self, *args, **options):
        if not 1 <= options['page_size'] <= 200 or not 1 <= options['runs'] <= 10:
            raise CommandError('page-size must be 1-200; runs must be 1-10')
        try:
            client = Client.objects.get(code=options['client'])
        except Client.DoesNotExist:
            raise CommandError('Unknown client')
        params = QueryDict('', mutable=True)
        if options['lag_date']:
            params['lag_date'] = options['lag_date']
        timings = []
        for number in range(options['runs']):
            start = perf_counter()
            with CaptureQueriesContext(connection) as queries:
                try:
                    batch = selected_batch(SimpleNamespace(query_params=params), client)
                except ValueError as exc:
                    raise CommandError(str(exc))
                if batch is None:
                    raise CommandError('No processed report for this selection')
                qs = filter_query(snapshot_query(client,batch),params)
                count = qs.count()
                rows = rows_for(qs[:options['page_size']],batch.report_date)
            elapsed = (perf_counter()-start)*1000
            timings.append(elapsed)
            self.stdout.write(f'Run {number+1}: {elapsed:.1f} ms; {len(queries)} queries; {len(rows)} rows returned; {count} matching')
        self.stdout.write(f'Median database/application query time: {median(timings):.1f} ms (excludes HTTP, browser and authentication)')
