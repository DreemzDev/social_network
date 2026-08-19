"""Опрос адресов: ping, обратный DNS, ARP и проба портов.

Только стандартная библиотека и системные утилиты — новых зависимостей не
нужно. Разбор вывода `ping` идёт по коду возврата, а не по тексту: текст
локализован, и на русской Windows он другой (проверено на машине разработки).
"""
import ipaddress
import platform
import re
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

WINDOWS = platform.system() == 'Windows'

#: TTL из ответа — бесплатный признак ОС: Windows отвечает 128, Linux 64,
#: сетевое железо обычно 255. Признак грубый (TTL настраивается и убывает на
#: каждом маршрутизаторе), поэтому он уступает пробе портов.
TTL_RE = re.compile(rb'[Tt][Tt][Ll][=\s](\d+)')

#: Порты, по которым видно и роль устройства, и ОС. Список короткий
#: намеренно: чем он длиннее, тем больше обход похож на сканирование портов.
PROBE_PORTS = (22, 445, 3389, 631, 9100, 80)

WINDOWS_PORTS = {445, 3389, 5985}
LINUX_PORTS = {22}
PRINTER_PORTS = {631, 9100}

#: MAC, которые в ARP-таблице ничего не значат: широковещательный и пустой.
MEANINGLESS_MACS = {'ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00'}

ARP_LINE_RE = re.compile(
    r'(?P<ip>\d{1,3}(?:\.\d{1,3}){3}).*?(?P<mac>(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2})'
)


@dataclass
class Probe:
    """Что удалось узнать об одном адресе за один обход."""

    ip: str
    alive: bool = False
    ttl: int | None = None
    hostname: str = ''
    mac: str = ''
    open_ports: list = field(default_factory=list)
    os_guess: str = ''
    method: str = ''


def ping(ip: str, timeout_ms: int = 1000) -> tuple[bool, int | None]:
    """Отвечает ли адрес на ICMP и с каким TTL.

    Ответ определяется кодом возврата: разбирать текст нельзя, он переведён.
    """
    if WINDOWS:
        cmd = ['ping', '-n', '1', '-w', str(timeout_ms), ip]
    else:
        cmd = ['ping', '-c', '1', '-W', str(max(1, round(timeout_ms / 1000))), ip]

    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout_ms / 1000 + 2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False, None

    if result.returncode != 0:
        return False, None
    match = TTL_RE.search(result.stdout)
    return True, int(match.group(1)) if match else None


def reverse_dns(ip: str) -> str:
    """Имя по адресу. Зовётся только для ответивших: на несуществующем адресе
    системный резолвер думает секунд десять, и обход /24 встанет намертво."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except (OSError, UnicodeError):
        return ''


def port_open(ip: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_ports(ip: str, ports=PROBE_PORTS, timeout: float = 0.5) -> list:
    return [port for port in ports if port_open(ip, port, timeout)]


def parse_arp(output: str) -> dict:
    """IP → MAC из вывода `arp -a` или `ip neigh`.

    Один разбор на обе ОС: строки различаются, но в каждой есть адрес и MAC, а
    остальное — локализованные слова. Из таблицы выкидываются multicast и
    служебные MAC, иначе в карту сети попадут 224.0.0.251 и ему подобные.
    """
    table = {}
    for line in output.splitlines():
        match = ARP_LINE_RE.search(line)
        if not match:
            continue

        ip = match.group('ip')
        mac = match.group('mac').lower().replace('-', ':')
        if mac in MEANINGLESS_MACS:
            continue
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if address.is_multicast or address.is_unspecified:
            continue
        table[ip] = mac
    return table


def arp_table() -> dict:
    """ARP-таблица сервера. Заполнена только для его собственного сегмента:
    за маршрутизатором MAC не виден в принципе, и пустой MAC у дальних
    подсетей — это норма, а не сбой опроса."""
    cmd = ['arp', '-a'] if WINDOWS else ['ip', 'neigh']
    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    return parse_arp(result.stdout.decode('utf-8', errors='replace'))


def guess_os(ttl: int | None, ports) -> str:
    """Windows, Linux или сетевое устройство.

    Порты весомее TTL: TTL настраивается и убывает на каждом маршрутизаторе,
    а открытый 3389 или 22 говорит о системе прямо.
    """
    ports = set(ports or ())
    if ports & PRINTER_PORTS:
        return 'device'
    if ports & WINDOWS_PORTS:
        return 'windows'
    if ports & LINUX_PORTS:
        return 'linux'
    if ttl is None:
        return ''
    if ttl > 128:
        return 'device'
    return 'windows' if ttl > 64 else 'linux'


def probe_address(ip: str, *, with_ports: bool = True, arp: dict | None = None) -> Probe:
    """Полный опрос одного адреса.

    Порядок важен: сначала ping, и только для ответившего — обратный DNS и
    порты. Иначе на каждый мёртвый адрес тратится время резолвера.
    """
    probe = Probe(ip=ip)
    alive, ttl = ping(ip)
    probe.ttl = ttl
    if alive:
        probe.alive = True
        probe.method = 'ping'

    if with_ports:
        probe.open_ports = probe_ports(ip)
        if probe.open_ports and not probe.alive:
            # Не ответил на ping, но порт открыт — тот самый случай, ради
            # которого проба портов и нужна: ICMP режет файрвол.
            probe.alive = True
            probe.method = 'tcp'

    if (arp or {}).get(ip):
        probe.mac = arp[ip]
        if not probe.alive:
            probe.alive = True
            probe.method = 'arp'

    if probe.alive:
        probe.hostname = reverse_dns(ip)

    probe.os_guess = guess_os(probe.ttl, probe.open_ports)
    return probe


def scan(addresses, *, with_ports: bool = True, workers: int = 50, on_progress=None) -> list:
    """Обход списка адресов. Потоки, а не последовательный проход: /24 с
    таймаутом в секунду на адрес шёл бы четыре минуты.

    on_progress(готово, всего) зовётся по мере ответов — обход /24 занимает
    десятки секунд, и без вестей о ходе фронт считает задачу зависшей.
    """
    arp = arp_table()
    addresses = list(addresses)
    if not addresses:
        return []

    total = len(addresses)
    probes = []
    with ThreadPoolExecutor(max_workers=min(workers, total)) as pool:
        futures = [
            pool.submit(probe_address, ip, with_ports=with_ports, arp=arp)
            for ip in addresses
        ]
        for future in as_completed(futures):
            probes.append(future.result())
            if on_progress:
                on_progress(len(probes), total)

    probes.sort(key=lambda probe: ipaddress.ip_address(probe.ip))
    return probes
