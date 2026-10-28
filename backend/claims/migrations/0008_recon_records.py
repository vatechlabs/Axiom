from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies = [('claims', '0007_recon_folder_paths')]
    operations = [
        migrations.AddField(model_name='clientfolderconfig', name='windows_recon_musgrove_path', field=models.TextField(blank=True)),
        migrations.AddField(model_name='clientfolderconfig', name='windows_recon_abc_path', field=models.TextField(blank=True)),
        migrations.CreateModel(name='ReconFile', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('sub_client', models.CharField(blank=True, default='', max_length=24)),
            ('path', models.TextField()),
            ('filename', models.CharField(max_length=255)),
            ('fingerprint', models.CharField(blank=True, max_length=64)),
            ('size', models.BigIntegerField(default=0)),
            ('modified_ns', models.BigIntegerField(default=0)),
            ('status', models.CharField(default='pending', max_length=20)),
            ('row_count', models.PositiveIntegerField(default=0)),
            ('error', models.TextField(blank=True)),
            ('indexed_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recon_files', to='claims.client')),
        ], options={'indexes':[models.Index(fields=['client','sub_client','indexed_at'], name='recon_client_sub_dt_idx')], 'constraints':[models.UniqueConstraint(fields=['client','sub_client','path'], name='uniq_recon_file_location')]}),
        migrations.CreateModel(name='ReconRecord', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('row_number', models.PositiveIntegerField()),
            ('claim_number', models.CharField(blank=True, db_index=True, max_length=100)),
            ('claim_date', models.DateField(blank=True, null=True)),
            ('raw_data', models.JSONField(default=dict)),
            ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='claims.reconfile')),
        ], options={'indexes':[models.Index(fields=['claim_date','claim_number'], name='recon_date_claim_idx')], 'constraints':[models.UniqueConstraint(fields=['source','row_number'], name='uniq_recon_file_row')]}),
    ]
