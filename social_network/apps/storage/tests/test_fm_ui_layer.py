"""Слой интерфейса файлового менеджера: видимость подпапок, массовые
операции, шаблонные теги и устойчивость живых обновлений.

Здесь собрано то, что раньше проверялось только глазами: пока сетка
рендерилась целой страницей и любое действие заканчивалось
window.location.reload(), ошибки в этих местах проявлялись как «кнопка не
работает» и ловились вручную.
"""

import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.template import Context, Template
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from deptdocs.models import DepartmentDocument, DepartmentFolder
from exchange.models import ExchangeFile, ExchangeFolder
from profiles.models import Notification
from storage import realtime
from storage.models import FileObject
from storage.services import StorageService
from storage.templatetags.fm_tags import fm_qs, is_previewable, trash_days_left
from storage.utils import apply_filters, build_folder_choices, folder_ancestors

User = get_user_model()


class DeptdocsSubfolderVisibilityTest(TestCase):
    """Список вложенных подпапок фильтруется по доступу.

    Регрессия: на корневом уровне фильтр по allowed_users был, а на
    вложенном — нет. Зайдя в доступную папку, пользователь видел названия
    ВСЕХ её подпапок, включая закрытые. Открыть их он не мог
    (PermissionDenied), но само название уже раскрывалось — а название
    папки в приватном доступе само по себе бывает содержательным.
    """

    def setUp(self):
        self.user = User.objects.create_user(username='subfolder_user', password='pass12345')
        self.other = User.objects.create_user(username='subfolder_other', password='pass12345')
        self.client.force_login(self.user)

        self.parent = DepartmentFolder.objects.create(name='Общий отдел', created_by=self.user)
        self.parent.allowed_users.add(self.user, self.other)

        self.visible = DepartmentFolder.objects.create(
            name='Видимая подпапка', parent=self.parent, created_by=self.user,
        )
        self.visible.allowed_users.add(self.user)

        self.hidden = DepartmentFolder.objects.create(
            name='Закрытая зарплата', parent=self.parent, created_by=self.other,
        )
        self.hidden.allowed_users.add(self.other)

    def test_inaccessible_subfolder_is_not_listed(self):
        response = self.client.get(reverse('deptdocs_folder', args=[self.parent.pk]))

        subfolders = list(response.context['subfolders'])
        self.assertIn(self.visible, subfolders)
        self.assertNotIn(self.hidden, subfolders)

    def test_inaccessible_subfolder_name_is_not_in_html(self):
        response = self.client.get(reverse('deptdocs_folder', args=[self.parent.pk]))

        self.assertContains(response, 'Видимая подпапка')
        self.assertNotContains(response, 'Закрытая зарплата')

    def test_root_documents_are_not_listed(self):
        """Документ вне папки не наследует ничьих прав и не должен быть
        виден никому — а корневой список показывал такие документы всем."""
        uploaded = SimpleUploadedFile('orphan.pdf', b'orphan', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.DOCUMENT)
        DepartmentDocument.objects.create(
            file_object=file_object, folder=None, title='Документ без папки', uploaded_by=self.user,
        )

        response = self.client.get(reverse('deptdocs_list'))

        self.assertEqual(list(response.context['documents']), [])
        self.assertNotContains(response, 'Документ без папки')


