from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.templatetags.static import static
from django.urls import reverse
from django.views import View
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST

from .forms import CommentForm
from .models import Comment
from .realtime import broadcast_comment_updated, broadcast_comment_deleted, broadcast_comment_like_toggled


def _serialize_likers(users):
    return [
        {
            'name': f'{u.first_name} {u.last_name}'.strip() or u.username,
            'avatar': u.avatar.url if u.avatar else static('img/avatar7.png'),
            'profile_url': reverse('addpost', kwargs={'username': u.username}),
        }
        for u in users
    ]


class CommentLikersView(LoginRequiredMixin, View):
    """JSON-список лайкнувших комментарий — для модального окна вместо тултипа."""

    def get(self, request, comment_id):
        comment = get_object_or_404(Comment, pk=comment_id)
        return JsonResponse({'users': _serialize_likers(comment.likes.all())})


class CommentEditView(LoginRequiredMixin, View):
    def post(self, request, comment_id):
        comment = get_object_or_404(Comment, pk=comment_id, comment_author=request.user)
        form = CommentForm(request.POST, instance=comment)
        if not form.is_valid():
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)

        form.save()
        broadcast_comment_updated(comment)
        return JsonResponse({'success': True, 'comment_text': comment.comment_text, 'is_edited': comment.is_edited})


class CommentDeleteView(LoginRequiredMixin, View):
    def post(self, request, comment_id):
        comment = get_object_or_404(Comment, pk=comment_id, comment_author=request.user)
        post_id = comment.post_id
        comment.delete()
        broadcast_comment_deleted(comment_id, post_id)
        return JsonResponse({'success': True})


@method_decorator(login_required, name='dispatch')
@method_decorator(require_POST, name='dispatch')
class CommentToggleLikeView(View):
    def post(self, request, comment_id):
        comment = get_object_or_404(Comment, pk=comment_id)
        if comment.likes.filter(pk=request.user.pk).exists():
            comment.likes.remove(request.user)
            liked = False
        else:
            comment.likes.add(request.user)
            liked = True

        broadcast_comment_like_toggled(comment)
        return JsonResponse({'liked': liked, 'likes_count': comment.likes.count()})
