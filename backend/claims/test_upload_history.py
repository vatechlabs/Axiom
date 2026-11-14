from datetime import date, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client as HttpClient
from django.utils import timezone
from rest_framework.authtoken.models import Token
from .models import (Client, Claim, ImportBatch, LagClaimSnapshot, UploadEvent,
                     FolderScanRun, FolderScanItem, EdiFile, EdiDocument, Membership)


class UploadHistoryTests(TestCase):
    def setUp(self):
        self.tenant = Client.objects.create(code='WGS', name='WGS')
        self.other = Client.objects.create(code='OTHER', name='Other')
        self.admin = get_user_model().objects.create_superuser('history-admin','admin@example.test','test')
        self.http = HttpClient(HTTP_AUTHORIZATION='Token '+Token.objects.create(user=self.admin).key)

    def event(self, key, **kwargs):
        values = dict(client=self.tenant, filename=key+'.837', kind='837', status='indexed', source_key=key)
        values.update(kwargs)
        return UploadEvent.objects.create(**values)

    def history(self, suffix=''):
        return self.http.get('/api/processing/history/?client=WGS'+suffix)

    def test_30_day_window_tenant_scope_stable_pagination_and_filters(self):
        now = timezone.now()
        for i in range(52):
            self.event(str(i), occurred_at=now, sub_client='MUSGROVE')
        self.event('old',occurred_at=now-timedelta(days=31))
        self.event('foreign',client=self.other)
        self.event('future',occurred_at=now+timedelta(days=1))
        first=self.history().json();second=self.history('&page=2').json()
        self.assertEqual(first['count'],52)
        self.assertEqual(len(first['results']),50)
        self.assertEqual(len(second['results']),2)
        self.assertFalse({x['id'] for x in first['results']} & {x['id'] for x in second['results']})
        self.assertEqual(self.history('&kind=835').json()['count'],0)
        self.assertEqual(self.history('&sub_client=MUSGROVE&status=indexed').json()['count'],52)
        self.assertEqual(self.history('&page=0').status_code,400)
        self.assertEqual(self.history('&page=abc').status_code,400)

    def test_history_requires_real_admin_even_if_membership_admin(self):
        viewer=get_user_model().objects.create_user('viewer',password='test')
        Membership.objects.create(user=viewer,client=self.tenant,role='admin')
        http=HttpClient(HTTP_AUTHORIZATION='Token '+Token.objects.create(user=viewer).key)
        self.assertEqual(http.get('/api/processing/history/?client=WGS').status_code,403)
        self.assertEqual(HttpClient().get('/api/processing/history/?client=WGS').status_code,401)

    def test_scan_outcomes_are_recorded_once_and_link_count_is_current(self):
        file=EdiFile.objects.create(client=self.tenant,kind='837',path='/MUSGROW/a.837',filename='a.837',fingerprint='a',bytes_size=1,modified_ns=1)
        run=FolderScanRun.objects.create(client=self.tenant,trigger='scheduled')
        item=FolderScanItem.objects.create(run=run,kind='837',filename='a.837',path=file.path,status='indexed',indexed_count=4,edi_file=file)
        item.save()
        event=UploadEvent.objects.get()
        self.assertEqual(event.sub_client,'MUSGROVE')
        self.assertEqual(event.trigger,'scheduled')
        self.assertEqual(self.history().json()['results'][0]['linked_claims'],0)
        claim=Claim.objects.create(client=self.tenant,claim_number='C1',report_date=date(2026,11,1))
        EdiDocument.objects.create(claim=claim,edi_file=file,kind='837',original_name='a.837')
        self.assertEqual(self.history().json()['results'][0]['linked_claims'],1)

    def test_lag_validation_failures_success_and_duplicate_attempts(self):
        url='/api/imports/lag/?client=WGS'
        self.assertEqual(self.http.post(url,{}).status_code,400)
        self.assertEqual(UploadEvent.objects.get().status,'failed')
        batch=ImportBatch.objects.create(client=self.tenant,source='lag',filename='lag.xlsx',report_date=date(2026,11,1),status='processed',processed_count=9)
        for duplicate in (False,True):
            with patch('claims.views.import_lag',return_value=(batch,duplicate)):
                response=self.http.post(url,{'file':SimpleUploadedFile('lag.xlsx',b'test'),'password':'SECRET'})
                self.assertEqual(response.status_code,200 if duplicate else 201)
        self.assertEqual(list(UploadEvent.objects.order_by('id').values_list('status',flat=True)),['failed','uploaded','duplicate'])
        self.assertNotIn('SECRET',str(list(UploadEvent.objects.values())))

    def test_dashboard_uses_tpa_and_includes_exactly_60(self):
        batch=ImportBatch.objects.create(client=self.tenant,source='lag',filename='lag.xlsx',report_date=date(2026,11,1),status='processed')
        for number,tpa,total in [('A',59,200),('B',60,60),('C',61,61)]:
            claim=Claim.objects.create(client=self.tenant,claim_number=number,report_date=batch.report_date)
            LagClaimSnapshot.objects.create(client=self.tenant,batch=batch,claim=claim,row_number=ord(number),sheet_name='LOCAL CLMS',row_data={'_snapshot':{'claim_number':number,'tpa_age':tpa,'total_age':total}})
        data=self.http.get('/api/dashboard/?client=WGS&lag_date=2026-11-01').json()
        self.assertEqual(data['over_60'],2)
        self.assertEqual([r['claim_number'] for r in data['attention']],['C','B','A'])

    def test_backfill_restores_available_history_without_duplicate_scan_files(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from .models import ReconFile
        batch=ImportBatch.objects.create(client=self.tenant,source='lag',filename='old-lag.xlsx',report_date=date(2026,11,1),status='processed')
        file=EdiFile.objects.create(client=self.tenant,kind='837',path='/MUSGROW/a.837',filename='a.837',fingerprint='a',bytes_size=1,modified_ns=1)
        run=FolderScanRun.objects.create(client=self.tenant)
        FolderScanItem.objects.create(run=run,kind='837',filename='a.837',path=file.path,status='indexed',edi_file=file)
        ReconFile.objects.create(client=self.tenant,sub_client='MUSGROVE',path='/r.csv',filename='r.csv',status='indexed',row_count=4)
        UploadEvent.objects.all().delete()
        migration=import_module('claims.migrations.0015_backfill_upload_history')
        from types import SimpleNamespace
        editor=SimpleNamespace(connection=connection)
        migration.backfill(apps,editor)
        migration.backfill(apps,editor)
        self.assertEqual(UploadEvent.objects.count(),3)
        self.assertEqual(UploadEvent.objects.filter(kind='837').count(),1)
        self.assertEqual(UploadEvent.objects.get(kind='rec_mus').record_count,4)

    def test_wgs_837_scan_accepts_dated_musgrove_file_and_links_exact_claim(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from .models import FolderScanSchedule, FileSourceRule, EdiClaimIndex
        from .folder_processing import queue_scan, process_client
        from .tests import fake_edi
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for kind in ('837','835','error_reports'):
                (root/'WGS'/kind).mkdir(parents=True)
            source=root/'WGS'/'837'/'HCP_5010_MUSGROVE_mcsjaa1114202605448370000.837'
            source.write_bytes(fake_edi(['ST*837*1','CLM*PROVIDER-ID*100','REF*9C*26213BK2702-   -000','SE*4*1']))
            os.utime(source,(1_700_000_000,1_700_000_000))
            claim=Claim.objects.create(client=self.tenant,claim_number='26213BK2702',report_date=date(2026,11,14))
            FolderScanSchedule.objects.create(client=self.tenant)
            with self.settings(EDI_ROOT=root,EDI_ALLOWED_ROOTS=[str(root)]):
                run,_=queue_scan(self.tenant)
                process_client(self.tenant)
            run.refresh_from_db()
            self.assertEqual(run.processed,1)
            self.assertEqual(run.failed,0)
            self.assertEqual(EdiDocument.objects.get(claim=claim).kind,'837')
            event=UploadEvent.objects.get()
            self.assertEqual(event.kind,'837')
            self.assertEqual(event.sub_client,'MUSGROVE')

    def test_missing_configured_subfolder_becomes_visible_failure(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from .models import FolderScanSchedule, FileSourceRule
        from .folder_processing import queue_scan, process_client
        with TemporaryDirectory() as folder:
            root=Path(folder)
            for kind in ('837','835','error_reports'):
                (root/'WGS'/kind).mkdir(parents=True)
            FileSourceRule.objects.create(client=self.tenant,file_type='837',folder_path=str(root/'WGS'/'837'),include_subfolders='MUSGROVE',extensions='.837')
            FolderScanSchedule.objects.create(client=self.tenant)
            with self.settings(EDI_ROOT=root,EDI_ALLOWED_ROOTS=[str(root)]):
                run,_=queue_scan(self.tenant)
                process_client(self.tenant)
            run.refresh_from_db()
            self.assertEqual(run.failed,1)
            self.assertIn('MUSGROVE',UploadEvent.objects.get(status='failed').message)
