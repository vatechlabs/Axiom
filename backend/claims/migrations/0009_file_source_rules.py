from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('claims','0008_recon_records')]
    operations=[migrations.CreateModel(name='FileSourceRule',fields=[
      ('id',models.BigAutoField(primary_key=True,serialize=False)),
      ('file_type',models.CharField(max_length=30)),('folder_path',models.TextField()),
      ('starts_with',models.CharField(max_length=500,blank=True)),
      ('ends_with',models.CharField(max_length=500,blank=True)),
      ('extensions',models.CharField(max_length=500,blank=True)),
      ('recursive',models.BooleanField(default=False)),
      ('include_subfolders',models.TextField(blank=True)),
      ('exclude_subfolders',models.TextField(blank=True)),
      ('enabled',models.BooleanField(default=True)),
      ('client',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='file_source_rules',to='claims.client'))
    ],options={'indexes':[models.Index(fields=['client','file_type','enabled'],name='file_rule_client_idx')]})]
