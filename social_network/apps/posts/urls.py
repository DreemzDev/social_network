from django.urls import path

from .views import *

urlpatterns = [
    path('', PortalHome.as_view(), name='home'),
    # Та же лента, суженная до подразделения. Раньше на этот адрес отвечала
    # отдельная вьюха приложения category — копия PortalHome без
    # select_related, prefetch_related и annotate.
    path('category/<int:cat_id>/', PortalHome.as_view(), name='category'),

    path('post/<int:post_id>/', ShowPost.as_view(), name='post'),
    path('post/<int:post_id>/likers/', post_likers, name='post_likers'),
    path('posts/new-since/<int:last_post_id>/', NewPostsFeedView.as_view(), name='new_posts_feed'),

    path('profile/<str:username>/', AddPost.as_view(), name='addpost'),

    path('settingpost/<int:post_id>/', SettingPost.as_view(), name='settingpost'),

    path('toggle_like/<int:post_id>/', toggle_like, name='toggle_like'),

    path('post/<int:post_id>/poll/vote/', PollVoteView.as_view(), name='poll_vote'),

    path('deletepost/<int:pk>/', PostDeleteView.as_view(), name='delete-post'),

    path('help/', HelpView.as_view(), name='help'),

    path('post-file/<int:file_id>/download/', PostFileDownloadView.as_view(), name='post_file_download'),
]