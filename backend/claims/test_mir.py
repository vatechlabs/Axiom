import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, override_settings
from .models import Client, Claim, FileSourceRule, FolderScanRun, FolderScanSchedule, MirRecord, MirFile, ImportBatch, LagClaimSnapshot
from .mir_processing import parse_mir, scan_mir_files, relink_mir
from .querying import highmark_snapshot_query, rows_for
from .serializers import ClaimDetailSerializer


def record(identifier='12345678901234567', prefix='MI', error='UE002'):
    line = bytearray(b' ' * 675)
    line[:2] = prefix.encode()
    line[2:19] = identifier.encode()
    line[207:217] = error.ljust(10).encode()
    return bytes(line)


class MirTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(code='Highmark', name='Highmark')
        self.claim = Claim.objects.create(client=self.client_obj, claim_number='12345678901234567',
                                          fund='ABC', report_date=date(2026, 11, 3))
        FolderScanSchedule.objects.create(client=self.client_obj)
        self.run = FolderScanRun.objects.create(client=self.client_obj)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'MIROUT261102.PA7'
        self.rule = FileSourceRule.objects.create(client=self.client_obj, file_type='mir_received',
            folder_path=str(self.folder), starts_with='MIROUT', extensions='.PA7')

    def scan(self):
        os.utime(self.path, (1000, 1000))
        with override_settings(EDI_ALLOWED_ROOTS=[str(self.folder)]), patch('claims.mir_processing.connect_for_edi_path'):
            return scan_mir_files(self.client_obj, self.run)

    def test_ingest_repeat_changed_and_display(self):
        self.path.write_bytes(record())
        self.assertEqual(self.scan()['processed'], 1)
        self.assertEqual(self.scan()['skipped'], 1)
        row = MirRecord.objects.get()
        self.assertEqual(row.claim_id, self.claim.pk)
        self.assertEqual(row.error_text, 'UE002')
        self.assertEqual(ClaimDetailSerializer(self.claim).data['mir_records'][0]['error_text'], 'UE002')
        batch = ImportBatch.objects.create(client=self.client_obj, source='lag', filename='test',
            status='processed', report_date=date(2026,11,3))
        LagClaimSnapshot.objects.create(client=self.client_obj,batch=batch,claim=self.claim,row_number=1,
            sheet_name='Daily',row_data={'_snapshot':{'claim_number':self.claim.claim_number,'fund':'ABC'}})
        self.assertEqual(rows_for(highmark_snapshot_query(self.client_obj),None)[0]['error_message'],'UE002')
        self.path.write_bytes(record(error=''))
        self.scan()
        self.assertEqual(MirRecord.objects.count(), 1)
        self.assertEqual(rows_for(highmark_snapshot_query(self.client_obj),None)[0]['error_message'],'')
        self.path.write_bytes(b'MIshort')
        self.assertEqual(self.scan()['failed'], 1)
        self.assertEqual(MirRecord.objects.count(), 1)

    def test_late_claim_and_ambiguous_match(self):
        self.claim.delete()
        self.path.write_bytes(record())
        self.scan()
        self.assertIsNone(MirRecord.objects.get().claim_id)
        claim = Claim.objects.create(client=self.client_obj,claim_number='12345678901234567',
                                     report_date=date(2026,11,3),fund='ABC')
        relink_mir(self.client_obj)
        self.assertEqual(MirRecord.objects.get().claim_id,claim.pk)
        Claim.objects.create(client=self.client_obj,claim_number='OTHER',dcn=claim.claim_number,
                             report_date=date(2026,11,3),fund='ABC')
        relink_mir(self.client_obj)
        self.assertIsNone(MirRecord.objects.get().claim_id)

    def test_cpt_scope_and_control_records(self):
        self.path.write_bytes(record(prefix='MO'))
        self.rule.file_type='mir_cpt'
        self.rule.save()
        self.scan()
        self.assertIsNone(MirRecord.objects.get().claim_id)
        self.claim.fund='CPT'
        self.claim.save()
        relink_mir(self.client_obj)
        self.assertEqual(MirRecord.objects.get().claim_id,self.claim.pk)
        rows, controls = parse_mir(record()+b'\n'+b'\x9f\x00control',True)
        self.assertEqual((len(rows),controls),(1,1))
        with self.assertRaises(ValueError):
            parse_mir(b'MIshort',True)

    def test_cpt_clp01_links_through_existing_835_document(self):
        from .models import EdiFile, EdiClaimIndex, EdiDocument
        self.claim.claim_number = 'SCCF123'
        self.claim.fund = 'CPT'
        self.claim.save()
        edi = EdiFile.objects.create(client=self.client_obj,kind='835',path='sample.x12',
            filename='sample.x12',fingerprint='test',bytes_size=10,modified_ns=1)
        index = EdiClaimIndex.objects.create(edi_file=edi,claim_identifier='12345678901234567',
                                            identifier_type='submitter')
        EdiDocument.objects.create(claim=self.claim,edi_file=edi,edi_index=index,kind='835',
                                   original_name='sample.x12')
        self.rule.file_type = 'mir_cpt'
        self.rule.save()
        self.path.write_bytes(record(prefix='MO'))
        self.scan()
        self.assertEqual(MirRecord.objects.get().claim_id,self.claim.pk)

    def test_normal_worker_entry_point(self):
        from .folder_processing import perform_scan
        self.path.write_bytes(record())
        os.utime(self.path,(1000,1000))
        schedule = FolderScanSchedule.objects.get(client=self.client_obj)
        with override_settings(EDI_ALLOWED_ROOTS=[str(self.folder)]), \
             patch('claims.mir_processing.connect_for_edi_path'), \
             patch('claims.folder_processing.connect_for_edi_path'), \
             patch('claims.folder_processing.scan_recon_files',return_value=dict(processed=0,skipped=0,deferred=0,failed=0)):
            perform_scan(self.run,schedule)
        self.assertTrue(self.run.items.filter(kind='mir_recv',status='indexed',indexed_count=1).exists())
        self.assertEqual(MirRecord.objects.get().claim_id,self.claim.pk)
