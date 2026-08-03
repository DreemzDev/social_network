import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('storage', '0001_initial'),
        ('posts', '0004_alter_post_photo_postfile_postimage'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='postfile',
            name='file',
        ),
        migrations.RemoveField(
            model_name='postfile',
            name='original_name',
        ),
        migrations.AddField(
            model_name='postfile',
            name='file_object',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='storage.fileobject',
                verbose_name='Файл',
                # Таблица posts_postfile пуста в новой Postgres-базе — прямая
                # замена поля без сохранения данных (ARCHITECTURE.md,
                # раздел 11: миграция без данных = просто перевести модель).
                default=None,
                null=True,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='postfile',
            name='file_object',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='storage.fileobject',
                verbose_name='Файл',
            ),
        ),
    ]
