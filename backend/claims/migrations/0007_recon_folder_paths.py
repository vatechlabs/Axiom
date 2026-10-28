from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[('claims','0006_clientfolderconfig_windows_835_path_and_more')]
    operations=[
        migrations.AddField(model_name='clientfolderconfig',name='windows_recon_path',field=models.TextField(blank=True)),
        migrations.AddField(model_name='clientfolderconfig',name='recon_path',field=models.TextField(blank=True)),
    ]
