from datetime import timedelta

from django.urls import reverse_lazy
from django.db.models import Q
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.shortcuts import redirect
from django.views.generic import DetailView, ListView, UpdateView, FormView, View
from django.contrib.auth.mixins import LoginRequiredMixin

from category.models import Category
from phonebook.models import Phonebook
from phonebook.forms import UpdateBookForm
from profiles.forms import AddProfileForm, SettingProfileForm, ChangePasswordForm, SecurityAnswerForm


class AddProfile(LoginRequiredMixin, UpdateView):
    """Заполнение анкеты после регистрации — всегда редактирует профиль
    текущего пользователя, user_id в URL не используется как источник
    объекта (иначе любой залогиненный мог бы редактировать чужой профиль)."""
    model = get_user_model()
    form_class = AddProfileForm
    template_name = 'profiles/addprofile.html'
    success_url = reverse_lazy('home')

    def get_object(self, queryset=None):
        return self.request.user


class SettingProfile(LoginRequiredMixin, UpdateView, DetailView):
    model = get_user_model()
    form_class = SettingProfileForm
    template_name = 'profiles/settingprofiles.html'

    def get_object(self, queryset=None):
        return self.request.user

    def get_success_url(self):
        return reverse_lazy('addpost', kwargs={'username': self.object.username})

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('password_form', ChangePasswordForm(self.request.user))
        context.setdefault('security_form', SecurityAnswerForm(instance=self.request.user))
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()

        if 'change_password' in request.POST:
            password_form = ChangePasswordForm(request.user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, request.user)
                return redirect(self.get_success_url())
            context = self.get_context_data(form=self.get_form_class()(instance=self.object))
            context['password_form'] = password_form
            return self.render_to_response(context)

        if 'change_security_answer' in request.POST:
            security_form = SecurityAnswerForm(request.POST, instance=self.object)
            if security_form.is_valid():
                security_form.save()
                return redirect(self.get_success_url())
            context = self.get_context_data(form=self.get_form_class()(instance=self.object))
            context['security_form'] = security_form
            return self.render_to_response(context)

        return super().post(request, *args, **kwargs)


class ShowUsers(ListView):
    model = get_user_model()
    template_name = 'profiles/all_users.html'
    paginate_by = 20

    def get_queryset(self):
        query = self.request.GET.get('q', '')
        dept = self.request.GET.get('dept', '')
        status = self.request.GET.get('status', '')

        qs = get_user_model().objects.filter(
            Q(first_name__icontains=query) | Q(last_name__icontains=query)
        ).select_related('cat').order_by('first_name', 'last_name')

        if dept:
            qs = qs.filter(cat__name=dept)

        if status == 'online':
            online_since = timezone.now() - timedelta(minutes=5)
            qs = qs.filter(last_activity__gte=online_since)
        elif status == 'offline':
            online_since = timezone.now() - timedelta(minutes=5)
            qs = qs.filter(Q(last_activity__lt=online_since) | Q(last_activity__isnull=True))

        return qs

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cats"] = Category.objects.all()

        def qs_with(**overrides):
            params = self.request.GET.copy()
            params.pop('page', None)
            for key, value in overrides.items():
                if value:
                    params[key] = value
                else:
                    params.pop(key, None)
            return params.urlencode()

        context["dept_links"] = [
            {'name': None, 'label': 'Все отделы', 'qs': qs_with(dept=None), 'active': not self.request.GET.get('dept')},
            *[
                {
                    'name': cat.name,
                    'label': cat.name,
                    'qs': qs_with(dept=cat.name),
                    'active': self.request.GET.get('dept') == cat.name,
                }
                for cat in context['cats']
            ],
        ]
        context["status_links"] = [
            {'value': None, 'label': 'Все статусы', 'qs': qs_with(status=None), 'active': not self.request.GET.get('status')},
            {'value': 'online', 'label': 'Онлайн', 'qs': qs_with(status='online'), 'active': self.request.GET.get('status') == 'online'},
            {'value': 'offline', 'label': 'Офлайн', 'qs': qs_with(status='offline'), 'active': self.request.GET.get('status') == 'offline'},
        ]
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            html = render_to_string(
                'includes/colleague_list_fragment.html', context, request=self.request
            )
            return JsonResponse({
                'html': html,
                'has_next': context['page_obj'].has_next() if context['page_obj'] else False,
            })
        return super().render_to_response(context, **response_kwargs)


class ShowPhones(ListView, FormView):
    model = get_user_model()
    template_name = 'profiles/phones.html'
    form_class = UpdateBookForm
    success_url = reverse_lazy('show_phones')

    def form_valid(self, form):
        form.save()
        return super().form_valid(form)

    def get_queryset(self):
        query = self.request.GET.get('q', '')
        return get_user_model().objects.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query))

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["cats"] = Category.objects.all()
        context["books"] = Phonebook.objects.all()
        return context


class EmployeeStatusUpdateView(LoginRequiredMixin, View):
    def post(self, request):
        User = get_user_model()
        status = request.POST.get('status')
        if status not in dict(User.EmployeeStatus.choices):
            return JsonResponse({'success': False, 'error': 'Некорректный статус.'}, status=400)

        request.user.employee_status = status
        request.user.save(update_fields=['employee_status'])
        return JsonResponse({'success': True, 'status': status, 'label': request.user.get_employee_status_display()})
