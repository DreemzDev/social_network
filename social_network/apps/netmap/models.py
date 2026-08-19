"""Карта сети: какой адрес каким устройством занят и когда отвечал.

Адрес, а не устройство — главная сущность: адреса статические, инвентарь
ведётся вокруг них. Записи заводятся только для известных адресов, свободные
считаются как разница с диапазоном подсети — иначе на каждую /24 в БД лежало
бы 254 пустые строки.
"""
import ipaddress

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Subnet(models.Model):
    """Подсеть, которую обходит портал."""

    cidr = models.CharField(
        max_length=18, unique=True, verbose_name='Подсеть',
        help_text='Например, 10.10.0.0/24',
    )
    name = models.CharField(max_length=255, blank=True, verbose_name='Название')
    is_scan_enabled = models.BooleanField(default=True, verbose_name='Обходить')
    # Проба портов включена по умолчанию: одного ping мало — Windows штатно
    # режет ICMP файрволом, и живая машина выглядит выключенной. Выключается
    # для сетей, где есть оборудование, плохо переносящее стук в порты.
    probe_ports = models.BooleanField(default=True, verbose_name='Проверять порты')
    note = models.TextField(blank=True, verbose_name='Примечание')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['cidr']
        verbose_name = 'Подсеть'
        verbose_name_plural = 'Подсети'

    def __str__(self):
        return f'{self.cidr} — {self.name}' if self.name else self.cidr

    def clean(self):
        try:
            self.network
        except ValueError as e:
            raise ValidationError({'cidr': f'Не похоже на подсеть: {e}'})

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.ip_network(self.cidr, strict=True)

    def hosts(self):
        """Адреса, которые имеет смысл опрашивать (без адреса сети и бродкаста)."""
        return [str(ip) for ip in self.network.hosts()]

    def free_addresses(self):
        """Свободные адреса — то, ради чего справочник чаще всего и открывают."""
        taken = set(self.addresses.values_list('ip', flat=True))
        return [ip for ip in self.hosts() if ip not in taken]


class NetworkAddress(models.Model):
    """Адрес в подсети: справочная часть заполняется человеком, техническая —
    обходом. Разделение важно: обход не должен затирать то, что вписали руками.
    """

    class Kind(models.TextChoices):
        WORKSTATION = 'workstation', 'Рабочее место'
        SERVER = 'server', 'Сервер'
        PRINTER = 'printer', 'Принтер или МФУ'
        NETWORK = 'network', 'Сетевое оборудование'
        PHONE = 'phone', 'IP-телефон'
        CAMERA = 'camera', 'Камера'
        OTHER = 'other', 'Прочее'

    class OsGuess(models.TextChoices):
        WINDOWS = 'windows', 'Windows'
        LINUX = 'linux', 'Linux / Астра'
        DEVICE = 'device', 'Сетевое устройство'
        UNKNOWN = '', 'Не определена'

    subnet = models.ForeignKey(Subnet, on_delete=models.CASCADE, related_name='addresses')
    ip = models.GenericIPAddressField(protocol='IPv4', unique=True, verbose_name='IP-адрес')

    # --- заполняется человеком ---
    name = models.CharField(max_length=255, blank=True, verbose_name='Название устройства')
    kind = models.CharField(
        max_length=20, choices=Kind.choices, blank=True, verbose_name='Тип',
    )
    responsible = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Ответственный',
    )
    room = models.CharField(max_length=50, blank=True, verbose_name='Кабинет')
    note = models.TextField(blank=True, verbose_name='Примечание')
    # Часть оборудования плохо переносит опрос — такие адреса обход пропускает.
    is_excluded = models.BooleanField(default=False, verbose_name='Не опрашивать')

    # --- заполняется обходом ---
    hostname = models.CharField(max_length=255, blank=True, verbose_name='Имя в DNS')
    mac = models.CharField(max_length=17, blank=True, verbose_name='MAC')
    os_guess = models.CharField(max_length=20, choices=OsGuess.choices, blank=True)
    open_ports = models.JSONField(default=list, blank=True)
    ttl = models.PositiveSmallIntegerField(null=True, blank=True)
    # Чем именно ответил адрес. Хранится явно, потому что «не ответил на ping»
    # и «выключен» — разные вещи: ICMP может резать файрвол.
    reply_method = models.CharField(max_length=20, blank=True, verbose_name='Ответил через')
    last_seen_at = models.DateTimeField(null=True, blank=True, verbose_name='Последний ответ')
    last_scan_at = models.DateTimeField(null=True, blank=True, verbose_name='Последний опрос')

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        # GenericIPAddressField в PostgreSQL это inet, поэтому сортировка
        # числовая: .9 не окажется после .10, как было бы у строки.
        ordering = ['ip']
        verbose_name = 'Адрес'
        verbose_name_plural = 'Адреса'

    def __str__(self):
        return f'{self.ip} — {self.name}' if self.name else self.ip

    @property
    def is_online(self) -> bool:
        """Отвечал ли при последнем обходе. Не «включён», а именно «ответил»."""
        return bool(self.last_seen_at and self.last_scan_at and self.last_seen_at >= self.last_scan_at)

    def apply_probe(self, probe):
        """Переносит результат опроса в запись, не трогая то, что вписано руками."""
        now = timezone.now()
        self.last_scan_at = now
        self.ttl = probe.ttl
        self.open_ports = probe.open_ports
        self.reply_method = probe.method
        if probe.hostname:
            self.hostname = probe.hostname
        if probe.mac:
            self.mac = probe.mac
        if probe.os_guess:
            self.os_guess = probe.os_guess
        if probe.alive:
            self.last_seen_at = now

    SCAN_FIELDS = [
        'hostname', 'mac', 'os_guess', 'open_ports', 'ttl',
        'reply_method', 'last_seen_at', 'last_scan_at',
    ]


class ScanRun(models.Model):
    """Журнал обходов: кто запустил, когда и что получилось.

    Обход сети — заметное для сети действие, и если о нём спросят, должно быть
    видно, что именно делал портал (тот же смысл, что у StorageAuditLog).
    """

    subnet = models.ForeignKey(Subnet, on_delete=models.CASCADE, related_name='runs')
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    scanned = models.PositiveIntegerField(default=0, verbose_name='Опрошено адресов')
    responded = models.PositiveIntegerField(default=0, verbose_name='Ответили')
    created = models.PositiveIntegerField(default=0, verbose_name='Новых записей')
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Обход сети'
        verbose_name_plural = 'Обходы сети'

    def __str__(self):
        return f'{self.subnet.cidr} {self.started_at:%d.%m.%Y %H:%M}'
