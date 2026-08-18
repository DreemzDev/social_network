"""Снимает дату добавления у справочников, заведённых до появления подписей.

`created_at` с auto_now_add проставился всем существующим строкам моментом
самой миграции 0004 — то есть страница показывала бы дату добавления,
которой не было. Правило простое: не знаем, кто добавил, — не знаем и когда.
"""
from django.db import migrations


def clear_backfilled_dates(apps, schema_editor):
    Phonebook = apps.get_model('phonebook', 'Phonebook')
    Phonebook.objects.filter(created_by__isnull=True).update(created_at=None)


def noop(apps, schema_editor):
    """Обратно восстанавливать нечего: даты и не было."""


class Migration(migrations.Migration):

    dependencies = [
        ('phonebook', '0004_phonebook_audit_and_trash'),
    ]

    operations = [
        migrations.RunPython(clear_backfilled_dates, noop),
    ]
