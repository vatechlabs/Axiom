import io,os
from datetime import datetime,date,time,timezone as dt_timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase,override_settings,Client as HttpClient
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from openpyxl import Workbook
from .models import (Client,Membership,FolderScanSchedule,FolderScanRun,FolderScanItem,
                     EdiDocument,Claim,ImportBatch,ErrorObservation,ErrorHistory)
from .folder_processing import queue_scan,process_client,due_today
from .importers import import_lag
from .tests import fake_edi

class FolderProcessingTests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.tenant=Client.objects.create(code='WGS',name='WGS')
        for name in ('error_reports','835','837'):(self.root/'WGS'/name).mkdir(parents=True)
        self.settings=override_settings(EDI_ROOT=self.root,EDI_ALLOWED_ROOTS=[str(self.root)])
        self.settings.enable();self.addCleanup(self.settings.disable)
        self.schedule=FolderScanSchedule.objects.create(client=self.tenant)
        self.user=get_user_model().objects.create_user(username='schedule-test',password='test')
        self.membership=Membership.objects.create(user=self.user,client=self.tenant,role='admin')
        self.token=Token.objects.create(user=self.user)
        self.http=HttpClient(HTTP_AUTHORIZATION='Token '+self.token.key)

    def write(self,kind,name,segments,settled=True):
        path=self.root/'WGS'/kind/name;path.write_bytes(fake_edi(segments))
        if settled:os.utime(path,(1_700_000_000,1_700_000_000))
        return path

    def test_schedule_days_time_timezone_and_once_per_date(self):
        self.schedule.enabled=True;self.schedule.weekdays=[0];self.schedule.daily_time=time(18,0);self.schedule.timezone_name='Asia/Kolkata'
        # Monday 18:00 in Kolkata, 12:30 UTC.
        now=datetime(2026,10,26,12,30,tzinfo=dt_timezone.utc)
        self.assertFalse(due_today(self.schedule,now.replace(minute=29)))
        self.assertTrue(due_today(self.schedule,now))
        self.schedule.last_scheduled_date=date(2026,10,26)
        self.assertFalse(due_today(self.schedule,now))
        self.schedule.last_scheduled_date=None;self.schedule.weekdays=[1]
        self.assertFalse(due_today(self.schedule,now))

    def test_manual_empty_run_and_duplicate_queue(self):
        run,created=queue_scan(self.tenant)
        again,created_again=queue_scan(self.tenant)
        self.assertEqual(run.pk,again.pk);self.assertFalse(created_again)
        self.assertTrue(process_client(self.tenant))
        run.refresh_from_db()
        self.assertEqual(run.status,'completed');self.assertEqual(run.message,'No new files found.')
        self.assertFalse(process_client(self.tenant))

    def test_scan_then_lag_links_previously_indexed_835_and_837(self):
        self.write('837','claim.837',['ST*837*1','CLM*PROVIDER-ID*100','REF*9C*26213BK2702-   -000','SE*4*1'])
        self.write('835','payment.835',['ST*835*1','CLP*PROVIDER-ID*1*100*80**12*2026213BK270280','SE*3*1'])
        run,_=queue_scan(self.tenant);process_client(self.tenant);run.refresh_from_db()
        self.assertEqual(run.processed,2);self.assertEqual(run.failed,0)
        self.assertEqual(EdiDocument.objects.count(),0)
        before=self.http.get('/api/processing/status/?client=WGS').json()
        self.assertTrue(all(item['matched_claims']==0 for item in before['items']))
        # Upload the inventory AFTER the EDI sources, without reindexing either source.
        wb=Workbook();wb.remove(wb.active)
        for name in ('LOCAL CLMS','LOCAL ADJ','ITS CLMS','ITS ADJ'):
            ws=wb.create_sheet(name);ws.append(['TOTAL AGE','CLAIM NUMBER','DCN','TPA','TPA AGE','X','Y','Z','Fund'])
            if name=='LOCAL CLMS':ws.append([30,'26213BK2702','','TPA',5,'','','','Fund A'])
        buffer=io.BytesIO();wb.save(buffer)
        import_lag(self.tenant,buffer.getvalue(),'lag.xlsx',date(2026,8,3),user=self.user)
        self.assertEqual(EdiDocument.objects.filter(kind='837').count(),1)
        self.assertEqual(EdiDocument.objects.filter(kind='835').count(),1)
        after=self.http.get('/api/processing/status/?client=WGS').json()
        self.assertTrue(all(item['matched_claims']==1 for item in after['items']))
        self.assertGreater(after['revision'],before['revision'])
        # No-change runs skip sources and do not call reconciliation again.
        run,_=queue_scan(self.tenant)
        with patch('claims.folder_processing.reconcile_client') as reconcile:
            process_client(self.tenant);reconcile.assert_not_called()
        run.refresh_from_db();self.assertEqual(run.skipped,2);self.assertEqual(run.processed,0)

    def test_scan_reconciles_new_errors_once(self):
        path=self.root/'WGS'/'error_reports'/'errors.txt';path.write_bytes(b'fixture');os.utime(path,(1_700_000_000,1_700_000_000))
        claim=Claim.objects.create(client=self.tenant,claim_number='26213BK2702',report_date=date(2026,8,3))
        record={'line':1,'dcn':'26213BK2702','claim_submitter_id':'Provider-ID','report_date':date(2026,8,2),'message':'Verified error','reject_code':'X'}
        run,_=queue_scan(self.tenant)
        with patch('claims.importers.parse_error_txt',return_value=([record],date(2026,8,2))):process_client(self.tenant)
        self.assertEqual(ErrorHistory.objects.get(claim=claim).message,'Verified error')
        run.refresh_from_db();self.assertEqual(run.processed,1)

    def test_recent_and_failed_files_visible_and_retry(self):
        path=self.write('837','late.837',['ST*837*1','CLM*X*100','SE*3*1'],settled=False)
        run,_=queue_scan(self.tenant);process_client(self.tenant);run.refresh_from_db()
        self.assertEqual(run.deferred,1);self.assertEqual(run.items.get().status,'deferred')
        os.utime(path,(1_700_000_000,1_700_000_000))
        bad=self.root/'WGS'/'837'/'bad.837';bad.write_bytes(b'invalid');os.utime(bad,(1_700_000_000,1_700_000_000))
        run,_=queue_scan(self.tenant);process_client(self.tenant);run.refresh_from_db()
        self.assertEqual(run.processed,1);self.assertEqual(run.failed,1);self.assertEqual(run.status,'partial')
        bad.unlink();run,_=queue_scan(self.tenant);process_client(self.tenant);run.refresh_from_db();self.assertEqual(run.status,'completed')

    def test_schedule_api_permissions_validation_and_client_scope(self):
        data={'enabled':True,'time':'18:15','days':[0,2,4],'timezone':'Asia/Kolkata'}
        self.assertEqual(self.http.put('/api/processing/schedule/?client=WGS',data=data,content_type='application/json').status_code,200)
        bad={**data,'days':[]}
        self.assertEqual(self.http.put('/api/processing/schedule/?client=WGS',data=bad,content_type='application/json').status_code,400)
        self.membership.role='viewer';self.membership.save()
        self.assertEqual(self.http.post('/api/processing/run/?client=WGS').status_code,403)
        self.assertEqual(self.http.put('/api/processing/schedule/?client=WGS',data=data,content_type='application/json').status_code,403)
        self.assertEqual(self.http.get('/api/processing/status/?client=OTHER').status_code,403)
        # A brand new schedule also serializes its default TimeField correctly.
        self.schedule.delete()
        response=self.http.get('/api/processing/schedule/?client=WGS')
        self.assertEqual(response.status_code,200);self.assertFalse(response.json()['schedule']['enabled'])

    def test_interrupted_reconciliation_retries_from_existing_index(self):
        self.schedule.needs_reconcile=True;self.schedule.save()
        run=FolderScanRun.objects.create(client=self.tenant,status='running')
        with patch('claims.folder_processing.reconcile_client') as reconcile:
            process_client(self.tenant);reconcile.assert_called_once()
        run.refresh_from_db();self.assertEqual(run.status,'failed')
        self.assertEqual(FolderScanRun.objects.filter(trigger='recovery',status='completed').count(),1)

    def test_worker_scheduled_run_once_on_selected_day(self):
        self.schedule.enabled=True
        self.schedule.daily_time=time(18,0)
        self.schedule.weekdays=[0]
        self.schedule.timezone_name='Asia/Kolkata'
        self.schedule.save()
        moment=datetime(2026,10,26,12,30,tzinfo=dt_timezone.utc)
        self.assertTrue(process_client(self.tenant,now=moment))
        self.assertFalse(process_client(self.tenant,now=moment))
        self.assertEqual(FolderScanRun.objects.filter(trigger='scheduled').count(),1)
        self.assertFalse(process_client(self.tenant,now=datetime(2026,10,27,12,30,tzinfo=dt_timezone.utc)))
