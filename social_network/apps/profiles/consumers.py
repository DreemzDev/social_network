import json

from django_private_chat2.consumers.chat_consumer import ChatConsumer


class ExtendedChatConsumer(ChatConsumer):
    """
    Расширяет стандартный consumer django_private_chat2 обработчиками
    для собственных типов событий (редактирование/удаление сообщений),
    которых нет в самой библиотеке.
    """

    async def message_edited(self, event: dict):
        await self.send(text_data=json.dumps({
            'msg_type': 100,
            'message_id': event['message_id'],
            'text': event['text'],
            'sender': event['sender'],
            'receiver': event['receiver'],
        }))

    async def message_deleted(self, event: dict):
        await self.send(text_data=json.dumps({
            'msg_type': 101,
            'message_id': event['message_id'],
            'sender': event['sender'],
            'receiver': event['receiver'],
        }))

    async def message_reaction(self, event: dict):
        await self.send(text_data=json.dumps({
            'msg_type': 102,
            'message_id': event['message_id'],
            'reactions': event['reactions'],
            'sender': event['sender'],
            'receiver': event['receiver'],
        }))

    async def notification(self, event: dict):
        await self.send(text_data=json.dumps({
            'msg_type': 103,
            'notification': event['notification'],
            'unread_count': event['unread_count'],
        }))

    async def new_text_message(self, event: dict):
        """Сообщение, отправленное через SendMessageWithReplyView (HTTP, не
        через стандартный WebSocket-путь библиотеки) — используется, когда
        нужно приложить reply_to, которого нативный протокол не знает."""
        await self.send(text_data=json.dumps({
            'msg_type': 104,
            'id': event['id'],
            'text': event['text'],
            'sender': event['sender'],
            'receiver': event['receiver'],
            'created': event['created'],
            'reply_to': event['reply_to'],
        }))