class ExchangeBulkMoveTest(TestCase):
    """Массового перемещения у обменника раньше не было вовсе: выделять
    файлы было можно, а в панели оставалось только удаление."""

    def setUp(self):
        self.user = User.objects.create_user(username='bulk_move_user', password='pass12345')
        self.client.force_login(self.user)
        self.folder = ExchangeFolder.objects.create(
            name='Проект', owner=self.user, created_by=self.user,
        )

    def _file(self, name):
        uploaded = SimpleUploadedFile(name, name.encode() * 3, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.EXCHANGE)
        return ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, uploaded_by=self.user,
        )

    def test_bulk_move_puts_files_into_subfolder(self):
        first, second = self._file('bm1.pdf'), self._file('bm2.pdf')

        response = self.client.post(reverse('exchange_bulk_move'), {
            'file_ids': [first.pk, second.pk], 'folder_id': self.folder.pk,
        })

        self.assertEqual(response.status_code, 200)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.folder_id, self.folder.pk)
        self.assertEqual(second.folder_id, self.folder.pk)

    def test_bulk_move_into_another_owner_folder_changes_owner(self):
        """Массовое перемещение идёт по тем же правилам, что и одиночное:
        разойдись они, одно стало бы обходным путём вокруг другого."""
        stranger = User.objects.create_user(username='bulk_move_stranger', password='x')
        foreign_folder = ExchangeFolder.objects.create(
            name='Чужая', owner=stranger, created_by=stranger,
        )
        my_file = self._file('bm3.pdf')

        response = self.client.post(reverse('exchange_bulk_move'), {
            'file_ids': [my_file.pk], 'folder_id': foreign_folder.pk,
        })

        self.assertEqual(response.status_code, 200)
        my_file.refresh_from_db()
        self.assertEqual(my_file.folder_id, foreign_folder.pk)
        self.assertEqual(my_file.owner_id, stranger.pk)
        self.assertTrue(Notification.objects.filter(recipient=stranger).exists())

    def test_bulk_move_into_another_owner_root(self):
        """Корень чужой личной папки — owner_id без folder_id: владелец и
        папка меняются одним update() (storage.tasks.bulk_move_documents,
        extra_updates), иначе между двумя запросами файл не виден ни в
        папке-источнике, ни в папке-назначении."""
        stranger = User.objects.create_user(username='bulk_move_root', password='x')
        my_file = self._file('bm4.pdf')
        my_file.folder = self.folder
        my_file.save(update_fields=['folder'])

        response = self.client.post(reverse('exchange_bulk_move'), {
            'file_ids': [my_file.pk], 'owner_id': stranger.pk, 'folder_id': '',
        })

        self.assertEqual(response.status_code, 200)
        my_file.refresh_from_db()
        self.assertEqual(my_file.owner_id, stranger.pk)
        self.assertIsNone(my_file.folder_id)

    def test_bulk_move_keeps_owner_when_destination_is_own_root(self):
        """Без owner_id и без папки владелец не меняется вообще — у
        выделения нет одного «текущего владельца», и подставлять его было бы
        не из чего."""
        my_file = self._file('bm5.pdf')
        my_file.folder = self.folder
        my_file.save(update_fields=['folder'])

        response = self.client.post(reverse('exchange_bulk_move'), {
            'file_ids': [my_file.pk], 'folder_id': '',
        })

        self.assertEqual(response.status_code, 200)
        my_file.refresh_from_db()
        self.assertEqual(my_file.owner_id, self.user.pk)
        self.assertIsNone(my_file.folder_id)

    def test_bulk_move_without_rights_answers_200_with_message(self):
        """Отвечать 403 на конкретные id значит подтверждать, что они
        существуют. Но и молчать нельзя — раньше пользователь не видел
        вообще никакой реакции."""
        stranger = User.objects.create_user(username='bulk_move_stranger2', password='x')
        uploaded = SimpleUploadedFile('foreign.pdf', b'foreign', content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=stranger, category=FileObject.Category.EXCHANGE)
        foreign_file = ExchangeFile.objects.create(
            file_object=file_object, owner=stranger, uploaded_by=stranger,
        )

        response = self.client.post(reverse('exchange_bulk_move'), {
            'file_ids': [foreign_file.pk], 'folder_id': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.assertIn('нет прав', response.json()['message'].lower())


class FmActionsSourceTest(SimpleTestCase):
    """Два свойства `fm-actions.js`, которые нечем проверить иначе.

    JS-раннера в проекте нет, а обе эти вещи ломались молча и **не
    ловились серверными тестами в принципе** — поэтому проверяется сам
    исходник, как уже делается с конфигами nginx в
    `test_x_accel_redirect.py`. Тест грубый, но он стоит ровно там, где
    отсутствие проверки уже стоило рабочей функциональности.
    """

    def source(self) -> str:
        path = Path(settings.BASE_DIR) / 'static' / 'js' / 'fm-actions.js'
        return path.read_text(encoding='utf-8')

    def test_post_puts_csrf_token_into_the_body(self):
        """FormData без единого поля — это multipart-тело без частей, и
        Daphne отвечает на такое 400 ещё до Django. Под это попадали все
        действия без данных: удаление в корзину, восстановление,
        окончательное удаление, удаление папки, пересылка в каталог.

        Тестовый клиент Django кодирует пустой словарь как
        x-www-form-urlencoded и до Daphne не доходит, runserver (WSGI)
        такой запрос пропускает — расхождение видно только на боевом
        сервере, то есть у пользователя.
        """
        self.assertIn("body.append('csrfmiddlewaretoken'", self.source())

    def test_toast_works_without_the_theme_plugin(self):
        """Стили тостов Midone кладёт в app.css, а сам плагин прячет внутрь
        своего бандла и вешает на jQuery, которую webpack держит у себя. У
        подключённой отдельным файлом jQuery метода .toast() нет, и все
        сообщения уходили в console — то есть отказ операции пользователь
        не видел вовсе, вопреки правилу 12.4."""
        self.assertIn('jq-toast-wrap', self.source())


class StaticVersioningTest(TestCase):
    """Исправление в JS должно доезжать до браузера без Ctrl+F5.

    Предыдущий тест проверяет, что нужная строка есть в `fm-actions.js`, —
    и этого оказалось мало: сервер отдавал исправленный файл, а браузер
    выполнял прежний. В ответе статики есть только `Last-Modified`, и Chrome
    кеширует такой файл «на глазок», не спрашивая сервер (в замере через
    CDP повторный заход давал `transferSize: 0`). Починка удаления в
    корзину из-за этого для пользователя просто не наступила.

    Лечится версией в адресе — см. `social_network/staticfiles.py`.
    """

    def storage(self, location):
        from social_network.staticfiles import VersionedStaticFilesStorage

        return VersionedStaticFilesStorage(location=location, base_url='/app/static/')

    def test_static_tag_adds_the_file_version(self):
        rendered = Template("{% load static %}{% static 'js/fm-actions.js' %}").render(Context())

        path = Path(settings.BASE_DIR) / 'static' / 'js' / 'fm-actions.js'
        self.assertEqual(rendered, f'/app/static/js/fm-actions.js?v={int(path.stat().st_mtime)}')

    def test_version_changes_when_the_file_changes(self):
        """Ради этого всё и делается: правка файла обязана менять адрес,
        иначе браузер продолжит выполнять то, что у него уже лежит."""
        directory = tempfile.mkdtemp(prefix='portal-static-')
        self.addCleanup(shutil.rmtree, directory, True)
        script = Path(directory) / 'script.js'
        script.write_text('первая версия', encoding='utf-8')
        storage = self.storage(directory)

        before = storage.url('script.js')
        os.utime(script, (script.stat().st_atime, script.stat().st_mtime + 60))
        after = storage.url('script.js')

        self.assertNotEqual(before, after)

    def test_unknown_file_keeps_a_plain_url(self):
        """Статика сторонних приложений лежит мимо STATIC_ROOT. Версии для
        неё нет — но и падать на ней рендер страницы не должен."""
        directory = tempfile.mkdtemp(prefix='portal-static-')
        self.addCleanup(shutil.rmtree, directory, True)

        self.assertEqual(self.storage(directory).url('missing.js'), '/app/static/missing.js')

    def test_page_references_the_versioned_script(self):
        """Проверка на настоящей странице: тег `{% static %}` мог бы брать
        адрес мимо этого хранилища (у проекта из finders — только
        CompressorFinder), и тогда версия не попала бы никуда."""
        user = User.objects.create_user(username='static_version_user', password='pass12345')
        self.client.force_login(user)

        response = self.client.get(reverse('exchange_folder', args=[user.pk]))

        self.assertContains(response, 'js/fm-actions.js?v=')


class ExchangeMoveTargetsTest(TestCase):
    """Что предлагает селект перемещения.

    Контрол обязан предлагать ровно то, что сработает (ARCHITECTURE.md,
    12.4). Пока перенос между сотрудниками был запрещён, в списке стояли
    только подпапки текущей личной папки; теперь — личные папки всех
    сотрудников со своими подпапками."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='move_targets_user', password='pass12345', last_name='Иванов',
        )
        self.other = User.objects.create_user(
            username='move_targets_other', password='x', last_name='Петров',
        )
        self.client.force_login(self.user)

    def test_targets_include_every_employee(self):
        response = self.client.get(reverse('exchange_folder', args=[self.user.pk]))

        targets = {target['title']: target for target in response.context['move_targets']}
        self.assertIn('Петров', targets)
        self.assertFalse(targets['Петров']['is_current'])
        self.assertTrue(targets['Иванов']['is_current'])

    def test_foreign_subfolder_option_carries_its_owner(self):
        """Своя подпапка — голый id, чужая — пара «owner:folder»: корень
        личной папки подпапкой не является и одним id не называется, а
        двоеточие в значении заодно служит признаком «папка чужая» для
        предупреждения в модалке."""
        mine = ExchangeFolder.objects.create(name='Моя', owner=self.user, created_by=self.user)
        theirs = ExchangeFolder.objects.create(name='Чужая', owner=self.other, created_by=self.other)

        response = self.client.get(reverse('exchange_folder', args=[self.user.pk]))

        self.assertContains(response, f'value="{mine.pk}"')
        self.assertContains(response, f'value="{self.other.pk}:{theirs.pk}"')
        self.assertContains(response, f'value="{self.other.pk}:"')


class DeptdocsBulkMoveTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='dept_bulk_user', password='pass12345')
        self.client.force_login(self.user)

        self.source = DepartmentFolder.objects.create(name='Источник', created_by=self.user)
        self.source.allowed_users.add(self.user)
        self.target = DepartmentFolder.objects.create(name='Назначение', created_by=self.user)
        self.target.allowed_users.add(self.user)

    def _document(self, folder, name):
        uploaded = SimpleUploadedFile(name, name.encode() * 3, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=self.user, category=FileObject.Category.DOCUMENT)
        return DepartmentDocument.objects.create(
            file_object=file_object, folder=folder, title=name, uploaded_by=self.user,
        )

    def test_bulk_move_between_accessible_folders(self):
        document = self._document(self.source, 'dbm1.pdf')

        response = self.client.post(reverse('deptdocs_bulk_move'), {
            'doc_ids': [document.pk], 'folder_id': self.target.pk,
        })

        self.assertEqual(response.status_code, 200)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.target.pk)

    def test_bulk_move_into_inaccessible_folder_is_denied(self):
        other = User.objects.create_user(username='dept_bulk_other', password='x')
        foreign = DepartmentFolder.objects.create(name='Чужая', created_by=other)
        foreign.allowed_users.add(other)
        document = self._document(self.source, 'dbm2.pdf')

        response = self.client.post(reverse('deptdocs_bulk_move'), {
            'doc_ids': [document.pk], 'folder_id': foreign.pk,
        })

        self.assertEqual(response.status_code, 403)
        document.refresh_from_db()
        self.assertEqual(document.folder_id, self.source.pk)


class FolderMoveTest(TestCase):
    """Перенос папок. У каталога и приватного доступа эндпоинты были с
    самого начала, но в меню карточки не выводились — пользоваться ими
    было нельзя; у обменника вьюхи не было вовсе."""

    def setUp(self):
        self.user = User.objects.create_user(username='folder_move_user', password='pass12345')
        self.client.force_login(self.user)

    def test_exchange_subfolder_moves_under_another(self):
        first = ExchangeFolder.objects.create(name='Первая', owner=self.user, created_by=self.user)
        second = ExchangeFolder.objects.create(name='Вторая', owner=self.user, created_by=self.user)

        response = self.client.post(
            reverse('exchange_folder_move', args=[second.pk]), {'parent_id': first.pk},
        )

        self.assertEqual(response.status_code, 200)
        second.refresh_from_db()
        self.assertEqual(second.parent_id, first.pk)

    def test_exchange_subfolder_moves_back_to_root(self):
        parent = ExchangeFolder.objects.create(name='Родитель', owner=self.user, created_by=self.user)
        child = ExchangeFolder.objects.create(
            name='Вложенная', owner=self.user, parent=parent, created_by=self.user,
        )

        response = self.client.post(reverse('exchange_folder_move', args=[child.pk]), {'parent_id': ''})

        self.assertEqual(response.status_code, 200)
        child.refresh_from_db()
        self.assertIsNone(child.parent_id)

    def test_exchange_folder_cannot_move_into_itself(self):
        """Иначе дерево self-FK замкнулось бы в цикл, и обход подпапок
        завис бы вместе с воркером."""
        folder = ExchangeFolder.objects.create(name='Папка', owner=self.user, created_by=self.user)

        response = self.client.post(
            reverse('exchange_folder_move', args=[folder.pk]), {'parent_id': folder.pk},
        )

        self.assertEqual(response.status_code, 400)
        folder.refresh_from_db()
        self.assertIsNone(folder.parent_id)

    def test_exchange_folder_cannot_move_into_own_descendant(self):
        parent = ExchangeFolder.objects.create(name='Родитель', owner=self.user, created_by=self.user)
        child = ExchangeFolder.objects.create(
            name='Потомок', owner=self.user, parent=parent, created_by=self.user,
        )

        response = self.client.post(
            reverse('exchange_folder_move', args=[parent.pk]), {'parent_id': child.pk},
        )

        self.assertEqual(response.status_code, 400)
        parent.refresh_from_db()
        self.assertIsNone(parent.parent_id)

    def test_exchange_folder_moves_into_another_owner_folder_with_its_subtree(self):
        """Перенос в чужую личную папку разрешён и меняет владельца ВСЕГО
        поддерева. Оставь вложенным файлам прежнего владельца — они пропали
        бы из интерфейса: список фильтруется по owner и folder сразу."""
        stranger = User.objects.create_user(username='folder_move_stranger', password='x')
        mine = ExchangeFolder.objects.create(name='Моя', owner=self.user, created_by=self.user)
        nested = ExchangeFolder.objects.create(
            name='Вложенная', owner=self.user, parent=mine, created_by=self.user,
        )
        foreign = ExchangeFolder.objects.create(name='Чужая', owner=stranger, created_by=stranger)

        uploaded = SimpleUploadedFile('inside.pdf', b'inside content', content_type='application/pdf')
        file_object = StorageService.upload(
            uploaded, user=self.user, category=FileObject.Category.EXCHANGE,
        )
        inside = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, folder=nested, uploaded_by=self.user,
        )

        response = self.client.post(
            reverse('exchange_folder_move', args=[mine.pk]), {'parent_id': foreign.pk},
        )

        self.assertEqual(response.status_code, 200)
        mine.refresh_from_db()
        nested.refresh_from_db()
        inside.refresh_from_db()
        self.assertEqual(mine.parent_id, foreign.pk)
        self.assertEqual(mine.owner_id, stranger.pk)
        self.assertEqual(nested.owner_id, stranger.pk)
        self.assertEqual(inside.owner_id, stranger.pk)

    def test_exchange_folder_move_takes_trashed_files_along(self):
        """У файла в корзине folder сохранён, и восстановление вернёт его в
        эту же папку. Не смени владельца — вернулся бы файл, невидимый ни в
        одной личной папке."""
        stranger = User.objects.create_user(username='folder_move_trash', password='x')
        mine = ExchangeFolder.objects.create(name='Моя', owner=self.user, created_by=self.user)

        uploaded = SimpleUploadedFile('trashed.pdf', b'trashed content', content_type='application/pdf')
        file_object = StorageService.upload(
            uploaded, user=self.user, category=FileObject.Category.EXCHANGE,
        )
        trashed = ExchangeFile.objects.create(
            file_object=file_object, owner=self.user, folder=mine,
            uploaded_by=self.user, is_deleted=True,
        )

        self.client.post(
            reverse('exchange_folder_move', args=[mine.pk]), {'owner_id': stranger.pk},
        )

        trashed.refresh_from_db()
        self.assertEqual(trashed.owner_id, stranger.pk)

    def test_exchange_folder_move_to_another_owner_notifies_them(self):
        stranger = User.objects.create_user(username='folder_move_notify', password='x')
        mine = ExchangeFolder.objects.create(name='Моя', owner=self.user, created_by=self.user)

        self.client.post(
            reverse('exchange_folder_move', args=[mine.pk]), {'owner_id': stranger.pk},
        )

        notification = Notification.objects.filter(recipient=stranger).first()
        self.assertIsNotNone(notification)
        self.assertEqual(notification.kind, Notification.Kind.FILE_SHARED)

    def test_deptdocs_folder_move_requires_access_to_target(self):
        other = User.objects.create_user(username='folder_move_other', password='x')

        mine = DepartmentFolder.objects.create(name='Моя', created_by=self.user)
        mine.allowed_users.add(self.user)
        foreign = DepartmentFolder.objects.create(name='Чужая', created_by=other)
        foreign.allowed_users.add(other)

        response = self.client.post(
            reverse('deptdocs_folder_move', args=[mine.pk]), {'parent_id': foreign.pk},
        )

        self.assertEqual(response.status_code, 403)
        mine.refresh_from_db()
        self.assertIsNone(mine.parent_id)


class FmTemplateTagsTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()

    def _context(self, query_string):
        return {'request': self.factory.get(f'/catalog/?{query_string}')}

    def test_fm_qs_keeps_sort_when_changing_page(self):
        """Регрессия: пагинация собиралась вручную и переносила только ?q=,
        поэтому вторая страница молча возвращалась к сортировке по
        умолчанию."""
        result = fm_qs(self._context('q=акт&sort=name&per_page=48'), page=2)

        self.assertIn('sort=name', result)
        self.assertIn('per_page=48', result)
        self.assertIn('page=2', result)
        self.assertIn('q=', result)

    def test_fm_qs_drops_parameter_when_value_is_none(self):
        result = fm_qs(self._context('sort=name&page=5'), page=None)

        self.assertIn('sort=name', result)
        self.assertNotIn('page=', result)

    def test_fm_qs_never_leaks_partial_flag(self):
        """partial=1 — служебный флаг живого обновления. Попав в ссылку
        пагинации, он заставил бы браузер открыть голый кусок сетки без
        страницы вокруг."""
        result = fm_qs(self._context('partial=1&sort=name'), page=2)

        self.assertNotIn('partial', result)

    def test_is_previewable_only_for_browser_openable_types(self):
        self.assertTrue(is_previewable('PDF'))
        self.assertTrue(is_previewable('png'))
        self.assertFalse(is_previewable('DOCX'))
        self.assertFalse(is_previewable('ZIP'))
        self.assertFalse(is_previewable(''))

    @override_settings(STORAGE_TRASH_RETENTION_DAYS=30)
    def test_trash_days_left_counts_down(self):
        self.assertEqual(trash_days_left(timezone.now()), 30)
        self.assertEqual(trash_days_left(timezone.now() - timedelta(days=28)), 2)

    @override_settings(STORAGE_TRASH_RETENTION_DAYS=30)
    def test_trash_days_left_never_negative(self):
        self.assertEqual(trash_days_left(timezone.now() - timedelta(days=100)), 0)

    def test_trash_days_left_handles_missing_date(self):
        self.assertIsNone(trash_days_left(None))


class FolderHelpersTest(TestCase):

    def test_folder_ancestors_builds_path_from_root(self):
        root = DepartmentFolder.objects.create(name='Кадры')
        middle = DepartmentFolder.objects.create(name='Приказы', parent=root)
        leaf = DepartmentFolder.objects.create(name='2026', parent=middle)

        self.assertEqual(folder_ancestors(leaf), [root, middle, leaf])
        self.assertEqual(folder_ancestors(None), [])

    def test_build_folder_choices_indents_by_depth(self):
        root = DepartmentFolder.objects.create(name='Кадры')
        child = DepartmentFolder.objects.create(name='Приказы', parent=root)

        choices = build_folder_choices(DepartmentFolder.objects.all())
        by_pk = {folder.pk: folder.indented_name for folder in choices}

        self.assertEqual(by_pk[root.pk], 'Кадры')
        self.assertEqual(by_pk[child.pk], '— Приказы')

    def test_build_folder_choices_keeps_folders_with_unavailable_parent(self):
        """У приватного доступа доступ выдаётся на конкретную папку, а не
        на всё дерево: родителя доступной подпапки в выборке может не
        быть. Без отдельной ветки такая папка потерялась бы в селекте."""
        hidden_parent = DepartmentFolder.objects.create(name='Скрытый родитель')
        child = DepartmentFolder.objects.create(name='Доступная', parent=hidden_parent)

        choices = build_folder_choices(DepartmentFolder.objects.filter(pk=child.pk))

        self.assertEqual([f.pk for f in choices], [child.pk])
        self.assertEqual(choices[0].indented_name, 'Доступная')


class ApplyFiltersTest(TestCase):
    """Поля расширенного фильтра раньше существовали только в разметке —
    четыре input'а и кнопки «Найти»/«Сбросить», не привязанные ни к чему."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='filter_user', password='x', last_name='Петрова', first_name='Анна',
        )
        self.other = User.objects.create_user(username='filter_other', password='x', last_name='Сидоров')

        folder = DepartmentFolder.objects.create(name='Отдел', created_by=self.user)
        folder.allowed_users.add(self.user)

        self.doc_a = self._document(folder, 'Акт сверки', self.user, b'a' * 100)
        self.doc_b = self._document(folder, 'Приказ об отпуске', self.other, b'b' * 4000)

    def _document(self, folder, title, user, content):
        uploaded = SimpleUploadedFile(f'{title}.pdf', content, content_type='application/pdf')
        file_object = StorageService.upload(uploaded, user=user, category=FileObject.Category.DOCUMENT)
        return DepartmentDocument.objects.create(
            file_object=file_object, folder=folder, title=title, uploaded_by=user,
        )

    def _filter(self, params):
        queryset, active = apply_filters(
            DepartmentDocument.objects.all(), params, name_field='title',
        )
        return list(queryset), active

    def test_filters_by_name(self):
        found, active = self._filter({'q': 'акт'})
        self.assertEqual(found, [self.doc_a])
        self.assertEqual(active['q'], 'акт')

    def test_filters_by_uploader(self):
        found, _ = self._filter({'uploader': 'Сидоров'})
        self.assertEqual(found, [self.doc_b])

    def test_filters_by_minimum_size(self):
        found, _ = self._filter({'size_min': '0.001'})  # ~1 КБ
        self.assertEqual(found, [self.doc_b])

    def test_invalid_values_are_ignored_instead_of_crashing(self):
        """Фильтр — вспомогательный инструмент: ронять из-за опечатки в
        поле весь список 500-й ошибкой незачем."""
        found, active = self._filter({'date_from': 'позавчера', 'size_min': 'много'})

        self.assertEqual(len(found), 2)
        self.assertEqual(active, {})


class RealtimeBroadcastTest(TestCase):

    def test_group_name_is_scoped_to_folder(self):
        self.assertEqual(
            realtime.group_name(realtime.SCOPE_EXCHANGE, realtime.exchange_location(7, 3)),
            'fm.exchange.7-3',
        )
        self.assertEqual(realtime.exchange_location(7, None), '7-0')
        self.assertEqual(realtime.folder_location(None), '0')

    def test_broadcast_failure_does_not_break_the_operation(self):
        """Живое обновление — удобство, а не часть операции. Если брокер
        недоступен, файл всё равно уже загружен, и превращать это в 500
        нельзя: пользователь получил бы ошибку на успешном действии, а
        повтор создал бы дубликат."""
        user = User.objects.create_user(username='rt_user', password='x')

        with mock.patch('storage.realtime.get_channel_layer') as get_layer:
            get_layer.return_value.group_send.side_effect = ConnectionError('redis is down')

            # Сбой не проглатывается бесследно: он уходит в лог, иначе
            # «живые обновления молча не работают» было бы нечем
            # диагностировать. assertLogs заодно убирает traceback из
            # вывода тестов.
            with self.assertLogs('storage.realtime', level='WARNING'):
                realtime.broadcast(
                    realtime.SCOPE_CATALOG, realtime.folder_location(None),
                    action='file_created', actor=user, text='добавил документ',
                )

    def test_upload_succeeds_when_broker_is_unreachable(self):
        user = User.objects.create_user(username='rt_upload_user', password='pass12345')
        self.client.force_login(user)

        with mock.patch('storage.realtime.get_channel_layer') as get_layer:
            get_layer.return_value.group_send.side_effect = ConnectionError('redis is down')

            uploaded = SimpleUploadedFile('rt.pdf', b'rt content', content_type='application/pdf')
            with self.assertLogs('storage.realtime', level='WARNING'):
                response = self.client.post(reverse('catalog_upload'), {'file': uploaded})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
