"""Общий поиск: выпадающий список в шапке и страница со всеми результатами.

Разметку результатов рисует сервер и на выпадающий список, и на страницу —
собирать её в JS означало бы вторую реализацию тех же правил показа
(тот же довод, что у PartialGridMixin в storage).
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from profiles.search import GROUP_LIMIT, MIN_QUERY_LENGTH, search_everything

#: На странице результатов группы длиннее, чем в выпадающем списке.
PAGE_LIMIT = 20


class GlobalSearchView(LoginRequiredMixin, TemplateView):
    """XHR отдаёт кусок разметки для выпадающего списка, обычный GET —
    страницу со всеми результатами."""

    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['includes/layout/search_results.html']
        return ['profiles/search.html']

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        is_xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        context['query'] = query
        context['too_short'] = 0 < len(query) < MIN_QUERY_LENGTH
        context['groups'] = search_everything(
            query, self.request.user, limit=GROUP_LIMIT if is_xhr else PAGE_LIMIT,
        )
        return context
