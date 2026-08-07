"""Общие вспомогательные функции для UI файлового менеджера — не часть
StorageService (это не про хранение файлов), но нужны одинаково всем трём
потребителям (exchange, catalog, deptdocs), поэтому вынесены сюда, а не
продублированы в каждом views.py."""
from datetime import datetime, time

from django.db.models import Q
from django.http import JsonResponse
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
#
# SVG отсюда убран намеренно: см. INLINE_UNSAFE_MIME_TYPES ниже.
PREVIEWABLE_EXTENSIONS = {
    'PDF',
    'PNG', 'JPG', 'JPEG', 'GIF', 'WEBP', 'BMP',
    'TXT', 'CSV', 'LOG', 'MD',
    'MP4', 'WEBM', 'MP3', 'WAV', 'OGG',
}

# Что безопасно отдавать с 'Content-Disposition: inline', то есть открывать
# прямо в браузере в домене портала.
#
# PREVIEWABLE_EXTENSIONS выше — про интерфейс: показывать ли пункт
# «Просмотр». Это здесь — про сервер: эндпоинты скачивания принимают
# ?inline=1 для чего угодно, а не только для того, на что в меню есть
# кнопка. Загруженный .html открывался бы как страница портала со всеми
# правами текущего пользователя — то есть хранимая XSS через обычную
# загрузку файла в обменник.
#
# Список именно белый. mime_type определяется по ИМЕНИ загруженного файла
# (mimetypes.guess_type в StorageService.upload), то есть его выбирает
# загрузивший; с чёрным списком любой не перечисленный активный формат
# оказывался бы разрешён по умолчанию.
INLINE_SAFE_MIME_PREFIXES = ('image/', 'video/', 'audio/')

INLINE_SAFE_MIME_TYPES = frozenset({
    'application/pdf',
    'text/plain',
    'text/csv',
    'text/markdown',
})

# image/svg+xml формально подходит под префикс image/, но SVG — активный
# документ: внутри работают <script> и обработчики событий, и открытый
# inline он выполняется в домене портала наравне с обычной HTML-страницей.
# Как картинка в <img> он безопасен, но здесь речь про переход по ссылке.
INLINE_UNSAFE_MIME_TYPES = frozenset({'image/svg+xml'})


def is_inline_safe(mime_type) -> bool:
    """Можно ли отдать этот тип с 'Content-Disposition: inline'."""
    mime = (mime_type or '').split(';')[0].strip().lower()
    if mime in INLINE_UNSAFE_MIME_TYPES:
        return False
    return mime in INLINE_SAFE_MIME_TYPES or mime.startswith(INLINE_SAFE_MIME_PREFIXES)


# Опрос статуса фоновой задачи идёт по task_id через общий эндпоинт
# /storage/task-status/<id>/, который обслуживает все три модуля. Раньше он
# отдавал статус любому аутентифицированному: uuid4 не угадаешь, но и
# владения не проверялось. Теперь запустивший запоминается в своей сессии.
#
# Сессия, а не кеш: CACHES в проекте не сконфигурирован, то есть это
# LocMemCache — память ОДНОГО процесса, теряется при перезапуске Daphne.
# Сессии лежат в БД и переживают и перезапуск, и второй процесс.
FM_TASK_SESSION_KEY = 'fm_task_ids'
FM_TASK_HISTORY_LIMIT = 20


def fm_task_response(request, task, **extra):
    """Единственный правильный способ вернуть task_id фронту.

    Собран как хелпер, а не как «не забудьте записать id в сессию» в
    чек-листе: запусков восемь штук в четырёх модулях, и забытая строчка
    означала бы 403 у самого же владельца задачи. Здесь запомнить нельзя
    забыть — ответ и запись делаются одним вызовом.

    extra — дополнительные поля ответа (например, сколько файлов в архиве),
    чтобы вызывающему не приходилось собирать JsonResponse руками в обход
    записи в сессию.
    """
    task_ids = [tid for tid in request.session.get(FM_TASK_SESSION_KEY, []) if tid != task.id]
    request.session[FM_TASK_SESSION_KEY] = task_ids[-(FM_TASK_HISTORY_LIMIT - 1):] + [task.id]

    return JsonResponse({'success': True, 'task_id': task.id, **extra})


