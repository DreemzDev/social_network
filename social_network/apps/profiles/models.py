from django.db import models
from category.models import Category
# from django.contrib.auth.models import User
from django.dispatch import receiver
from django.urls import reverse
from django.db.models.signals import post_save
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
from datetime import date, datetime, timedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.contrib.auth.signals import user_logged_in, user_logged_out


# Create your models here.

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

    Почему справочник, а не CharField: должность говорит не о человеке, а о
    его месте в организации, и вписать её себе самому — всё равно что
    назначить себя начальником. Поэтому список ведёт админ, а кто может
    выбрать должность сам, решает флаг assignable_by_user.

    Почему с parent: иерархия («начальник организации → заместитель →
    начальник отдела») — это дерево, и оно уже есть в жизни организации.
    Самостоятельные должности (юрист и подобные) — узлы без потомков,
    подчинённые напрямую верхнему уровню; отдельной сущности для них не
    нужно.

    Дерево — тем же паттерном self-FK, что и папки файлового менеджера
    (CatalogFolder/ExchangeFolder), включая защиту от цикла: без неё
    достаточно одной ошибки в админке, чтобы обход структуры зациклился.
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

        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({'parent': 'Должность не может подчиняться сама себе'})
        if self.pk and self.parent_id and self._is_descendant(self.parent):
            raise ValidationError({'parent': 'Нельзя подчинить должность её же подчинённому — получится цикл'})

    def _is_descendant(self, candidate) -> bool:
        """Лежит ли candidate внутри поддерева этой должности."""
        node, seen = candidate, set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            if node.parent_id == self.pk:
                return True
            node = node.parent
        return False

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


class User(AbstractUser):

    avatar = models.ImageField(upload_to = 'avatar/', blank=True,null=True, verbose_name="Аватар")
    cover = models.ImageField(upload_to='cover/', blank=True, null=True, verbose_name="Обложка профиля")
    cat = models.ForeignKey(Category, on_delete=models.PROTECT, null=True,blank=True, verbose_name="Категории")
    # SET_NULL, а не PROTECT: удаление должности из справочника — это
    # изменение структуры организации, а не повод запретить его из-за того,
    # что кто-то её занимает. Человек останется без должности, и это видно.
    position = models.ForeignKey(
        Position, on_delete=models.SET_NULL, null=True, blank=True, related_name='holders',
        verbose_name="Должность",
    )
    rank = models.ForeignKey(
        Rank, on_delete=models.SET_NULL, null=True, blank=True, related_name='holders',
        verbose_name="Звание",
    )
    patronymic = models.CharField( max_length=100, blank=True, verbose_name="Отчество" )
    birthday = models.DateField(null=True, blank=True, verbose_name="Дата рождения")
    cab = models.CharField(max_length=10, blank=True, verbose_name="Номер кабинета" )
    # Телефоны — в UserPhone: видов связи в организации больше, чем пять, и
    # шестой не должен стоить миграции и правки трёх шаблонов.
    last_activity = models.DateTimeField(default=timezone.now, verbose_name="Последняя активность")
    security_answer = models.CharField(max_length=255, blank=True, verbose_name="Проверочное слово для сброса пароля")

    class EmployeeStatus(models.TextChoices):
        AT_WORK = 'at_work', 'На месте'
        SICK_LEAVE = 'sick_leave', 'Больничный'
        BUSINESS_TRIP = 'business_trip', 'Командировка'
        DO_NOT_DISTURB = 'do_not_disturb', 'Не беспокоить'

    employee_status = models.CharField(max_length=20, choices=EmployeeStatus.choices,
        default=EmployeeStatus.AT_WORK, verbose_name="Статус сотрудника")

    def get_absolute_url(self):
        return reverse('profile', kwargs={'username': self.username})

    @property
    def is_online(self):
        """Проверяет онлайн статус на основе последней активности"""
        if not self.last_activity:
            return False
        return (timezone.now() - self.last_activity) < timedelta(minutes=5)

    @property
    def online_status(self):
        """Возвращает детальный статус для отображения"""
        if not self.last_activity:
            return "offline"
            
        time_diff = timezone.now() - self.last_activity
        
        if time_diff < timedelta(minutes=5):
            return "online"
        elif time_diff < timedelta(minutes=15):
            return "recently"
        else:
            return "offline"

    _ONLINE_STATUS_LABELS = {
        "online": "Онлайн",
        "recently": "Был(а) недавно",
        "offline": "Офлайн",
    }

    def get_online_status_display(self):
        """Текстовое представление online_status.

        Не называется get_status_display() — у User нет поля status,
        и такое имя выглядело бы как автосгенерированный Django-метод
        для поля с choices (как get_status_display() у Task), но им не является."""
        return self._ONLINE_STATUS_LABELS.get(self.online_status, "Офлайн")

    def __str__(self):
        return f"{self.last_name} {self.first_name}"

    class Meta:
        verbose_name = 'Личный кабинет'
        verbose_name_plural = 'Личные кабинеты'

