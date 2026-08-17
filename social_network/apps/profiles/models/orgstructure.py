"""Структура организации: подразделения, должности, звания, виды связи.

Справочники ведёт администратор — сотрудник выбирает из них, а не вписывает
текстом, иначе любой мог бы назначить себя начальником организации
(SESSION_CONTEXT.md, «Структура организации»).
"""
from django.db import models
from django.urls import reverse

from storage.utils import is_descendant


class Category(models.Model):
    """Подразделение сотрудника.

    Жило отдельным приложением `category` вместе с тремя вьюхами-копиями
    существующих страниц. Вьюхи схлопнуты в оригиналы, а справочник — часть
    структуры организации, рядом с Position и Rank.
    """

    name = models.CharField(max_length=100, db_index=True, verbose_name='Категория')

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['id']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('category', kwargs={'cat_id': self.pk})


class Rank(models.Model):
    """Звание — справочник, а не свободный текст.

    Свободным текстом одно и то же звание пишется десятком способов
    («майор», «Майор», «м-р»), и ни поиск, ни группировка по нему не
    работают. В отличие от должности, звание ничего не говорит о месте в
    структуре, поэтому сотрудник выбирает его себе сам — из этого списка.
    """

    name = models.CharField(max_length=100, unique=True, verbose_name='Звание')
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок в списке')

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Звание'
        verbose_name_plural = 'Звания'

    def __str__(self):
        return self.name


class Position(models.Model):
    """Должность — узел структуры организации.

    Справочник, а не CharField: вписать должность себе самому — всё равно что
    назначить себя начальником, поэтому список ведёт админ, а право выбрать
    её самому даёт `assignable_by_user`.

    Дерево на self-FK — тем же паттерном, что папки файлового менеджера,
    включая защиту от цикла: одной ошибки в админке хватило бы, чтобы обход
    структуры зациклился.
    """

    name = models.CharField(max_length=150, verbose_name='Должность')
    parent = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True, related_name='children',
        verbose_name='Подчиняется должности',
    )
    department = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='positions',
        verbose_name='Подразделение',
    )
    # 0 — сколько угодно. Единица нужна ровно там, где должность в
    # организации одна: начальник организации, начальник отдела. Это не
    # право доступа, а защита от опечатки кадровика — двух начальников
    # организации в структуре быть не может.
    max_holders = models.PositiveIntegerField(
        default=0, verbose_name='Сколько человек может занимать (0 — без ограничения)',
    )
    assignable_by_user = models.BooleanField(
        default=False, verbose_name='Сотрудник может выбрать эту должность сам',
        help_text='Оставьте выключенным для руководящих должностей — их назначает только администратор.',
    )
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок в списке')

    class Meta:
        ordering = ['order', 'name']
        unique_together = ('name', 'department')
        verbose_name = 'Должность'
        verbose_name_plural = 'Должности (структура организации)'

    def __str__(self):
        return self.name

    def clean(self):
        from django.core.exceptions import ValidationError
        from storage.utils import is_descendant

        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({'parent': 'Должность не может подчиняться сама себе'})
        if self.pk and self.parent_id and is_descendant(self.parent, self):
            raise ValidationError({'parent': 'Нельзя подчинить должность её же подчинённому — получится цикл'})

    @property
    def depth(self) -> int:
        """Глубина в дереве — для отступов в списках."""
        depth, node, seen = 0, self.parent, set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            depth += 1
            node = node.parent
        return depth

    def has_free_slot(self, *, exclude_user=None) -> bool:
        """Есть ли место на этой должности.

        exclude_user — тот, кто её уже занимает и просто пересохраняет
        профиль: без него человек на единственной должности не смог бы
        сохранить собственные настройки.
        """
        if not self.max_holders:
            return True

        holders = self.holders.all()
        if exclude_user is not None and exclude_user.pk:
            holders = holders.exclude(pk=exclude_user.pk)
        return holders.count() < self.max_holders


class PhoneType(models.Model):
    """Вид телефонного номера: «Город», «ПТС», «АТС-9», «ЗС», «HiCom».

    Раньше это были пять отдельных полей в User с подписями, вшитыми в
    шаблоны, и масками ввода, вшитыми в base.html. Шестой вид связи стоил бы
    миграции, правки формы и трёх шаблонов; теперь — одной записи в админке.
    """

    name = models.CharField(max_length=50, unique=True, verbose_name='Вид номера')
    # Маска ввода (jquery.maskedinput в base.html) хранится здесь, а не в
    # шаблоне: она — свойство вида связи, а не разметки. Пустая маска
    # означает «ввод без маски».
    mask = models.CharField(
        max_length=50, blank=True, verbose_name='Маска ввода',
        help_text='Например: 8 (999) 999-99-99. Пусто — вводить как есть.',
    )
    order = models.PositiveIntegerField(default=0, verbose_name='Порядок в списке')

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Вид телефонного номера'
        verbose_name_plural = 'Виды телефонных номеров'

    def __str__(self):
        return self.name


class UserPhone(models.Model):
    """Номер сотрудника определённого вида. Сам номер вводит сотрудник, вид
    заводит админ."""

    user = models.ForeignKey(
        'User', on_delete=models.CASCADE, related_name='phones', verbose_name='Сотрудник',
    )
    # PROTECT, а не CASCADE: удаление вида связи в админке не должно тихо
    # стирать номера у всех сотрудников — сначала пусть станет видно, что
    # вид используется.
    type = models.ForeignKey(
        PhoneType, on_delete=models.PROTECT, related_name='numbers', verbose_name='Вид номера',
    )
    number = models.CharField(max_length=30, verbose_name='Номер')

    class Meta:
        ordering = ['type__order', 'type__name']
        unique_together = ('user', 'type')
        verbose_name = 'Телефон сотрудника'
        verbose_name_plural = 'Телефоны сотрудников'

    def __str__(self):
        return f'{self.type}: {self.number}'
