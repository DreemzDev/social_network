"""Карта сети — страница только для администраторов.

Инвентарь адресов с тем, кто где сидит, — служебная информация, поэтому
доступ шире `is_staff` не открывается: и сама страница, и пункт меню.
"""
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, UpdateView, View

from storage.utils import fm_task_response

from .forms import AddressForm
from .models import NetworkAddress, ScanRun, Subnet
from .tasks import scan_subnet


class StaffOnlyMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Вся карта сети — для администраторов. Обычный сотрудник её не видит
    и не должен получать 403 на пункт, которого ему не показывают."""

    def test_func(self):
        return self.request.user.is_staff


class NetmapView(StaffOnlyMixin, ListView):
    """Список адресов подсети с фильтрами и списком свободных."""

    model = NetworkAddress
    template_name = 'netmap/list.html'
    context_object_name = 'addresses'
    paginate_by = 50

    def get_subnet(self):
        subnets = Subnet.objects.all()
        cidr = self.request.GET.get('subnet')
        if cidr:
            return subnets.filter(cidr=cidr).first() or subnets.first()
        return subnets.first()

    def get_queryset(self):
        subnet = self.get_subnet()
        if subnet is None:
            return NetworkAddress.objects.none()

        qs = subnet.addresses.select_related('responsible')

        query = self.request.GET.get('q', '').strip()
        if query:
            from django.db.models import Q

            qs = qs.filter(
                Q(ip__icontains=query) | Q(name__icontains=query)
                | Q(hostname__icontains=query) | Q(mac__icontains=query)
                | Q(room__icontains=query)
            )

        os_guess = self.request.GET.get('os')
        if os_guess:
            qs = qs.filter(os_guess=os_guess)

        kind = self.request.GET.get('kind')
        if kind:
            qs = qs.filter(kind=kind)

        # «Отвечал при последнем обходе» считается сравнением двух меток, а не
        # отдельным флагом: флаг пришлось бы гасить у всех при каждом обходе.
        from django.db.models import F

        status = self.request.GET.get('status')
        if status == 'online':
            qs = qs.filter(last_seen_at__isnull=False, last_seen_at__gte=F('last_scan_at'))
        elif status == 'offline':
            qs = qs.filter(last_seen_at__isnull=True) | qs.filter(last_seen_at__lt=F('last_scan_at'))

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        subnet = self.get_subnet()
        context['subnets'] = Subnet.objects.all()
        context['subnet'] = subnet
        context['kinds'] = NetworkAddress.Kind.choices
        context['os_choices'] = [c for c in NetworkAddress.OsGuess.choices if c[0]]

        if subnet is not None:
            free = subnet.free_addresses()
            context['free_addresses'] = free[:60]
            context['free_total'] = len(free)
            context['occupied_total'] = subnet.addresses.count()
            context['last_run'] = ScanRun.objects.filter(subnet=subnet).first()
        return context


class AddressUpdateView(StaffOnlyMixin, UpdateView):
    """Правка справочной части адреса. Данные обхода не редактируются:
    их перезапишет следующий обход, и ручная правка была бы обманом."""

    model = NetworkAddress
    form_class = AddressForm
    template_name = 'netmap/address_form.html'
    context_object_name = 'address'

    def get_success_url(self):
        return f"{reverse_lazy('netmap')}?subnet={self.object.subnet.cidr}"


class ScanLaunchView(StaffOnlyMixin, View):
    """Запуск обхода. Задача уходит в Celery, страница опрашивает её статус
    тем же эндпоинтом, что и массовые операции файлового менеджера."""

    def post(self, request, subnet_id):
        subnet = get_object_or_404(Subnet, pk=subnet_id)
        if not subnet.is_scan_enabled:
            return JsonResponse(
                {'success': False, 'error': 'Для этой подсети обход выключен'}, status=400,
            )

        task = scan_subnet.delay(subnet.pk, request.user.pk)
        return fm_task_response(request, task, subnet=subnet.cidr)
