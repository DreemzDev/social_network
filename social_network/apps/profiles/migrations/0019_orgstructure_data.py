"""Собирает справочники из того, что сотрудники успели вписать руками.

Должности и звания были свободным текстом, телефоны — пятью полями с
подписями в шаблонах. Всё это уже заполнено, поэтому справочники не
создаются пустыми: значения переносятся как есть, повторы схлопываются,
регистр и пробелы нормализуются («майор» и «Майор» становятся одной
записью).

Виды телефонов заводятся ровно те пять, что были вшиты в разметку, с теми
же подписями и масками ввода из base.html — иначе после миграции у людей
пропали бы подписи «ПТС»/«ЗС», а маски перестали бы применяться.

Должности переносятся плоским списком: кто кому подчиняется, из текста
вывести нельзя — это задаётся в админке уже после миграции. По той же
причине max_holders у всех 0, а assignable_by_user выключен: молча
разрешить сотрудникам менять себе должность значило бы вернуть ровно ту
проблему, ради которой справочник и заводится.
"""
from django.db import migrations

# (поле в User, подпись, маска из base.html, порядок в списке)
PHONE_FIELDS = [
    ('phone_city', 'Город', '8 (999) 999-99-99', 1),
    ('phone_hc', 'HiCom', '9-99-99', 2),
    ('phone_pts', 'ПТС', '999-99', 3),
    ('phone_9', 'АТС-9', '99-99', 4),
    ('phone_zs', 'ЗС', '999-9999', 5),
]


def build_dictionaries(apps, schema_editor):
    User = apps.get_model('profiles', 'User')
    Position = apps.get_model('profiles', 'Position')
    Rank = apps.get_model('profiles', 'Rank')
    PhoneType = apps.get_model('profiles', 'PhoneType')
    UserPhone = apps.get_model('profiles', 'UserPhone')

    positions = {}
    ranks = {}

    for user in User.objects.all():
        title = (user.position_text or '').strip()
        if title:
            key = title.lower()
            if key not in positions:
                positions[key], _ = Position.objects.get_or_create(
                    name=title, department=None, defaults={'order': 0},
                )
            user.position = positions[key]

        rank_name = (user.rank_text or '').strip()
        if rank_name:
            key = rank_name.lower()
            if key not in ranks:
                ranks[key], _ = Rank.objects.get_or_create(name=rank_name)
            user.rank = ranks[key]

        user.save(update_fields=['position', 'rank'])

    for field_name, label, mask, order in PHONE_FIELDS:
        phone_type, _ = PhoneType.objects.get_or_create(
            name=label, defaults={'mask': mask, 'order': order},
        )
        for user in User.objects.exclude(**{field_name: ''}).exclude(**{f'{field_name}__isnull': True}):
            number = (getattr(user, field_name) or '').strip()
            if number:
                UserPhone.objects.get_or_create(
                    user=user, type=phone_type, defaults={'number': number},
                )


def restore_text_fields(apps, schema_editor):
    """Обратный ход: вернуть значения в текстовые поля.

    Нужен, чтобы миграцию можно было откатить на шаг, не потеряв данные —
    сами справочники при этом остаются, их снесёт откат 0018.
    """
    User = apps.get_model('profiles', 'User')
    UserPhone = apps.get_model('profiles', 'UserPhone')

    field_by_label = {label: field_name for field_name, label, _, _ in PHONE_FIELDS}

    for user in User.objects.select_related('position', 'rank'):
        user.position_text = user.position.name if user.position_id else ''
        user.rank_text = user.rank.name if user.rank_id else ''
        for phone in UserPhone.objects.filter(user=user).select_related('type'):
            field_name = field_by_label.get(phone.type.name)
            if field_name:
                setattr(user, field_name, phone.number)
        user.save()


class Migration(migrations.Migration):

    dependencies = [
        ('profiles', '0018_orgstructure'),
    ]

    operations = [
        migrations.RunPython(build_dictionaries, restore_text_fields),
    ]
