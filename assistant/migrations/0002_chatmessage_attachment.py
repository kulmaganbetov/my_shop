# Generated migration for attachment fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assistant', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='attachment',
            field=models.FileField(blank=True, null=True, upload_to='chat_attachments/', verbose_name='Вложение'),
        ),
        migrations.AddField(
            model_name='chatmessage',
            name='attachment_type',
            field=models.CharField(blank=True, max_length=50, verbose_name='Тип вложения'),
        ),
    ]
