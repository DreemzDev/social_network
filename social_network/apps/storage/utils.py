"""Общие вспомогательные функции для UI файлового менеджера — не часть
StorageService (это не про хранение файлов), но нужны одинаково всем трём
потребителям (exchange, catalog, deptdocs), поэтому вынесены сюда, а не
продублированы в каждом views.py."""
from datetime import datetime, time

from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

SORT_OPTIONS = {
    'name': 'По имени',
    '-name': 'По имени (Я-А)',
    '-uploaded_at': 'Сначала новые',
    'uploaded_at': 'Сначала старые',
    '-size': 'Сначала большие',
    'size': 'Сначала маленькие',
}

DEFAULT_SORT = '-uploaded_at'

# Размеры страницы для селектора в пагинации. Раньше селектор был чисто
# декоративным (перенесён из макета Midone как есть) — теперь он реально
# управляет paginate_by, а список здесь ограничивает, что можно прислать
# в ?per_page=, чтобы запросом нельзя было заказать страницу на 100000
# карточек и положить рендер.
PER_PAGE_OPTIONS = [12, 24, 48, 96]
DEFAULT_PER_PAGE = 24

# Что имеет смысл открывать в браузере вместо скачивания. Пункт «Просмотр»
# показывался для любого файла, включая .docx и .zip — браузер их всё равно
# скачивает или показывает мусор, то есть пункт меню врал о том, что
# произойдёт по клику.
PREVIEWABLE_EXTENSIONS = {
    'PDF',
    'PNG', 'JPG', 'JPEG', 'GIF', 'WEBP', 'BMP', 'SVG',
    'TXT', 'CSV', 'LOG', 'MD',
    'MP4', 'WEBM', 'MP3', 'WAV', 'OGG',
}


def apply_sort(queryset, sort_param, *, name_field):
    """Применяет сортировку из query-параметра ?sort= к queryset документов.

    name_field — 'title' у catalog/deptdocs, 'file_object__original_name' у
    exchange (там своего заголовка нет, имя всегда от FileObject).

    Сортировка по size идёт через file_object__blob__size: size у FileObject
    и FileBlob — python-property (см. storage/models.py), а не поле модели,
    ORDER BY по property невозможен, нужно реальное поле FileBlob.size.
    Field-нейм 'size'/'-size' — фасад для шаблона, реального поля с таким
    именем на CatalogDocument/DepartmentDocument/ExchangeFile нет.
    """
    if sort_param not in SORT_OPTIONS:
        sort_param = DEFAULT_SORT

    field_map = {
        'name': name_field,
        '-name': f'-{name_field}',
        'uploaded_at': 'uploaded_at',
        '-uploaded_at': '-uploaded_at',
        'size': 'file_object__blob__size',
        '-size': '-file_object__blob__size',
    }

    return queryset.order_by(field_map[sort_param]), sort_param


def _parse_date(value: str):
    """Принимает и 'дд.мм.гггг' (то, что человек печатает руками, и что
    стоит placeholder'ом в форме), и 'гггг-мм-дд' (то, что присылает
    <input type="date">). Мусор игнорируется молча: фильтр — вспомогательный
    инструмент, ронять из-за него список 500-й ошибкой незачем."""
    value = (value or '').strip()
    if not value:
        return None

    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_megabytes(value: str):
    value = (value or '').strip().replace(',', '.')
    if not value:
        return None
    try:
        megabytes = float(value)
    except ValueError:
        return None
    return int(megabytes * 1024 * 1024) if megabytes >= 0 else None


def apply_filters(queryset, params, *, name_field):
    """Расширенный фильтр панели поиска: имя, кто загрузил, диапазон дат,
    диапазон размеров.

    Раньше эти поля существовали только в разметке (перенесены из макета
    Midone вместе с кнопками «Найти»/«Сбросить», которые ни к чему не были
    привязаны). Контрол, который выглядит рабочим и ничего не делает, хуже
    отсутствующего, поэтому фильтр либо работает, либо его быть не должно.

    Возвращает (queryset, active_filters) — второй элемент нужен шаблону,
    чтобы показать, что фильтр применён, и предложить сбросить.
    """
    active = {}

    query = (params.get('q') or '').strip()
    if query:
        queryset = queryset.filter(**{f'{name_field}__icontains': query})
        active['q'] = query

    uploader = (params.get('uploader') or '').strip()
    if uploader:
        queryset = queryset.filter(
            Q(uploaded_by__last_name__icontains=uploader)
            | Q(uploaded_by__first_name__icontains=uploader)
            | Q(uploaded_by__username__icontains=uploader)
        )
        active['uploader'] = uploader

    date_from = _parse_date(params.get('date_from'))
    if date_from:
        queryset = queryset.filter(
            uploaded_at__gte=timezone.make_aware(datetime.combine(date_from, time.min))
        )
        active['date_from'] = params.get('date_from')

    date_to = _parse_date(params.get('date_to'))
    if date_to:
        queryset = queryset.filter(
            uploaded_at__lte=timezone.make_aware(datetime.combine(date_to, time.max))
        )
        active['date_to'] = params.get('date_to')

    size_min = _parse_megabytes(params.get('size_min'))
    if size_min:
        queryset = queryset.filter(file_object__blob__size__gte=size_min)
        active['size_min'] = params.get('size_min')

    size_max = _parse_megabytes(params.get('size_max'))
    if size_max:
        queryset = queryset.filter(file_object__blob__size__lte=size_max)
        active['size_max'] = params.get('size_max')

    return queryset, active


