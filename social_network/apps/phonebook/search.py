"""Поиск по справочникам организации."""
from django.urls import reverse

from .models import Phonebook


def search(query, user, limit):
    books = Phonebook.objects.filter(is_deleted=False, title__icontains=query)[:limit]

    return [{
        'title': item.title,
        'subtitle': 'Справочник',
        'url': reverse('phonebook', args=[item.pk]),
    } for item in books]
