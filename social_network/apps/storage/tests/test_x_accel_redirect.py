"""Регрессионный тест на прод-путь отдачи файлов: X-Accel-Redirect + nginx.

Повод. `test_direct_access.py` ходит через Django-клиент и проверяет, что
`/media/storage/...` не отдаётся. В проде эту дыру закрывает не Django, а
nginx: перехватчик `^media/storage/` в корневом urls.py стоит перед
`static()`, который при DEBUG=False вообще не подключается — раздачей медиа
занимается веб-сервер. До этой сессии в nginx.conf был обычный
`location /media/` и не было internal-локейшна `/protected/`, то есть тесты
были зелёными, а прод сломан сразу в обе стороны: скачивание не работало
(`X-Accel-Redirect` некому обработать), а прямая ссылка на
`/media/storage/blobs/<checksum>` работала — в обход всех проверок прав
модуля-потребителя.

Поэтому проверяются обе половины контракта, и по отдельности ни одна из них
ничего не гарантирует:

1. Django при DEBUG=False отдаёт `X-Accel-Redirect` с путём относительно
   MEDIA_ROOT и не пропускает тело файла через воркер.
2. Конфигурация nginx содержит ровно те location'ы, на которые этот
   заголовок рассчитан.

Отдельного внимания стоит `assertNotIn('\\\\', ...)`: разработка идёт на
Windows, `FileField.name` там хранится с обратными слэшами, и без
нормализации в заголовок ушёл бы путь вида
`/protected/storage\\blobs\\ab\\<checksum>`, который nginx не найдёт.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from catalog.models import CatalogDocument
from storage.models import FileObject
from storage.services import StorageService

User = get_user_model()

PROD_CONFIG = Path(str(settings.BASE_DIR)) / 'deploy' / 'nginx' / 'portal.conf'
# Локальный Windows-nginx лежит в каталоге, который целиком в .gitignore, —
# на чистом клоне и на проде его нет, поэтому проверки по нему условные.
LOCAL_CONFIG = Path(str(settings.BASE_DIR)) / 'nginx-1.28.0' / 'conf' / 'nginx.conf'


def read_config(path: Path) -> str:
    """Текст конфига без комментариев — иначе закомментированный location
    считался бы существующим."""
    text = path.read_text(encoding='utf-8')
    return re.sub(r'#[^\n]*', '', text)


def location_body(config: str, prefix: str) -> str | None:
    """Тело `location <prefix> { ... }` или None, если такого нет."""
    opening = re.search(
        r'location\s+(?:\^~\s*)?' + re.escape(prefix) + r'\s*\{', config
    )
    if opening is None:
        return None

    depth, index = 1, opening.end()
    while index < len(config) and depth:
        if config[index] == '{':
            depth += 1
        elif config[index] == '}':
            depth -= 1
        index += 1
    return config[opening.end():index - 1]


def directive_value(block: str, name: str) -> str | None:
    match = re.search(r'\b' + re.escape(name) + r'\s+([^;]+);', block)
    return match.group(1).strip() if match else None


@override_settings(DEBUG=False)
class XAccelRedirectHeaderTest(TestCase):
    """Свой MEDIA_ROOT на каждый тест — обязательное условие, а не гигиена.

    Ожидаемый путь в заголовке считается от checksum. Если файл от
    предыдущего теста переживёт откат БД, FileSystemStorage допишет к имени
    случайный суффикс (`<checksum>_B24AkEu`), и путь перестанет совпадать —
    тест будет падать при исправном коде (см. ARCHITECTURE.md, 5.4).
    """

    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='xaccel-test-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(username='xaccel_user', password='pass12345')
        self.file_object = StorageService.upload(
            SimpleUploadedFile('Приказ №12.pdf', b'PDF CONTENT', content_type='application/pdf'),
            user=self.user,
            category=FileObject.Category.CATALOG,
        )

    def response(self, **kwargs):
        return StorageService.get_download_response(
            self.file_object, RequestFactory().get('/'), **kwargs
        )

    def expected_path(self):
        checksum = self.file_object.blob.checksum
        return f'/protected/storage/blobs/{checksum[:2]}/{checksum}'

    def test_header_is_set_and_points_relative_to_media_root(self):
        """Путь в заголовке считается от MEDIA_ROOT, потому что alias в
        nginx указывает именно на MEDIA_ROOT. С alias на blobs/ путь бы
        удвоился (ARCHITECTURE.md, раздел 8)."""
        response = self.response()

        self.assertIn(
            'X-Accel-Redirect', response,
            'При DEBUG=False файл обязан отдаваться через nginx, а не через воркер Daphne',
        )
        self.assertEqual(response['X-Accel-Redirect'], self.expected_path())

    def test_header_never_contains_backslashes(self):
        """Windows-специфичная ловушка: FileField.name хранится с os.sep,
        и без нормализации nginx получил бы `/protected/storage\\blobs\\...`
        и отдал 404 — на Linux-проде тест бы этого не поймал."""
        self.assertNotIn('\\', self.response()['X-Accel-Redirect'])

    def test_file_body_does_not_go_through_django(self):
        """Смысл X-Accel-Redirect: воркер не занят на всё время скачивания.
        Непустое тело означает, что файл всё-таки прочитан приложением."""
        response = self.response()

        self.assertEqual(response.content, b'')
        self.assertFalse(getattr(response, 'streaming', False))

    def test_content_disposition_carries_original_name(self):
        """На диске файл называется checksum'ом — осмысленное имя приходит
        только из этого заголовка, nginx его не переписывает."""
        header = self.response()['Content-Disposition']

        self.assertTrue(header.startswith('attachment;'))
        self.assertIn(quote('Приказ №12.pdf'), header)
        self.assertNotIn(self.file_object.blob.checksum, header)

    def test_cyrillic_name_uses_rfc5987_not_rfc2047(self):
        """Заголовок со значением вне latin-1 HttpResponse кодирует по
        RFC 2047 (=?utf-8?b?...?=). Content-Disposition эту форму не
        поддерживает: браузер сохранил бы файл под base64-строкой или под
        именем-хэшем из URL. Имена в портале почти все кириллические,
        то есть баг задевал бы почти каждое скачивание — и только на проде,
        потому что в ветке DEBUG имя оформляет FileResponse."""
        header = self.response()['Content-Disposition']

        self.assertFalse(header.startswith('=?'), f'RFC 2047 в Content-Disposition: {header}')
        self.assertIn("filename*=utf-8''", header)

    def test_ascii_name_stays_plain(self):
        """Обратная сторона: имя из ASCII не нужно уводить в RFC 5987 —
        обычный filename="..." понимают все клиенты без исключений."""
        ascii_object = StorageService.upload(
            SimpleUploadedFile('report.pdf', b'ANOTHER PDF', content_type='application/pdf'),
            user=self.user,
            category=FileObject.Category.CATALOG,
        )
        response = StorageService.get_download_response(ascii_object, RequestFactory().get('/'))

        self.assertEqual(response['Content-Disposition'], 'attachment; filename="report.pdf"')

    def test_inline_preview_also_goes_through_nginx(self):
        """Просмотр в браузере (?inline=1) — тот же прод-путь, меняется
        только Content-Disposition."""
        response = self.response(inline=True)

        self.assertEqual(response['X-Accel-Redirect'], self.expected_path())
        self.assertIn('inline', response['Content-Disposition'])

    def test_consumer_download_view_emits_the_header(self):
        """Сервис проверен выше отдельно; здесь — что реальный эндпоинт
        потребителя отдаёт именно этот ответ, а не заворачивает его."""
        document = CatalogDocument.objects.create(
            file_object=self.file_object, title='Приказ', uploaded_by=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse('catalog_download', args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Accel-Redirect'], self.expected_path())

    @override_settings(DEBUG=True)
    def test_debug_serves_the_file_itself_without_the_header(self):
        """Локальная разработка не требует nginx: при DEBUG=True файл
        отдаётся FileResponse'ом. Обратная сторона — заголовок в этом режиме
        не проверить, поэтому весь класс идёт с DEBUG=False.

        Закрывается здесь именно файл, а не response: HttpResponseBase.close()
        шлёт request_finished, а его штатный обработчик close_old_connections
        рвёт соединение с БД — внутри atomic-блока TestCase оно потом не
        восстанавливается, и все последующие тесты класса падают на setUp.
        Тестовый клиент Django ради этого отключает обработчик на время
        своего вызова, при прямом обращении к сервису делать это некому."""
        response = self.response()
        self.addCleanup(response.file_to_stream.close)

        self.assertNotIn('X-Accel-Redirect', response)
        self.assertEqual(b''.join(response.streaming_content), b'PDF CONTENT')


class NginxConfigTest(SimpleTestCase):
    """Половина контракта, живущая вне Python.

    Проверяется прод-конфиг из git; локальный Windows-конфиг — если он есть
    на этой машине (его каталог в .gitignore).
    """

    def setUp(self):
        self.assertTrue(
            PROD_CONFIG.exists(),
            f'Прод-конфиг nginx должен лежать в git: {PROD_CONFIG}',
        )
        self.prod = read_config(PROD_CONFIG)

    def configs(self):
        yield 'deploy/nginx/portal.conf', self.prod
        if LOCAL_CONFIG.exists():
            yield 'nginx-1.28.0/conf/nginx.conf', read_config(LOCAL_CONFIG)

    def test_protected_location_exists_and_is_internal(self):
        """Без `internal` этот location становится публичным зеркалом всей
        медиатеки — то есть ровно той дырой, которую он закрывает."""
        for name, config in self.configs():
            with self.subTest(config=name):
                block = location_body(config, '/protected/')
                self.assertIsNotNone(block, 'нет location /protected/ для X-Accel-Redirect')
                self.assertIn(
                    'internal', block,
                    'location /protected/ обязан быть internal, иначе к файлам '
                    'есть прямой доступ по /protected/storage/blobs/<checksum>',
                )

    def test_protected_alias_is_media_root_not_blobs_dir(self):
        """get_download_response() считает путь от MEDIA_ROOT. Alias на
        .../storage/blobs/ удвоил бы путь и дал 404 на каждом скачивании."""
        for name, config in self.configs():
            with self.subTest(config=name):
                alias = directive_value(location_body(config, '/protected/'), 'alias')
                self.assertIsNotNone(alias, 'у location /protected/ нет alias')
                self.assertTrue(
                    alias.rstrip('/').endswith('media'),
                    f'alias должен указывать на MEDIA_ROOT, а не на {alias}',
                )
                self.assertNotIn('blobs', alias)

    def test_direct_access_to_blobs_is_denied(self):
        """Django-перехватчик ^media/storage/ работает только при DEBUG=True:
        при DEBUG=False медиа раздаёт nginx и до urls.py дело не доходит."""
        for name, config in self.configs():
            with self.subTest(config=name):
                block = location_body(config, '/media/storage/')
                self.assertIsNotNone(
                    block,
                    'нет location /media/storage/ — blob скачивается по прямой '
                    'ссылке в обход проверки прав модуля-потребителя',
                )
                self.assertIn('deny all', block)

    def test_public_media_is_still_served(self):
        """Обратная проверка: запрет не должен закрывать аватары, картинки
        постов и галерею — они намеренно не в storage (ARCHITECTURE.md, 1.1)."""
        for name, config in self.configs():
            with self.subTest(config=name):
                block = location_body(config, '/media/')
                self.assertIsNotNone(block, 'публичная медиатека перестала раздаваться')
                self.assertIsNotNone(directive_value(block, 'alias'))

    def test_upload_limit_is_not_stricter_than_django(self):
        """client_max_body_size ниже STORAGE_MAX_UPLOAD_SIZE означает, что
        файл разрешённого размера отбивает nginx своей страницей 413, и
        пользователь не видит ни сообщения об ошибке, ни причины."""
        limit_mb = settings.STORAGE_MAX_UPLOAD_SIZE / (1024 * 1024)

        for name, config in self.configs():
            with self.subTest(config=name):
                value = directive_value(config, 'client_max_body_size')
                self.assertIsNotNone(value, 'client_max_body_size не задан')
                self.assertGreaterEqual(float(value.rstrip('Mm')), limit_mb)

    def test_local_config_aliases_point_to_this_checkout(self):
        """Локальный конфиг содержит абсолютные пути этой машины: если
        проект переехал, nginx молча раздаёт пустоту."""
        if not LOCAL_CONFIG.exists():
            self.skipTest('локальный nginx не установлен')

        config = read_config(LOCAL_CONFIG)
        expected = os.path.normcase(os.path.normpath(os.path.join(str(settings.BASE_DIR), 'media')))

        for prefix in ('/protected/', '/media/'):
            with self.subTest(location=prefix):
                alias = directive_value(location_body(config, prefix), 'alias')
                self.assertEqual(os.path.normcase(os.path.normpath(alias)), expected)
