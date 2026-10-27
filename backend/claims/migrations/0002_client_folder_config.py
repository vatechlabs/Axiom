from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('claims', '0001_initial')]
    operations = [migrations.CreateModel(name='ClientFolderConfig',fields=[
      ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
      ('error_reports_path',models.TextField(blank=True)),
      ('edi_835_path',models.TextField(blank=True)),
      ('edi_837_path',models.TextField(blank=True)),
      ('updated_at',models.DateTimeField(auto_now=True)),
      ('client',models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,related_name='folder_config',to='claims.client')),
    ])]
