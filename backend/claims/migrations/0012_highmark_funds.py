from django.db import migrations, models
import django.db.models.deletion



def seed_highmark_fund_mappings(apps, schema_editor):
    Client=apps.get_model('claims','Client')
    FundMap=apps.get_model('claims','HighmarkFundMap')
    defaults={'J7':'L152','J6':'OEW','J5':'CPT','T5':'AMO'}
    for client in Client.objects.filter(code__in=['Highmark','HIGHMARK','HMRK','HM']):
        for prefix,fund in defaults.items():
            FundMap.objects.get_or_create(client=client,prefix=prefix,defaults={'fund':fund})

class Migration(migrations.Migration):
    dependencies = [('claims','0011_smbcredential')]
    operations = [
        migrations.CreateModel(name='HighmarkFundMap',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('prefix',models.CharField(max_length=12)),('fund',models.CharField(max_length=100)),
            ('client',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='claims.client')),
        ]),
        migrations.AddField(model_name='importbatch',name='report_type',field=models.CharField(blank=True,default='',max_length=32)),
        migrations.AddField(model_name='importbatch',name='content_sha256',field=models.CharField(blank=True,db_index=True,default='',max_length=64)),
        migrations.AddConstraint(model_name='highmarkfundmap',constraint=models.UniqueConstraint(fields=('client','prefix'),name='uniq_hm_fund_prefix')),
        migrations.RunPython(seed_highmark_fund_mappings, migrations.RunPython.noop),
    ]
