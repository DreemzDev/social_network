"""Обход подсети из командной строки — чтобы посмотреть результат до того,
как вешать задачу на расписание, и чтобы было чем проверить сеть на месте."""
from django.core.management.base import BaseCommand, CommandError

from netmap.models import Subnet
from netmap.services import run_scan


class Command(BaseCommand):
    help = 'Опросить подсеть и обновить карту сети'

    def add_arguments(self, parser):
        parser.add_argument('cidr', nargs='?', help='Например, 10.10.0.0/24. Без него — все включённые')
        parser.add_argument('--no-ports', action='store_true', help='Только ping, без пробы портов')

    def handle(self, *args, **options):
        if options['cidr']:
            subnets = Subnet.objects.filter(cidr=options['cidr'])
            if not subnets:
                raise CommandError(f'Подсеть {options["cidr"]} не заведена — добавьте её в админке')
        else:
            subnets = Subnet.objects.filter(is_scan_enabled=True)
            if not subnets:
                raise CommandError('Нет подсетей, отмеченных «обходить»')

        for subnet in subnets:
            if options['no_ports']:
                subnet.probe_ports = False
            self.stdout.write(f'Обход {subnet.cidr} ({len(subnet.hosts())} адресов)...')

            run = run_scan(subnet)
            if run.error:
                self.stdout.write(self.style.ERROR(f'  ошибка: {run.error}'))
                continue

            seconds = (run.finished_at - run.started_at).total_seconds()
            self.stdout.write(self.style.SUCCESS(
                f'  ответили {run.responded} из {run.scanned}, '
                f'новых записей {run.created}, за {seconds:.0f} с'
            ))
            free = len(subnet.free_addresses())
            self.stdout.write(f'  свободных адресов: {free}')
