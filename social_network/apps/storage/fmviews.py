"""Базовые вьюхи файлового менеджера для модулей-потребителей.

Обменник, каталог и документы отделов делают над файлом одно и то же:
в корзину, восстановить, удалить окончательно, скачать. Различаются только
модель, имя параметра в URL, политика прав и слово в уведомлении — а логика
была написана трижды.

Права остаются у потребителя: базовый класс их не знает и не проверяет, он
лишь вызывает `check_permission()`, который по умолчанию не делает ничего
(так устроен каталог — см. ARCHITECTURE.md, «Каталог открыт на запись»).
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import View

from .services import StorageService
from .signals import attribute_deletion

TRASH_FIELDS = ['is_deleted', 'deleted_at', 'deleted_by']


class FmObjectView(LoginRequiredMixin, View):
    """Действие над одним объектом потребителя.

    Потребитель задаёт `model` и `pk_kwarg`, при необходимости
    переопределяет `check_permission()` и `notify()`.
    """

    model = None
    pk_kwarg = 'pk'
    #: каким должен быть is_deleted у объекта, над которым работаем
    expects_trashed = False
    #: чем объект называется в уведомлении — «файл» или «документ»
    noun = 'файл'

    def get_object(self, request, **kwargs):
        obj = get_object_or_404(
            self.model, pk=kwargs[self.pk_kwarg], is_deleted=self.expects_trashed,
        )
        self.check_permission(request, obj)
        return obj

    def check_permission(self, request, obj):
        """Потребитель переопределяет. По умолчанию ограничений нет."""

    def notify(self, obj, *, actor, action, text):
        """Живое обновление той папки, где объект лежит. Потребитель знает,
        как назвать это место (storage/realtime.py), storage — нет."""


class TrashObjectView(FmObjectView):
    """В корзину: пометка, а не физическое удаление (ARCHITECTURE.md, 6)."""

    expects_trashed = False

    def post(self, request, **kwargs):
        obj = self.get_object(request, **kwargs)

        obj.is_deleted = True
        obj.deleted_at = timezone.now()
        obj.deleted_by = request.user
        obj.save(update_fields=TRASH_FIELDS)

        self.notify(obj, actor=request.user, action='file_trashed',
                    text=f'удалил {self.noun}')
        return JsonResponse({'success': True})


class RestoreObjectView(FmObjectView):
    expects_trashed = True

    def post(self, request, **kwargs):
        obj = self.get_object(request, **kwargs)

        obj.is_deleted = False
        obj.deleted_at = None
        obj.deleted_by = None
        obj.save(update_fields=TRASH_FIELDS)

        self.notify(obj, actor=request.user, action='file_restored',
                    text=f'восстановил {self.noun}')
        return JsonResponse({'success': True})


class PurgeObjectView(FmObjectView):
    """Окончательное удаление — только из корзины.

    `detach()` руками не зовётся: его выполнит сигнал `post_delete`. Пометка
    нужна лишь затем, чтобы в журнале остался инициатор удаления.
    """

    expects_trashed = True

    def post(self, request, **kwargs):
        obj = self.get_object(request, **kwargs)

        attribute_deletion(obj, user=request.user, consumer=self.model._meta.label)
        obj.delete()
        return JsonResponse({'success': True})


class DownloadObjectView(FmObjectView):
    """Скачивание. `?inline=1` — открыть в браузере вместо загрузки."""

    expects_trashed = False

    def get(self, request, **kwargs):
        obj = self.get_object(request, **kwargs)
        return StorageService.get_download_response(
            obj.file_object, request, inline=request.GET.get('inline') == '1',
        )