class MessageReaction(models.Model):
    message = models.ForeignKey('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='reactions', verbose_name="Сообщение")
    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, verbose_name="Пользователь")
    emoji = models.CharField(max_length=8, verbose_name="Эмодзи")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Время создания")

    class Meta:
        unique_together = ('message', 'user')
        ordering = ('emoji',)
        verbose_name = 'Реакция на сообщение'
        verbose_name_plural = 'Реакции на сообщения'

    def __str__(self):
        return f"{self.user} {self.emoji} on message {self.message_id}"


class MessageReply(models.Model):
    """Связывает сообщение с тем, на которое оно отвечает. Отдельная
    модель-расширение (как MessageReaction) — сама MessageModel из
    django_private_chat2 не трогается, чтобы не форкать миграции сторонней
    библиотеки."""
    message = models.OneToOneField('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='reply_info', verbose_name="Сообщение-ответ")
    reply_to = models.ForeignKey('django_private_chat2.MessageModel', on_delete=models.CASCADE, related_name='replies', verbose_name="Исходное сообщение")

    def __str__(self):
        return f"Message {self.message_id} replies to {self.reply_to_id}"


class Task(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Не начата'
        IN_PROGRESS = 'in_progress', 'В работе'
        DONE = 'done', 'Выполнена'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='tasks', verbose_name="Исполнитель")
    assigned_by = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name='assigned_tasks',
        null=True, blank=True, verbose_name="Постановщик"
    )
    title = models.CharField(max_length=255, verbose_name="Задача")
    description = models.TextField(blank=True, verbose_name="Описание")
    due_date = models.DateField(null=True, blank=True, verbose_name="Срок выполнения")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED, verbose_name="Статус")
    is_completed = models.BooleanField(default=False, verbose_name="Выполнено")
    is_edited = models.BooleanField(default=False, verbose_name="Изменена постановщиком после создания")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['is_completed', 'due_date', '-created']
        verbose_name = 'Задача'
        verbose_name_plural = 'Задачи'

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        self.is_completed = self.status == self.Status.DONE
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return bool(self.due_date and not self.is_completed and self.due_date < date.today())


class Note(models.Model):
    class Color(models.TextChoices):
        YELLOW = 'yellow', 'Жёлтый'
        PINK = 'pink', 'Розовый'
        BLUE = 'blue', 'Голубой'
        GREEN = 'green', 'Зелёный'
        PURPLE = 'purple', 'Фиолетовый'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='notes', verbose_name="Пользователь")
    content = models.TextField(blank=True, verbose_name="Текст заметки")
    color = models.CharField(max_length=10, choices=Color.choices, default=Color.YELLOW, verbose_name="Цвет")
    position = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    updated = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ['position', '-updated']
        verbose_name = 'Личная заметка'
        verbose_name_plural = 'Личные заметки'

    def __str__(self):
        return f"Заметка {self.user}"


