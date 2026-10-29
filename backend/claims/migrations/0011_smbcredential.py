from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [('claims', '0010_alter_filesourcerule_id_alter_reconfile_id_and_more')]
    operations = [
        migrations.CreateModel(
            name='SmbCredential',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('share_path', models.CharField(max_length=255)),
                ('username', models.CharField(max_length=255)),
                ('password_ciphertext', models.BinaryField()),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('client', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='smb_credential', to='claims.client')),
            ],
        ),
    ]
