import json

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .realtime import FEED_GROUP


class FeedConsumer(AsyncJsonWebsocketConsumer):
    """WebSocket-канал для живых обновлений ленты постов: новый пост
    (кнопка "Есть новые посты"), изменение счётчика лайков/комментариев у
    карточки. Отдельный consumer со своей группой — та же причина, что и у
    PostCommentsConsumer (comments/consumers.py): не переиспользовать чужой
    WebSocket-протокол, чтобы не рисковать конфликтом имён обработчиков."""

    async def connect(self):
        await self.channel_layer.group_add(FEED_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(FEED_GROUP, self.channel_name)

    async def post_created(self, event):
        await self.send(text_data=json.dumps({'type': 'post_created', 'post_id': event['post_id']}))

    async def post_like_toggled(self, event):
        await self.send(text_data=json.dumps({
            'type': 'post_like_toggled', 'post_id': event['post_id'], 'likes_count': event['likes_count'],
        }))

    async def post_comment_count_changed(self, event):
        await self.send(text_data=json.dumps({
            'type': 'post_comment_count_changed', 'post_id': event['post_id'], 'delta': event['delta'],
        }))

    async def post_deleted(self, event):
        await self.send(text_data=json.dumps({'type': 'post_deleted', 'post_id': event['post_id']}))
