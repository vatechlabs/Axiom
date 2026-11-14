"""Read-only evidence for files omitted by rules or indexed without claim links."""
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from claims.models import Client, FileSourceRule, EdiFile, EdiDocument
from claims.rule_engine import match_file, wgs_837_year_allowed, valid_root
from claims.folder_paths import client_paths


class Command(BaseCommand):
    help = 'Show configured 837 sources and why a supplied filename is accepted/rejected. Does not open SMB sessions or modify data.'

    def add_arguments(self, parser):
        parser.add_argument('--client', default='WGS')
        parser.add_argument('--filename', help='Exact filename or path of a missing 837')

    def handle(self, *args, **options):
        client = Client.objects.filter(code=options['client']).first()
        if not client:
            raise CommandError('Unknown client')
        rules = list(FileSourceRule.objects.filter(client=client,file_type='837'))
        if not rules:
            self.stdout.write('No explicit 837 rules. Default source: '+str(client_paths(client)['837']))
        for rule in rules:
            self.stdout.write(f'Rule {rule.pk}: enabled={rule.enabled}; folder={rule.folder_path}; recursive={rule.recursive}; include={rule.include_subfolders}; exclude={rule.exclude_subfolders}; prefix={rule.starts_with}; suffix={rule.ends_with}; extensions={rule.extensions}')
        self.stdout.write('Check MUSGROW versus MUSGROVE spelling against the actual server folder.')
        name = options.get('filename')
        if not name:
            self.stdout.write('Run again with --filename "EXACT_FILENAME.837" to diagnose filename restrictions.')
            return
        path = Path(name.replace('\\','/'))
        if client.code.upper() == 'WGS':
            self.stdout.write('WGS filename date (MMDDYYYY, 2025+): '+str(wgs_837_year_allowed(path)))
        for rule in rules:
            self.stdout.write(f'Rule {rule.pk} filename matches: {rule.enabled and match_file(rule,path)}')
        self.stdout.write('Scanner extension accepted: '+str(path.suffix.lower() in ('.837','.edi','.txt')))
        files=EdiFile.objects.filter(client=client,kind='837',filename=path.name)
        if not files.exists():
            self.stdout.write('No existing index for this filename. Check rule folder, selected subfolders, permissions, date and extension.')
        for file in files:
            self.stdout.write(f'Index {file.pk}: identifiers={file.claims_indexed.count()}, linked claims={EdiDocument.objects.filter(edi_file=file).values("claim_id").distinct().count()}')
