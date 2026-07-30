from django.urls import path

from .views import CommentEditView, CommentDeleteView, CommentToggleLikeView, CommentLikersView

urlpatterns = [
    path('comment/<int:comment_id>/edit/', CommentEditView.as_view(), name='comment_edit'),
    path('comment/<int:comment_id>/delete/', CommentDeleteView.as_view(), name='comment_delete'),
    path('comment/<int:comment_id>/toggle-like/', CommentToggleLikeView.as_view(), name='comment_toggle_like'),
    path('comment/<int:comment_id>/likers/', CommentLikersView.as_view(), name='comment_likers'),
]