class Event(models.Model):
    class EventType(models.TextChoices):
        PERSONAL = 'personal', 'Личное'
        CORPORATE = 'corporate', 'Корпоративное'

    class Recurrence(models.TextChoices):
        NONE = 'none', 'Не повторять'
        WEEKLY = 'weekly', 'Каждую неделю'
        MONTHLY = 'monthly', 'Каждый месяц'
        YEARLY = 'yearly', 'Каждый год'

    user = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='events', null=True, blank=True, verbose_name="Пользователь")
    created_by = models.ForeignKey(
        get_user_model(), on_delete=models.CASCADE, related_name='created_events',
        null=True, blank=True, verbose_name="Автор события"
    )
    share_group = models.UUIDField(null=True, blank=True, editable=False, verbose_name="Группа общего события")
    title = models.CharField(max_length=255, verbose_name="Название")
    description = models.TextField(blank=True, verbose_name="Описание")
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.PERSONAL, verbose_name="Тип события")
    recurrence = models.CharField(max_length=10, choices=Recurrence.choices, default=Recurrence.NONE, verbose_name="Повтор")
    date = models.DateField(verbose_name="Дата")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    source_task = models.OneToOneField(
        'Task', on_delete=models.CASCADE, null=True, blank=True,
        related_name='calendar_event', verbose_name="Связанная задача"
    )

    class Meta:
        ordering = ['date']
        verbose_name = 'Событие календаря'
        verbose_name_plural = 'События календаря'

    def __str__(self):
        return f"{self.title} ({self.date})"


class Notification(models.Model):
    class Kind(models.TextChoices):
        TASK_ASSIGNED = 'task_assigned', 'Поставлена задача'
        TASK_EDITED = 'task_edited', 'Задача изменена'
        TASK_STATUS_CHANGED = 'task_status_changed', 'Статус задачи изменён'
        POST_COMMENTED = 'post_commented', 'Новый комментарий к посту'
        FILE_SHARED = 'file_shared', 'Новый файл'

    recipient = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='notifications', verbose_name="Получатель")
    actor = models.ForeignKey(get_user_model(), on_delete=models.CASCADE, related_name='+', null=True, blank=True, verbose_name="Инициатор")
    kind = models.CharField(max_length=30, choices=Kind.choices, verbose_name="Тип")
    text = models.CharField(max_length=255, verbose_name="Текст")
    task = models.ForeignKey('Task', on_delete=models.CASCADE, null=True, blank=True, related_name='notifications', verbose_name="Задача")
    post = models.ForeignKey('posts.Post', on_delete=models.CASCADE, null=True, blank=True, related_name='notifications', verbose_name="Пост")
    # Готовая ссылка вместо FK на конкретный объект — файловые уведомления
    # (FILE_SHARED) могут указывать на обменник, каталог или приватный
    # доступ, и заводить по FK на каждый из трёх модулей ради одной ссылки
    # избыточно: сама ссылка не участвует ни в какой бизнес-логике, кроме
    # перехода по клику.
    url = models.CharField(max_length=255, blank=True, verbose_name="Ссылка")
    is_read = models.BooleanField(default=False, verbose_name="Прочитано")
    created = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        ordering = ['-created']
        verbose_name = 'Уведомление'
        verbose_name_plural = 'Уведомления'

    def __str__(self):
        return self.text


@receiver(user_logged_in)
def user_logged_in_callback(sender, request, user, **kwargs):
    """Обновляем время активности при входе"""
    user.last_activity = timezone.now()
    user.save(update_fields=['last_activity'])

@receiver(user_logged_out)
def user_logged_out_callback(sender, request, user, **kwargs):
    """Обновляем время активности при выходе"""
    user.last_activity = timezone.now()
    user.save(update_fields=['last_activity'])