"""Поиск по новостям портала."""
from django.urls import reverse
from django.utils.text import Truncator

from .models import Post


def search(query, user, limit):
    posts = Post.objects.filter(content__icontains=query).select_related('author')[:limit]

    return [{
        'title': Truncator(item.content).chars(70),
        'subtitle': f'Новость · {item.author.last_name} {item.author.first_name}'.strip(' ·') if item.author else 'Новость',
        'url': reverse('post', args=[item.pk]),
    } for item in posts]
