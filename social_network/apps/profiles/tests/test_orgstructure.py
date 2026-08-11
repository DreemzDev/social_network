"""Структура организации: должности, звания и виды телефонных номеров.

Раньше должность и звание были свободным текстом в профиле, а телефоны —
пятью полями модели с подписями, вшитыми в три шаблона. Из этого следовало
главное, ради чего всё переделано: **любой сотрудник мог вписать себе
«Начальник организации»** и перевести себя в чужое подразделение, потому
что оба поля стояли в форме настроек.

Тесты закрепляют новые правила:
  - структуру (должность, подразделение) назначает администратор;
  - исключение — должности с флагом assignable_by_user;
  - звание сотрудник выбирает сам, но из справочника;
  - виды телефонов ведутся в админке, номера — сотрудником.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from category.models import Category
from profiles.forms import SettingProfileForm, UserPhonesForm
from profiles.models import PhoneType, Position, Rank, UserPhone

User = get_user_model()


class PositionTreeTest(TestCase):
    """Иерархия должностей — дерево, и оно должно оставаться деревом."""

    def setUp(self):
        self.chief = Position.objects.create(name='Начальник организации', max_holders=1)
        self.deputy = Position.objects.create(name='Заместитель организации', parent=self.chief)
        self.head = Position.objects.create(name='Начальник первого отдела', parent=self.deputy)

    def test_depth_counts_levels(self):
        self.assertEqual(self.chief.depth, 0)
        self.assertEqual(self.deputy.depth, 1)
        self.assertEqual(self.head.depth, 2)

    def test_position_cannot_report_to_itself(self):
        self.chief.parent = self.chief
        with self.assertRaises(ValidationError):
            self.chief.full_clean()

    def test_position_cannot_report_to_its_own_subordinate(self):
        """Цикл в дереве повесил бы любой обход структуры — ровно та же
        защита, что у папок файлового менеджера."""
        self.chief.parent = self.head
        with self.assertRaises(ValidationError):
            self.chief.full_clean()

    def test_standalone_position_has_no_department(self):
        """Юрист и подобные — узел без отдела, отдельной сущности для них
        заводить не нужно."""
        lawyer = Position.objects.create(name='Юрист', parent=self.chief)

        self.assertIsNone(lawyer.department_id)
        self.assertEqual(lawyer.depth, 1)


class PositionCapacityTest(TestCase):

    def setUp(self):
        self.chief = Position.objects.create(name='Начальник организации', max_holders=1)
        self.clerk = Position.objects.create(name='Делопроизводитель')  # 0 — без ограничения

    def test_position_without_limit_always_has_room(self):
        for index in range(3):
            User.objects.create_user(username=f'clerk{index}', password='x', position=self.clerk)

        self.assertTrue(self.clerk.has_free_slot())

    def test_single_holder_position_fills_up(self):
        User.objects.create_user(username='boss', password='x', position=self.chief)

        self.assertFalse(self.chief.has_free_slot())

    def test_current_holder_does_not_block_himself(self):
        """Иначе начальник организации не смог бы сохранить собственные
        настройки: его же должность считалась бы занятой."""
        boss = User.objects.create_user(username='boss2', password='x', position=self.chief)

        self.assertTrue(self.chief.has_free_slot(exclude_user=boss))


class SettingProfileFormTest(TestCase):
    """Что сотрудник может поставить себе сам."""

    def setUp(self):
        self.chief = Position.objects.create(name='Начальник организации', max_holders=1)
        self.specialist = Position.objects.create(name='Специалист', assignable_by_user=True)
        self.rank = Rank.objects.create(name='Майор')
        self.department = Category.objects.create(name='Первый отдел')
        self.user = User.objects.create_user(username='employee', password='pass12345')

    def test_department_is_not_self_service(self):
        """Подразделение — такое же место в структуре, как должность."""
        self.assertNotIn('cat', SettingProfileForm(instance=self.user).fields)

    def test_only_assignable_positions_are_offered(self):
        choices = list(SettingProfileForm(instance=self.user).fields['position'].queryset)

        self.assertIn(self.specialist, choices)
        self.assertNotIn(self.chief, choices)

    def test_own_position_stays_in_the_list(self):
        """Иначе сохранение настроек молча снимало бы человека с должности,
        которую ему назначил администратор."""
        self.user.position = self.chief
        self.user.save(update_fields=['position'])

        choices = list(SettingProfileForm(instance=self.user).fields['position'].queryset)

        self.assertIn(self.chief, choices)

    def test_assigning_a_forbidden_position_is_rejected(self):
        form = SettingProfileForm(
            data={'position': self.chief.pk, 'rank': self.rank.pk}, instance=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('position', form.errors)

    def test_assignable_position_is_accepted(self):
        form = SettingProfileForm(
            data={'position': self.specialist.pk, 'rank': self.rank.pk}, instance=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.position, self.specialist)

    def test_occupied_single_holder_position_is_rejected_with_reason(self):
        """Контрол обязан объяснить отказ, а не молча не сработать."""
        self.chief.assignable_by_user = True
        self.chief.save(update_fields=['assignable_by_user'])
        User.objects.create_user(username='already', password='x', position=self.chief)

        form = SettingProfileForm(data={'position': self.chief.pk}, instance=self.user)

        self.assertFalse(form.is_valid())
        self.assertIn('занята', form.errors['position'][0])

    def test_position_field_disappears_when_there_is_nothing_to_choose(self):
        """Контрол либо работает, либо его нет в разметке (ARCHITECTURE 12.4):
        селект без единого варианта — это второе."""
        Position.objects.update(assignable_by_user=False)

        self.assertNotIn('position', SettingProfileForm(instance=self.user).fields)

    def test_rank_is_free_choice_from_the_dictionary(self):
        """Звание не говорит о подчинении, поэтому ограничивать выбор незачем
        — но и вписывать своё руками больше нельзя."""
        form = SettingProfileForm(data={'rank': self.rank.pk}, instance=self.user)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(self.rank, form.fields['rank'].queryset)


class PhoneDictionaryTestCase(TestCase):
    """Справочник видов связи задаёт сам тест.

    Миграция 0019 заводит пять реальных видов («Город», «HiCom», «ПТС»,
    «АТС-9», «ЗС») — они есть в каждой свежей БД, включая тестовую. Тесту
    про механику справочника они мешают: он проверяет, что набор полей
    следует за содержимым таблицы, а не за конкретными пятью строками.
    Ссылок на них в чистой тестовой БД нет, поэтому удаление безопасно.
    """

    def setUp(self):
        PhoneType.objects.all().delete()


class UserPhonesFormTest(PhoneDictionaryTestCase):

    def setUp(self):
        super().setUp()
        self.city = PhoneType.objects.create(name='Город', mask='8 (999) 999-99-99', order=1)
        self.pts = PhoneType.objects.create(name='ПТС', mask='999-99', order=2)
        self.user = User.objects.create_user(username='phones', password='pass12345')

    def field(self, phone_type):
        return UserPhonesForm.field_name(phone_type)

    def test_fields_come_from_the_dictionary(self):
        """Шестой вид связи — запись в админке, а не миграция и правка трёх
        шаблонов."""
        form = UserPhonesForm(self.user)

        self.assertEqual(len(form.rows()), 2)

        PhoneType.objects.create(name='Спутниковый', order=3)

        self.assertEqual(len(UserPhonesForm(self.user).rows()), 3)

    def test_numbers_are_saved(self):
        form = UserPhonesForm(self.user, {self.field(self.city): '8 (495) 123-45-67'})

        self.assertTrue(form.is_valid())
        form.save()

        self.assertEqual(self.user.phones.get(type=self.city).number, '8 (495) 123-45-67')

    def test_number_is_updated_not_duplicated(self):
        UserPhone.objects.create(user=self.user, type=self.pts, number='11-11')

        form = UserPhonesForm(self.user, {self.field(self.pts): '22-22'})
        self.assertTrue(form.is_valid())
        form.save()

        self.assertEqual(self.user.phones.count(), 1)
        self.assertEqual(self.user.phones.get().number, '22-22')

    def test_clearing_a_number_removes_the_record(self):
        """Пустая строка — это «номера нет», а не номер из пробелов:
        иначе справочник постепенно зарос бы пустыми записями, которые
        шаблонам пришлось бы отфильтровывать."""
        UserPhone.objects.create(user=self.user, type=self.pts, number='11-11')

        form = UserPhonesForm(self.user, {self.field(self.pts): '   '})
        self.assertTrue(form.is_valid())
        form.save()

        self.assertFalse(self.user.phones.exists())

    def test_phone_type_in_use_cannot_be_deleted_silently(self):
        """PROTECT: удаление вида связи не должно тихо стирать номера у всех
        сотрудников."""
        from django.db.models import ProtectedError

        UserPhone.objects.create(user=self.user, type=self.pts, number='11-11')

        with self.assertRaises(ProtectedError):
            self.pts.delete()


class SettingsPageTest(PhoneDictionaryTestCase):
    """Страница настроек — то же, что видит сотрудник."""

    def setUp(self):
        super().setUp()
        self.pts = PhoneType.objects.create(name='ПТС', mask='999-99')
        self.specialist = Position.objects.create(name='Специалист', assignable_by_user=True)
        self.chief = Position.objects.create(name='Начальник организации')
        self.user = User.objects.create_user(username='page_user', password='pass12345')
        self.client.force_login(self.user)

    def test_phone_inputs_are_rendered_with_their_mask(self):
        """Маска — свойство вида связи: base.html применяет её по
        data-phone-mask, а не по пяти прошитым id."""
        response = self.client.get(reverse('settingprofile'))

        self.assertContains(response, 'Телефон ПТС')
        self.assertContains(response, 'data-phone-mask="999-99"')

    def test_forbidden_position_is_not_in_the_page(self):
        response = self.client.get(reverse('settingprofile'))

        self.assertContains(response, 'Специалист')
        self.assertNotContains(response, 'Начальник организации')

    def test_saving_settings_stores_phones(self):
        response = self.client.post(reverse('settingprofile'), {
            'first_name': 'Иван',
            UserPhonesForm.field_name(self.pts): '12-34',
        })

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Иван')
        self.assertEqual(self.user.phones.get(type=self.pts).number, '12-34')

    def test_posting_a_forbidden_position_changes_nothing(self):
        """Подделанный запрос в обход интерфейса: id должности, которой нет
        в списке, форма не примет."""
        response = self.client.post(reverse('settingprofile'), {'position': self.chief.pk})

        self.assertEqual(response.status_code, 200)  # форма вернулась с ошибкой
        self.user.refresh_from_db()
        self.assertIsNone(self.user.position_id)


class PhonebookPageTest(PhoneDictionaryTestCase):

    def test_directory_shows_numbers_from_the_dictionary(self):
        phone_type = PhoneType.objects.create(name='Город')
        user = User.objects.create_user(
            username='directory', password='pass12345', first_name='Пётр', last_name='Петров',
        )
        UserPhone.objects.create(user=user, type=phone_type, number='8 (495) 000-00-00')
        self.client.force_login(user)

        response = self.client.get(reverse('show_phones'))

        self.assertContains(response, 'Город')
        self.assertContains(response, '8 (495) 000-00-00')
