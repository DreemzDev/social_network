"""Обход подсети и перенос результата в записи адресов.

Одно место на все входы: кнопка на странице, задача Celery по расписанию и
management-команда зовут отсюда, чтобы правила обновления записей не
разъехались между тремя копиями.
"""
from django.db import transaction
from django.utils import timezone

from .models import NetworkAddress, ScanRun
from .scanner import scan


def run_scan(subnet, *, user=None, on_progress=None) -> ScanRun:
    """Обходит подсеть и сохраняет результат. Возвращает запись журнала."""
    run = ScanRun.objects.create(subnet=subnet, started_by=user)

    try:
        # Адреса с пометкой «не опрашивать» не трогаем: часть оборудования
        # плохо переносит даже пробу портов.
        excluded = set(subnet.addresses.filter(is_excluded=True).values_list('ip', flat=True))
        targets = [ip for ip in subnet.hosts() if ip not in excluded]

        probes = scan(targets, with_ports=subnet.probe_ports, on_progress=on_progress)
        run.scanned = len(probes)
        run.responded = sum(1 for probe in probes if probe.alive)
        run.created = _save_probes(subnet, probes, user=user)
    except Exception as error:                       # noqa: BLE001 — в журнал, не в лог
        run.error = str(error)

    run.finished_at = timezone.now()
    run.save()
    return run


@transaction.atomic
def _save_probes(subnet, probes, *, user=None) -> int:
    """Заводит записи для ответивших адресов и обновляет уже известные.

    Запись создаётся только для ответившего: держать в БД 254 пустые строки на
    каждую /24 незачем, свободные адреса считаются как разница с диапазоном.
    Молчащим, но уже известным адресам обновляется только время опроса — так
    видно разницу между «ответил» и «его сегодня опрашивали».
    """
    known = {a.ip: a for a in subnet.addresses.all()}
    created = 0
    to_update = []

    for probe in probes:
        address = known.get(probe.ip)
        if address is None:
            if not probe.alive:
                continue
            address = NetworkAddress(subnet=subnet, ip=probe.ip, created_by=user)
            address.apply_probe(probe)
            address.save()
            created += 1
            continue

        address.apply_probe(probe)
        to_update.append(address)

    if to_update:
        NetworkAddress.objects.bulk_update(to_update, NetworkAddress.SCAN_FIELDS)
    return created
