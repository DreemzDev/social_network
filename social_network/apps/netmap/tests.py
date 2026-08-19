"""Карта сети: разбор вывода утилит, определение ОС, свободные адреса, доступ.

Сеть в тестах не трогается: проверяется разбор того, что утилиты вернули, —
именно там ошибки и живут. Вывод `ping` и `arp` локализован, и на русской
Windows он другой, чем на Астре, поэтому разбор обязан не зависеть от языка.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from netmap.models import NetworkAddress, ScanRun, Subnet
from netmap.scanner import Probe, guess_os, parse_arp
from netmap.services import run_scan

User = get_user_model()


class ArpParsingTest(TestCase):
    """ARP-таблица — единственное место, где приходится разбирать текст."""

    WINDOWS_RU = """
Интерфейс: 10.10.0.5 --- 0x12
  адрес в Интернете      Физический адрес      Тип
  10.10.0.1             9c-57-ad-30-0f-c0     динамический
  10.10.0.23            ff-ff-ff-ff-ff-ff     статический
  224.0.0.22            01-00-5e-00-00-16     статический
  239.255.255.250       01-00-5e-7f-ff-fa     статический
"""

    LINUX = """
10.10.0.1 dev eth0 lladdr 9c:57:ad:30:0f:c0 REACHABLE
10.10.0.7 dev eth0 lladdr 00:1b:21:3c:4d:5e STALE
10.10.0.9 dev eth0  FAILED
"""

    def test_windows_output_parsed_regardless_of_locale(self):
        """Слова в выводе переведены, адрес и MAC — нет. Разбор цепляется
        только за них, иначе на русской Windows таблица была бы пустой."""
        table = parse_arp(self.WINDOWS_RU)

        self.assertEqual(table['10.10.0.1'], '9c:57:ad:30:0f:c0')
        self.assertNotIn('224.0.0.22', table, 'multicast — не устройство сети')
        self.assertNotIn('239.255.255.250', table, 'multicast — не устройство сети')
        self.assertNotIn('10.10.0.23', table, 'широковещательный MAC ничего не значит')

    def test_linux_output_parsed_by_the_same_code(self):
        """`ip neigh` даёт другую строку, но те же данные — второй разбор
        завёл бы вторую реализацию, которая разойдётся с первой."""
        table = parse_arp(self.LINUX)

        self.assertEqual(table['10.10.0.1'], '9c:57:ad:30:0f:c0')
        self.assertEqual(table['10.10.0.7'], '00:1b:21:3c:4d:5e')
        self.assertNotIn('10.10.0.9', table, 'у записи FAILED нет MAC')

    def test_windows_mac_separator_normalised(self):
        """Windows пишет MAC через дефис, Linux — через двоеточие. Если не
        приводить к одному виду, один и тот же адрес выглядел бы как два."""
        table = parse_arp('  10.10.0.1   9C-57-AD-30-0F-C0   динамический')

        self.assertEqual(table['10.10.0.1'], '9c:57:ad:30:0f:c0')


class OsGuessTest(TestCase):
    """Определение ОС: порты весомее TTL."""

    def test_ports_win_over_ttl(self):
        """TTL убывает на каждом маршрутизаторе и настраивается, а открытый
        22-й порт говорит о системе прямо."""
        self.assertEqual(guess_os(128, [22]), 'linux')
        self.assertEqual(guess_os(64, [3389]), 'windows')

    def test_ttl_used_when_no_ports(self):
        self.assertEqual(guess_os(128, []), 'windows')
        self.assertEqual(guess_os(64, []), 'linux')
        self.assertEqual(guess_os(255, []), 'device')

    def test_printer_ports_recognised_as_device(self):
        self.assertEqual(guess_os(64, [9100, 80]), 'device')

    def test_nothing_known_gives_nothing(self):
        self.assertEqual(guess_os(None, []), '')


class SubnetTest(TestCase):

    def setUp(self):
        self.subnet = Subnet.objects.create(cidr='10.10.0.0/24', name='Основная')

    def test_hosts_exclude_network_and_broadcast(self):
        hosts = self.subnet.hosts()

        self.assertEqual(len(hosts), 254)
        self.assertEqual(hosts[0], '10.10.0.1')
        self.assertEqual(hosts[-1], '10.10.0.254')

    def test_free_addresses_are_the_difference(self):
        """Записи заводятся только занятым адресам, свободные считаются —
        иначе на каждую /24 в БД лежало бы 254 пустые строки."""
        NetworkAddress.objects.create(subnet=self.subnet, ip='10.10.0.5')

        free = self.subnet.free_addresses()

        self.assertEqual(len(free), 253)
        self.assertNotIn('10.10.0.5', free)


class RunScanTest(TestCase):
    """Перенос результата обхода в записи. Сеть подменена: проверяется, что
    делает код с полученным, а не работает ли сеть."""

    def setUp(self):
        self.subnet = Subnet.objects.create(cidr='10.10.0.0/30')
        self.user = User.objects.create_user(username='netadmin', password='pass12345', is_staff=True)

    def scan_returning(self, probes):
        return patch('netmap.services.scan', return_value=probes)

    def test_record_created_only_for_responding_address(self):
        probes = [
            Probe(ip='10.10.0.1', alive=True, ttl=64, method='ping', os_guess='linux'),
            Probe(ip='10.10.0.2', alive=False),
        ]
        with self.scan_returning(probes):
            run = run_scan(self.subnet, user=self.user)

        self.assertEqual(run.responded, 1)
        self.assertEqual(run.created, 1)
        self.assertEqual([a.ip for a in self.subnet.addresses.all()], ['10.10.0.1'])

    def test_manual_fields_survive_the_scan(self):
        """Главное свойство: обход не затирает то, что вписал человек.
        Иначе первый же ночной обход стёр бы ответственных и кабинеты."""
        address = NetworkAddress.objects.create(
            subnet=self.subnet, ip='10.10.0.1', name='ПК бухгалтерии',
            room='301', responsible=self.user, kind=NetworkAddress.Kind.WORKSTATION,
        )

        probes = [Probe(ip='10.10.0.1', alive=True, hostname='buh.local', method='ping')]
        with self.scan_returning(probes):
            run_scan(self.subnet)

        address.refresh_from_db()
        self.assertEqual(address.name, 'ПК бухгалтерии')
        self.assertEqual(address.room, '301')
        self.assertEqual(address.responsible, self.user)
        self.assertEqual(address.hostname, 'buh.local', 'данные обхода при этом обновились')

    def test_silent_known_address_keeps_previous_data(self):
        """Молчащему адресу обновляется только время опроса: «сегодня не
        ответил» не значит «имя и MAC устарели»."""
        with self.scan_returning([Probe(ip='10.10.0.1', alive=True, hostname='pc.local', mac='aa:bb:cc:dd:ee:ff')]):
            run_scan(self.subnet)

        with self.scan_returning([Probe(ip='10.10.0.1', alive=False)]):
            run_scan(self.subnet)

        address = NetworkAddress.objects.get(ip='10.10.0.1')
        self.assertEqual(address.hostname, 'pc.local')
        self.assertEqual(address.mac, 'aa:bb:cc:dd:ee:ff')
        self.assertFalse(address.is_online, 'при последнем обходе адрес молчал')

    def test_excluded_address_is_not_probed(self):
        """Часть оборудования плохо переносит опрос — пометка обязана
        исключать адрес из обхода, а не просто прятать его в списке."""
        NetworkAddress.objects.create(subnet=self.subnet, ip='10.10.0.1', is_excluded=True)

        with patch('netmap.services.scan', return_value=[]) as scan_mock:
            run_scan(self.subnet)

        targets = scan_mock.call_args[0][0]
        self.assertNotIn('10.10.0.1', targets)

    def test_failure_is_recorded_not_swallowed(self):
        """Упавший обход должен остаться в журнале: молча пропавший результат
        выглядит как «кнопка не работает»."""
        with patch('netmap.services.scan', side_effect=OSError('ping не найден')):
            run = run_scan(self.subnet)

        self.assertIn('ping не найден', run.error)
        self.assertIsNotNone(run.finished_at)


class AccessTest(TestCase):
    """Карта сети — только для администраторов."""

    def setUp(self):
        self.subnet = Subnet.objects.create(cidr='10.10.0.0/24')
        self.address = NetworkAddress.objects.create(subnet=self.subnet, ip='10.10.0.1')
        self.staff = User.objects.create_user(username='netstaff', password='pass12345', is_staff=True)
        self.employee = User.objects.create_user(username='netuser', password='pass12345')

    def test_anonymous_is_redirected(self):
        response = self.client.get(reverse('netmap'))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_ordinary_employee_is_refused_everywhere(self):
        """Проверяются все три адреса: закрытая страница со списком не
        помогает, если правку или запуск обхода можно позвать напрямую."""
        self.client.force_login(self.employee)

        for response in (
            self.client.get(reverse('netmap')),
            self.client.get(reverse('netmap_address', args=[self.address.pk])),
            self.client.post(reverse('netmap_scan', args=[self.subnet.pk])),
        ):
            self.assertIn(response.status_code, (302, 403))

    def test_staff_sees_the_page(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('netmap'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '10.10.0.0/24')

    def test_menu_item_hidden_from_ordinary_employee(self):
        """Пункт меню не показывается тем, кому страница недоступна:
        контрол либо работает, либо его нет (ARCHITECTURE 12.4)."""
        self.client.force_login(self.employee)
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, 'Карта сети')

        self.client.force_login(self.staff)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Карта сети')


class ScanLaunchTest(TestCase):

    def setUp(self):
        self.subnet = Subnet.objects.create(cidr='10.10.0.0/24')
        self.staff = User.objects.create_user(username='netstaff2', password='pass12345', is_staff=True)
        self.client.force_login(self.staff)

    def test_launch_returns_task_id(self):
        with patch('netmap.views.scan_subnet.delay') as delay:
            delay.return_value.id = 'task-1'
            response = self.client.post(reverse('netmap_scan', args=[self.subnet.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task_id'], 'task-1')

    def test_disabled_subnet_is_refused_with_a_reason(self):
        """Отказ должен называть причину: молчаливый отказ — дефект, даже
        если сервер ответил правильно."""
        self.subnet.is_scan_enabled = False
        self.subnet.save()

        response = self.client.post(reverse('netmap_scan', args=[self.subnet.pk]))

        self.assertEqual(response.status_code, 400)
        self.assertIn('обход выключен', response.json()['error'])


class ScheduledScanTest(TestCase):
    """Задача по расписанию обходит только отмеченные подсети."""

    def test_disabled_subnet_is_skipped(self):
        Subnet.objects.create(cidr='10.10.1.0/30', is_scan_enabled=True)
        Subnet.objects.create(cidr='10.10.2.0/30', is_scan_enabled=False)

        with patch('netmap.services.scan', return_value=[]):
            from netmap.tasks import scan_all_subnets

            results = scan_all_subnets()

        self.assertEqual([r['subnet'] for r in results], ['10.10.1.0/30'])


class ScanRunJournalTest(TestCase):
    """Обход сети заметен извне, поэтому у него должен быть журнал."""

    def test_run_records_who_and_when(self):
        subnet = Subnet.objects.create(cidr='10.10.0.0/30')
        user = User.objects.create_user(username='netadmin2', password='pass12345', is_staff=True)

        with patch('netmap.services.scan', return_value=[]):
            run_scan(subnet, user=user)

        run = ScanRun.objects.get()
        self.assertEqual(run.started_by, user)
        self.assertIsNotNone(run.finished_at)