def owns_task(request, task_id) -> bool:
    """Запускал ли текущий пользователь эту задачу (в этой сессии)."""
    return task_id in request.session.get(FM_TASK_SESSION_KEY, [])


def fm_archive_view(request, queryset, *, filename):
    """Общая половина массового скачивания одним zip для трёх модулей.

    Разное у модулей ровно одно — queryset, уже суженный до того, что этому
    пользователю разрешено видеть (права, как всегда, проверяет потребитель,
    не storage). Всё остальное — разбор ?ids=, режим проверки ?check=1,
    пределы и оформление ответа — одинаково, и три копии этого кода
    разошлись бы, как уже разошлись когда-то карточки файлов.

    Два режима одного адреса:
      ?check=1  — JSON: можно ли собрать такой архив и что в него войдёт;
      без него  — сам архив.

    Развилка нужна потому, что за архивом браузер уходит обычной навигацией
    (иначе весь zip пришлось бы держать в памяти вкладки), и показать
    причину отказа в этот момент уже нечем — ответ просто заменит собой
    страницу. Поэтому кнопка сначала спрашивает разрешение, и только
    получив его, переходит по ссылке.
    """
    from .exceptions import ArchiveTooLargeError
    from .services import StorageService

    requested = [chunk for chunk in request.GET.get('ids', '').split(',') if chunk.isdigit()]

    items = queryset.filter(pk__in=requested).select_related('file_object__blob')
    file_objects = [item.file_object for item in items]

    try:
        summary = StorageService.check_archive_request(file_objects)
    except ArchiveTooLargeError as error:
        return JsonResponse({'success': False, 'error': error.message}, status=400)

    if request.GET.get('check') == '1':
        # skipped — то, что пользователь выбрал, но видеть не вправе (или
        # уже удалено). Молча недодать файлов нельзя: человек получил бы
        # архив и не узнал, что он неполный.
        return JsonResponse({
            'success': True,
            'count': summary['count'],
            'total_size': summary['total_size'],
            'skipped': len(requested) - summary['count'],
        })

    return StorageService.get_archive_response(file_objects, filename=filename)


def fm_archive_upload_view(request, *, category, launch, field='archive'):
    """Общая половина загрузки zip с распаковкой для трёх модулей.

    Разное у модулей — только куда распаковывать и какую задачу запустить
    (`launch`); проверки, приём файла и оформление ответа одинаковы.

    Порядок шагов важен:

    1. Архив проверяется СИНХРОННО, до постановки задачи. Читается только
       оглавление, поэтому это дёшево даже на большом файле, зато «это не
       архив», «архив под паролем» и «слишком много файлов» пользователь
       видит сразу в ответ на загрузку, а не через опрос статуса задачи.
    2. Сам архив кладётся в storage обычным upload(). Не во временный файл:
       задачу выполняет другой процесс (воркер Celery), и ему нужно откуда-то
       прочитать содержимое, а blobs/ — единственное место, где в этом
       проекте лежат файлы (ARCHITECTURE.md, раздел 3). Заодно на архив
       действуют те же STORAGE_MAX_UPLOAD_SIZE и квота, что и на любой
       другой файл. Задача отвяжет его по завершении.
    3. Ответ — тот же {'task_id': ...}, что и у массовых операций, поэтому
       прогресс на фронте работает уже существующим FM.pollTask.
    """
    from .archives import inspect_archive
    from .exceptions import FileTooLargeError, InvalidArchiveError, QuotaExceededError
    from .services import StorageService

    uploaded = request.FILES.get(field)
    if not uploaded:
        return JsonResponse({'success': False, 'error': 'Архив не выбран'}, status=400)

    try:
        entries = inspect_archive(uploaded)
    except InvalidArchiveError as error:
        return JsonResponse({'success': False, 'error': error.message}, status=400)

    try:
        archive_object = StorageService.upload(uploaded, user=request.user, category=category)
    except FileTooLargeError:
        return JsonResponse(
            {'success': False, 'error': 'Файл архива слишком большой'}, status=400,
        )
    except QuotaExceededError:
        return JsonResponse(
            {'success': False, 'error': 'Превышена квота хранилища'}, status=400,
        )

    # total — сколько файлов ждать; фронт покажет это в подписи прогресса
    # ещё до того, как задача доложит первый шаг.
    return fm_task_response(request, launch(archive_object), total=len(entries))


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