def folder_ancestors(folder) -> list:
    """Путь от корня до folder включительно — для хлебных крошек.

    Раньше крошка показывала только текущую папку: в дереве вроде
    «Кадры → Приказы → Приказы 2026» понять, где ты находишься, было
    нельзя, а вернуться — только сразу в корень.
    """
    chain = []
    node = folder
    seen = set()
    while node is not None and node.pk not in seen:
        seen.add(node.pk)
        chain.append(node)
        node = node.parent
    return list(reversed(chain))


def build_folder_choices(folders) -> list:
    """Плоский список папок с отступами по уровню вложенности — для
    <select> в модалке перемещения.

    Плоский список без отступов не давал понять структуру (две папки
    «Приказы» в разных разделах выглядели одинаково), а полноценное дерево
    ради разового выбора избыточно. Порядок и отступы считаются в Python
    одним проходом: рекурсивный SQL ради десятков папок не нужен, а
    вычислять глубину для каждой папки отдельным запросом — тем более.
    """
    folders = list(folders)
    children = {}
    for folder in folders:
        children.setdefault(folder.parent_id, []).append(folder)

    known_ids = {folder.pk for folder in folders}
    result = []

    def walk(parent_id, depth):
        for folder in sorted(children.get(parent_id, []), key=lambda f: f.name.lower()):
            folder.indented_name = ('— ' * depth) + folder.name
            result.append(folder)
            walk(folder.pk, depth + 1)

    walk(None, 0)

    # Папки, чей родитель в выборку не попал (у deptdocs доступ выдаётся
    # на конкретную папку, а не на всё дерево), иначе потерялись бы: их
    # родителя нет в children[None], и walk() до них не дошёл бы.
    for folder in folders:
        if folder.parent_id is not None and folder.parent_id not in known_ids:
            folder.indented_name = folder.name
            result.append(folder)

    return result


class PartialGridMixin:
    """Позволяет ListView отдать ТОЛЬКО сетку карточек (?partial=1) вместо
    целой страницы.

    Ради этого существует весь живой режим файлового менеджера: когда
    приходит WebSocket-событие «в этой папке что-то изменилось» (или когда
    пользователь сам что-то сделал), клиент дозапрашивает сетку и
    подменяет её кусок DOM, вместо window.location.reload() на каждое
    переименование.

    Почему перерисовывается серверный HTML, а не собирается карточка в JS:
    карточка рендерится include-шаблоном, внутри которого проверки прав
    ({% if item.owner_id == request.user.pk %}). Вторая реализация той же
    разметки и тех же прав на JS обязана была бы совпадать с первой — а
    совпадать она перестанет при первом же изменении правил.
    """

    partial_template_name = None

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get('partial') == '1' and self.partial_template_name:
            return render(self.request, self.partial_template_name, context)
        return super().render_to_response(context, **response_kwargs)

    def get_paginate_by(self, queryset):
        try:
            per_page = int(self.request.GET.get('per_page', DEFAULT_PER_PAGE))
        except (TypeError, ValueError):
            return DEFAULT_PER_PAGE
        return per_page if per_page in PER_PAGE_OPTIONS else DEFAULT_PER_PAGE

    def get_fm_context(self, active_sort, active_filters=None):
        """Общий кусок контекста для панели фильтров/сортировки/пагинации —
        одинаковый у всех трёх модулей."""
        return {
            'sort_options': SORT_OPTIONS,
            'active_sort': active_sort,
            'active_filters': active_filters or {},
            'per_page_options': PER_PAGE_OPTIONS,
            'active_per_page': self.get_paginate_by(None),
        }
