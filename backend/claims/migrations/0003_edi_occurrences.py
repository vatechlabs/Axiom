from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('claims', '0002_client_folder_config')]
    operations = [
        migrations.AddField(model_name='ediclaimindex',name='start_byte',field=models.BigIntegerField(blank=True,null=True)),
        migrations.AddField(model_name='edidocument',name='edi_index',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.CASCADE,related_name='linked_documents',to='claims.ediclaimindex')),
        migrations.RemoveConstraint(model_name='edidocument',name='unique_claim_edi'),
        migrations.AddConstraint(model_name='edidocument',constraint=models.UniqueConstraint(fields=('claim','edi_index'),condition=models.Q(edi_index__isnull=False),name='unique_claim_edi_occurrence')),
    ]
