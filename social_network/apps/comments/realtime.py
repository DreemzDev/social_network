"""Живые обновления комментариев (WebSocket) и уведомление автора поста.

Отдельный модуль, а не логика внутри views, — переиспользуется и из
comments/views.py (редактирование/удаление/лайк), и из posts/views.py
(создание комментария), без циклических импортов между приложениями."""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.template.loader import render_to_string


def _group_name(post_id):
    return f'post_comments_{post_id}'


def _push(post_id, event_type, **payload):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(_group_name(post_id), {'type': event_type, **payload})


def broadcast_new_comment(comment):
    """Рендерит комментарий тем же partial-шаблоном, что и обычный список
    (templates/includes/posts/comment.html), и рассылает готовый HTML всем, кто
    сейчас смотрит страницу поста — без дублирования разметки в JS."""
    html = render_to_string('includes/posts/comment.html', {'comment': comment, 'request': None})
    _push(comment.post_id, 'comment_created', html=html)

    from posts.realtime import broadcast_post_comment_count_changed
    broadcast_post_comment_count_changed(comment.post_id, delta=1)


def broadcast_comment_updated(comment):
    _push(comment.post_id, 'comment_updated', comment_id=comment.id, comment_text=comment.comment_text, is_edited=comment.is_edited)


def broadcast_comment_deleted(comment_id, post_id):
    _push(post_id, 'comment_deleted', comment_id=comment_id)

    from posts.realtime import broadcast_post_comment_count_changed
    broadcast_post_comment_count_changed(post_id, delta=-1)


def broadcast_comment_like_toggled(comment):
    _push(comment.post_id, 'comment_like_toggled', comment_id=comment.id, likes_count=comment.likes.count())


def notify_post_author_about_comment(comment):
    from profiles.models import Notification
    from profiles.views._common import notify

    post = comment.post
    author = comment.comment_author

    if not post.author_id or post.author_id == author.id:
        return  # автор комментирует свой же пост — уведомлять не нужно

    actor_name = f'{author.first_name} {author.last_name}'.strip() or author.username
    notify(
        post.author, Notification.Kind.POST_COMMENTED,
        f'{actor_name} прокомментировал(а) ваш пост',
        actor=author, post=post,
    )
