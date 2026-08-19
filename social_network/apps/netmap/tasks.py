"""Обход сети фоновой задачей.

Отдельная задача, а не вызов из вьюхи: /24 с таймаутом в секунду на адрес
занимает десятки секунд, и держать на этом HTTP-запрос нельзя. Расписание
задаётся через django_celery_beat в админке, как и остальные периодические
задачи проекта.
"""
from celery import shared_task

from .models import Subnet
from .services import run_scan


@shared_task(bind=True)
def scan_subnet(self, subnet_id, user_id=None):
    subnet = Subnet.objects.filter(pk=subnet_id).first()
    if subnet is None:
        return {'error': 'Подсеть удалена'}

    user = None
    if user_id:
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.filter(pk=user_id).first()

    # Без вестей о ходе фронт через 20 секунд решит, что воркер не забрал
    # задачу, и покажет ложную ошибку: обход /24 столько и идёт.
    def report(done, total):
        self.update_state(state='PROGRESS', meta={'done': done, 'total': total})

    run = run_scan(subnet, user=user, on_progress=report)
    return {
        'run_id': run.pk,
        'scanned': run.scanned,
        'responded': run.responded,
        'created': run.created,
        'error': run.error,
    }


@shared_task
def scan_all_subnets():
    """Для расписания: обходит все подсети, отмеченные «обходить».

    Зовёт run_scan напрямую, а не задачу внутри задачи: подсети обходятся
    последовательно, и лишний уровень Celery ничего не даёт.
    """
    results = []
    for subnet in Subnet.objects.filter(is_scan_enabled=True):
        run = run_scan(subnet)
        results.append({
            'subnet': subnet.cidr, 'scanned': run.scanned,
            'responded': run.responded, 'error': run.error,
        })
    return results
