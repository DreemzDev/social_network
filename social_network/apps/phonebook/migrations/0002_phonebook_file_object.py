import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0001_initial'),
        ('phonebook', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='phonebook',
            name='book',
        ),
        migrations.AddField(
            model_name='phonebook',
            name='file_object',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='storage.fileobject',
                verbose_name='Справочник',
            ),
        ),
    ]
