"""WebSocket-канал файлового менеджера.

Один consumer на все три модуля: протокол одинаковый («содержимое этой
папки изменилось»), различается только область (scope) и идентификатор
места. Отдельные consumer'ы на модуль отличались бы только строкой
проверки прав.

Проверка доступа делается ЗДЕСЬ, при подключении, а не при рассылке.
Причина — та же, что и в ARCHITECTURE.md разделе 8: права знает
модуль-потребитель. Обменник и каталог открыты всем аутентифицированным,
у «Приватного доступа» доступ к папке определяется allowed_users, и если
его не проверить на connect, любой сотрудник мог бы подписаться на чужую
закрытую папку и узнавать о движении файлов в ней.
"""
import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .realtime import SCOPE_DEPTDOCS, VALID_SCOPES, group_name


class FileManagerConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.scope_name = self.scope['url_route']['kwargs']['fm_scope']
        self.location = self.scope['url_route']['kwargs']['location']
        user = self.scope.get('user')

        if user is None or not user.is_authenticated or self.scope_name not in VALID_SCOPES:
            await self.close()
            return

        if not await self._has_access(user):
            await self.close()
            return

        self.group = group_name(self.scope_name, self.location)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        group = getattr(self, 'group', None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def fm_event(self, event):
        await self.send(text_data=json.dumps({
            'action': event.get('action', ''),
            'actor_id': event.get('actor_id'),
            'actor_name': event.get('actor_name', ''),
            'text': event.get('text', ''),
        }))

    @database_sync_to_async
    def _has_access(self, user) -> bool:
        """Обменник и каталог видны всем аутентифицированным — там достаточно
        самого факта авторизации. У deptdocs подписка разрешена только на
        папку, где пользователь есть в allowed_users; location='0' —
        псевдоместо «список доступных папок верхнего уровня», оно у каждого
        своё и ничего чужого не раскрывает."""
        if self.scope_name != SCOPE_DEPTDOCS:
            return True

        if self.location in ('0', ''):
            return True

        from deptdocs.models import DepartmentFolder

        folder = DepartmentFolder.objects.filter(pk=self.location).first()
        return folder is not None and folder.is_accessible_by(user)
