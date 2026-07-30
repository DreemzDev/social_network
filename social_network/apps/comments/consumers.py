import json

from channels.generic.websocket import AsyncJsonWebsocketConsumer


class PostCommentsConsumer(AsyncJsonWebsocketConsumer):
    """Отдельный WebSocket-канал для живых комментариев на странице поста.

    Не переиспользует ExtendedChatConsumer/группу пользователя из чата —
    у комментариев другая модель подписки (группа на пост, а не на
    получателя), и заводить это через существующий чат-consumer означало
    бы переопределять его connect()/receive(), рискуя повторить баг с
    конфликтом имени обработчика, который уже был в мессенджере."""

    async def connect(self):
        self.post_id = self.scope['url_route']['kwargs']['post_id']
        self.group_name = f'post_comments_{self.post_id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def comment_created(self, event):
        await self.send(text_data=json.dumps({'type': 'comment_created', 'html': event['html']}))

    async def comment_updated(self, event):
        await self.send(text_data=json.dumps({
            'type': 'comment_updated', 'comment_id': event['comment_id'],
            'comment_text': event['comment_text'], 'is_edited': event['is_edited'],
        }))

    async def comment_deleted(self, event):
        await self.send(text_data=json.dumps({'type': 'comment_deleted', 'comment_id': event['comment_id']}))

    async def comment_like_toggled(self, event):
        await self.send(text_data=json.dumps({
            'type': 'comment_like_toggled', 'comment_id': event['comment_id'], 'likes_count': event['likes_count'],
        }))
