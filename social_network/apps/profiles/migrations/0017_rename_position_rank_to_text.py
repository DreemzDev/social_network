"""Первый шаг перевода должности и звания на справочники.

Старые текстовые поля не удаляются сразу, а переименовываются: из них
следующая миграция соберёт сами справочники, а потом уже уберёт за собой
(0020). Переименование, а не «удалить и создать», потому что данные в них
и есть единственный источник будущего справочника.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0016_notification_url_alter_notification_kind'),
    ]

    operations = [
        migrations.RenameField(model_name='user', old_name='position', new_name='position_text'),
        migrations.RenameField(model_name='user', old_name='rank', new_name='rank_text'),
    ]
