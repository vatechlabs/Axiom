from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('claims', '0003_edi_occurrences')]
    operations = [
        migrations.AddIndex(model_name='errorhistory', index=models.Index(fields=['claim','-report_date','-id'], name='err_claim_date_latest')),
        migrations.AddIndex(model_name='edidocument', index=models.Index(fields=['claim','kind'], name='edi_claim_kind_count')),
    ]
