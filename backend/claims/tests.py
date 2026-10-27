from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from django.test import TestCase, override_settings
from .models import Client, Claim, EdiClaimIndex, EdiDocument, ErrorObservation, ErrorHistory, ImportBatch
from .importers import index_edi, reconcile_client
from .edi_excerpt import extract_claim_excerpt

# Small, valid-length synthetic X12 ISA; production samples must still be independently certified.
def fake_edi(segments):
    isa = b'ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       *260101*1200*^*00501*000000001*0*T*:'
    assert len(isa)==105, len(isa)
    return isa+b'~'+b'~'.join(x.encode('ascii') for x in segments)+b'~'

class OccurrenceTests(TestCase):
    def setUp(self):
        self.c=Client.objects.create(code='WGS',name='WGS')
        self.c2=Client.objects.create(code='HI',name='Highmark')
        self.claim=Claim.objects.create(client=self.c,claim_number='SUB100',dcn='DCN100',report_date=date(2026,1,3))
    def test_errors_retain_all_dates_and_are_tenant_scoped(self):
        for day,msg in ((1,'First error'),(2,'Second error')):
            batch=ImportBatch.objects.create(client=self.c,source='error',filename=f'{day}.txt',report_date=date(2026,1,day))
            ErrorObservation.objects.create(client=self.c,batch=batch,dcn='DCN100',claim_submitter_id='SUB100',report_date=date(2026,1,day),message=msg,source_line=day)
        reconcile_client(self.c)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.error_message,'Second error')
        self.assertEqual(list(ErrorHistory.objects.filter(claim=self.claim).values_list('message',flat=True)),['Second error','First error'])
        self.assertFalse(Claim.objects.filter(client=self.c2).exists())
    def test_multiple_edi_occurrences_in_same_file_and_preview(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'sample.837'
            p.write_bytes(fake_edi(['ST*837*001','CLM*SUB100*100','REF*D9*ONE','CLM*OTHER*100','REF*D9*OTHER','CLM*SUB100*200','REF*D9*TWO','SE*8*001']))
            index_edi(self.c,p,'837')
            reconcile_client(self.c)
            docs=list(EdiDocument.objects.filter(claim=self.claim).select_related('edi_index').order_by('edi_index__segment_number'))
            self.assertEqual(len(docs),2)
            a=extract_claim_excerpt(p,docs[0].edi_index,'837')
            z=extract_claim_excerpt(p,docs[1].edi_index,'837')
            self.assertIn(b'ONE',a)
            self.assertNotIn(b'OTHER',a)
            self.assertIn(b'TWO',z)
            self.assertEqual(EdiClaimIndex.objects.filter(edi_file__client=self.c2).count(),0)
            obj,skipped=index_edi(self.c,p,'837')
            self.assertTrue(skipped)

class DailyLagSnapshotTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from rest_framework.authtoken.models import Token
        from .models import Membership,LagClaimSnapshot
        self.client=Client.objects.create(code='WGS',name='WGS')
        self.user=get_user_model().objects.create_user(username='snapshot_test',password='test-password-long-123')
        Membership.objects.create(user=self.user,client=self.client,role='admin')
        self.token=Token.objects.create(user=self.user)
        self.b1=ImportBatch.objects.create(client=self.client,source='lag',filename='day1.xlsx',report_date=date(2026,8,1),status='processed')
        self.b2=ImportBatch.objects.create(client=self.client,source='lag',filename='day2.xlsx',report_date=date(2026,8,2),status='processed')
        self.claim=Claim.objects.create(client=self.client,claim_number='CLAIM-1',report_date=date(2026,8,2),fund='New fund',tpa_age=8,total_age=10,lag_batch=self.b2)
        LagClaimSnapshot.objects.create(client=self.client,batch=self.b1,claim=self.claim,row_number=2,sheet_name='LOCAL CLMS',row_data={'_snapshot':{'claim_number':'CLAIM-1','fund':'Old fund','tpa_age':2,'total_age':3}})
        LagClaimSnapshot.objects.create(client=self.client,batch=self.b2,claim=self.claim,row_number=2,sheet_name='LOCAL CLMS',row_data={'_snapshot':{'claim_number':'CLAIM-1','fund':'New fund','tpa_age':8,'total_age':10}})
    def test_historical_lag_is_not_overwritten_by_current_claim(self):
        from .views import snapshot_rows
        class R:
            query_params={}
        self.assertEqual(snapshot_rows(R(),self.client,self.b1)[0]['fund'],'Old fund')
        self.assertEqual(snapshot_rows(R(),self.client,self.b1)[0]['tpa_age'],2)
        self.assertEqual(snapshot_rows(R(),self.client,self.b2)[0]['fund'],'New fund')
    def test_dashboard_clickable_group_counts_are_day_scoped(self):
        headers={'HTTP_AUTHORIZATION':'Token '+self.token.key}
        resp=self.client_api('/api/dashboard/?client=WGS&lag_date=2026-08-01',headers)
        self.assertEqual(resp.status_code,200)
        self.assertEqual(resp.json()['fund_counts'][0]['fund'],'Old fund')
    def client_api(self,path,headers):
        from django.test import Client as HttpClient
        return HttpClient().get(path,**headers)

    def test_claims_pagination_and_inclusive_20_day_age_filter(self):
        from .models import LagClaimSnapshot
        from django.test import Client as HttpClient
        for i in range(25):
            claim=Claim.objects.create(client=self.client,claim_number=f'PAGE-{i:03d}',report_date=date(2026,8,2))
            LagClaimSnapshot.objects.create(client=self.client,batch=self.b2,claim=claim,row_number=i+3,sheet_name='LOCAL CLMS',row_data={'_snapshot':{'claim_number':claim.claim_number,'fund':'Some fund','tpa_age':20 if i<20 else 19,'total_age':i+2}})
        headers={'HTTP_AUTHORIZATION':'Token '+self.token.key}
        base='/api/claims/?client=WGS&lag_date=2026-08-02&tpa_age_min=20&page_size=10'
        first=HttpClient().get(base+'&page=1',**headers)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['count'],20)
        self.assertEqual(len(first.json()['results']),10)
        second=HttpClient().get(base+'&page=2',**headers)
        self.assertEqual(len(second.json()['results']),10)
        ids=[r['id'] for r in first.json()['results']+second.json()['results']]
        self.assertEqual(len(ids),len(set(ids)))
        invalid=HttpClient().get(base+'&page_size=999',**headers)
        self.assertEqual(invalid.status_code,400)
