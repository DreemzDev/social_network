"""Живые обновления ленты постов (WebSocket).

Одна общая группа 'feed' — все, кто сейчас открыл страницу ленты (или "Моя
страница", там тот же список постов), а не группа на конкретный пост, как у
комментариев (comments/realtime.py): в ленте одновременно видно много карточек,
и события (лайк, новый комментарий, новый пост) должны доходить до всех, кто
её сейчас смотрит, независимо от того, на какой пост они смотрят."""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

FEED_GROUP = 'feed'


def _push(event_type, **payload):
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(FEED_GROUP, {'type': event_type, **payload})


def broadcast_post_created(post):
    _push('post_created', post_id=post.id)


def broadcast_post_like_toggled(post):
    _push('post_like_toggled', post_id=post.id, likes_count=post.likes.count())


def broadcast_post_comment_count_changed(post_id, delta):
    _push('post_comment_count_changed', post_id=post_id, delta=delta)


def broadcast_post_deleted(post_id):
    _push('post_deleted', post_id=post_id)
