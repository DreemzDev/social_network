"""Поиск по карте сети — только для администраторов.

Проверка прав повторяется здесь, а не полагается на то, что страница
закрыта: общий поиск зовёт этот модуль напрямую, минуя его вьюхи.
"""
from django.db.models import Q
from django.urls import reverse

from .models import NetworkAddress


def search(query, user, limit):
    if not user.is_staff:
        return []

    addresses = NetworkAddress.objects.filter(
        Q(ip__icontains=query) | Q(name__icontains=query)
        | Q(hostname__icontains=query) | Q(mac__icontains=query)
    ).select_related('subnet')[:limit]

    return [{
        'title': item.ip,
        'subtitle': ' · '.join(filter(None, ['Карта сети', item.name or item.hostname])),
        'url': reverse('netmap_address', args=[item.pk]),
    } for item in addresses]
