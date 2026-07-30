from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.generic import View
from django.contrib.auth.mixins import LoginRequiredMixin

from ..models import Note
from ..forms import NoteForm


def _serialize_note(note):
    return {'id': note.id, 'content': note.content, 'color': note.color}


class NoteCreateView(LoginRequiredMixin, View):
    def post(self, request):
        form = NoteForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        note = form.save(commit=False)
        note.user = request.user

        # select_for_update держит блокировку на существующих заметках
        # пользователя до конца транзакции — без неё два параллельных
        # запроса на создание могут прочитать один и тот же max_position
        # и получить одинаковую позицию.
        with transaction.atomic():
            max_position = Note.objects.select_for_update().filter(
                user=request.user
            ).aggregate(m=Max('position'))['m']
            note.position = (max_position or 0) + 1
            note.save()

        return JsonResponse({'success': True, 'note': _serialize_note(note)})


class NoteEditView(LoginRequiredMixin, View):
    def post(self, request, note_id):
        note = get_object_or_404(Note, pk=note_id, user=request.user)
        form = NoteForm(request.POST, instance=note)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        note = form.save()
        return JsonResponse({'success': True, 'note': _serialize_note(note)})


class NoteDeleteView(LoginRequiredMixin, View):
    def post(self, request, note_id):
        get_object_or_404(Note, pk=note_id, user=request.user).delete()
        return JsonResponse({'success': True})
