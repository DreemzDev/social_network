from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import ListView, DetailView, TemplateView, FormView, UpdateView, DeleteView
from django.views.generic.edit import FormMixin

from comments.forms import CommentForm
from comments.models import Comment
from comments.realtime import broadcast_new_comment, notify_post_author_about_comment
from profiles.forms import TaskForm, NoteForm, EventForm
from profiles.models import Category, Task, Note

from storage.models import FileObject
from storage.services import StorageService
from storage.signals import attribute_deletion

from .forms import AddPostForm
from .models import Post, PostImage, PostFile
from .realtime import broadcast_post_created, broadcast_post_like_toggled, broadcast_post_deleted


class PortalHome(LoginRequiredMixin, ListView):
    paginate_by = 10
    model = Post
    template_name = 'index.html'
    context_object_name = 'posts'
    login_url = 'login'
    

    def get_queryset(self):
        posts = Post.objects.select_related('author').annotate(
            num_comments=Count('post_comments')
        ).prefetch_related('images', 'files', 'likes', 'viewers')

        # cat_id приходит с маршрута /category/<id>/ — это та же лента,
        # суженная до одного подразделения. Раньше на него отвечала отдельная
        # вьюха, собиравшая посты без select_related и prefetch_related.
        cat_id = self.kwargs.get('cat_id')
        if cat_id:
            posts = posts.filter(author__cat_id=cat_id)

        return posts.order_by('-time_create')

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        context['current_cat_id'] = int(self.kwargs.get('cat_id', 0))
        return context


class NewPostsFeedView(LoginRequiredMixin, View):
    """Отдаёт HTML новых постов (created позже last_post_id) для кнопки
    «Есть новые посты» — рендерит тем же partial-шаблоном, что и основная
    лента (includes/posts/post.html), чтобы разметка карточки не расходилась
    между обычной загрузкой страницы и live-довставкой."""

    def get(self, request, last_post_id):
        posts = Post.objects.select_related('author').annotate(
            num_comments=Count('post_comments')
        ).prefetch_related('images', 'files', 'likes', 'viewers').filter(
            pk__gt=last_post_id
        ).order_by('-time_create')

        html = render_to_string('includes/posts/list_fragment.html', {'posts': posts, 'request': request}, request=request)
        return JsonResponse({'html': html, 'count': posts.count()})



class ShowPost(LoginRequiredMixin, FormMixin, DetailView):

    model = Post
    template_name = 'post/single.html'
    context_object_name = 'post'
    pk_url_kwarg = 'post_id'
    form_class = CommentForm

    def get_queryset(self):
        return Post.objects.prefetch_related('images', 'files')

    def get_success_url(self, **kwargs):
        return reverse_lazy('post', kwargs={'post_id': self.object.id})

    @method_decorator(login_required)
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form):
        comment = form.save(commit=False)
        comment.post = self.object
        comment.comment_author = self.request.user
        comment.save()

        broadcast_new_comment(comment)
        notify_post_author_about_comment(comment)

        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True})
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)
        return super().form_invalid(form)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.user.is_authenticated:
            self.object.viewers.add(request.user)
        return super().get(request, *args, **kwargs)

@login_required
@require_POST
def toggle_like(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    if post.likes.filter(pk=request.user.pk).exists():
        post.likes.remove(request.user)
        liked = False
    else:
        post.likes.add(request.user)
        liked = True

    broadcast_post_like_toggled(post)
    return JsonResponse({'liked': liked, 'likes_count': post.likes.count()})


@login_required
def post_likers(request, post_id):
    """JSON-список лайкнувших пост — для модального окна вместо тултипа
    (тултип не помещал весь список при большом числе лайков)."""
    post = get_object_or_404(Post, id=post_id)
    return JsonResponse({'users': serialize_likers(post.likes.all())})


def serialize_likers(users):
    return [
        {
            'name': f'{u.first_name} {u.last_name}'.strip() or u.username,
            'avatar': u.avatar.url if u.avatar else static('img/avatar7.png'),
            'profile_url': reverse('addpost', kwargs={'username': u.username}),
        }
        for u in users
    ]


class AddPost(LoginRequiredMixin, FormView, TemplateView):

    form_class = AddPostForm
    template_name = 'profiles/profiles.html'
    success_url = reverse_lazy('home')
    
    def form_valid(self, form):
        post = form.save(commit=False)
        post.author = self.request.user
        post.save()

        for order, image_file in enumerate(self.request.FILES.getlist('images')):
            PostImage.objects.create(post=post, image=image_file, order=order)

        for uploaded_file in self.request.FILES.getlist('attachments'):
            file_object = StorageService.upload(
                uploaded_file, user=self.request.user, category=FileObject.Category.DOCUMENT,
            )
            PostFile.objects.create(post=post, file_object=file_object)

        broadcast_post_created(post)
        return super().form_valid(form)

    def get_online_users(self):
        """Возвращает пользователей, которые были активны в последние 5 минут"""
        User = get_user_model()
        time_threshold = timezone.now() - timedelta(minutes=5)
        return User.objects.filter(last_activity__gte=time_threshold)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        User = get_user_model()
        
        user = get_object_or_404(User, username=self.kwargs.get('username'))
        user_list = User.objects.all().order_by('-last_activity')
        
        # Считаем онлайн пользователей
        online_count = sum(1 for u in user_list if u.is_online)

        context['user_list'] = user_list  # Используем уже созданный user_list
        context["sh_online"] = self.get_online_users()
        context['posts'] = Post.objects.select_related('author').annotate(
            num_comments=Count('post_comments')
        ).prefetch_related('images', 'files', 'likes', 'viewers').filter(author=user).order_by('-time_create')
        context['user'] = user
        context['profile_user'] = user
        context['online_count'] = online_count  # Добавляем в контекст!

        user_posts = Post.objects.filter(author=user)
        context['stats'] = {
            'posts_count': user_posts.count(),
            'comments_count': Comment.objects.filter(comment_author=user).count(),
            'likes_count': user_posts.aggregate(total=Count('likes'))['total'] or 0,
            'views_count': user_posts.aggregate(total=Count('viewers'))['total'] or 0,
        }
        context['is_owner'] = self.request.user == user

        if context['is_owner']:
            own_tasks = Task.objects.filter(user=user).filter(Q(assigned_by__isnull=True) | Q(assigned_by=user))
            context['pending_tasks'] = own_tasks.filter(is_completed=False)
            context['completed_tasks'] = own_tasks.filter(is_completed=True)
            context['task_form'] = TaskForm()

            context['notes'] = Note.objects.filter(user=user)
            context['note_form'] = NoteForm()

            context['event_form'] = EventForm()

        return context
  
class PostDeleteView(LoginRequiredMixin, DeleteView):
    model = Post
    success_url = reverse_lazy('home')

    def get_queryset(self):
        # Удалить пост может только его автор.
        return Post.objects.filter(author=self.request.user)

    def form_valid(self, form):
        # Реальный путь удаления для этого view: Django (начиная с 4.0)
        # обрабатывает POST через post() -> form_valid() -> object.delete(),
        # а не через delete() (тот вызывается только на настоящий HTTP
        # DELETE-запрос, которым фронтенд проекта не пользуется — здесь
        # везде fetch(..., {method: 'POST'})). Переопределение delete() было
        # мёртвым кодом: PostFile каскадно удалялись вместе с постом, но
        # StorageService.detach() ни разу не вызывался, и FileObject
        # оставался ACTIVE навсегда.
        post_id = self.object.id

        # Вложения удаляются явно ДО поста, хотя каскад снёс бы их сам:
        # сигнал storage отработал бы и на каскаде, но без атрибуции —
        # collector создаёт собственные экземпляры PostFile и пометок,
        # проставленных здесь, на них уже не будет. Явное удаление
        # сохраняет в журнале, кто именно удалил файл.
        for post_file in self.object.files.all():
            attribute_deletion(post_file, user=self.request.user, consumer='posts.PostFile')
            post_file.delete()

        response = super().form_valid(form)

        broadcast_post_deleted(post_id)
        if self.request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return HttpResponse(status=204)
        messages.success(self.request, 'Пост удалён')
        return response

    

class HelpView(LoginRequiredMixin, TemplateView):
    template_name = 'post/help.html'


class PostFileDownloadView(LoginRequiredMixin, View):
    """Скачивание вложения поста. Права тривиальны: лента постов видна всем
    аутентифицированным (как и сам пост), поэтому LoginRequiredMixin
    достаточно — дополнительная проверка владения постом не нужна
    (ARCHITECTURE.md, раздел 8)."""

    def get(self, request, file_id):
        post_file = get_object_or_404(PostFile, pk=file_id)
        return StorageService.get_download_response(post_file.file_object, request)


class SettingPost(LoginRequiredMixin, UpdateView):
    form_class = AddPostForm
    template_name = 'post/setting_post.html'
    pk_url_kwarg = 'post_id'
    success_url = reverse_lazy('home')
    context_object_name = 'post'

    def get_queryset(self):
        # Редактировать пост может только его автор.
        return Post.objects.filter(author=self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        post = self.object

        remove_image_ids = self.request.POST.getlist('remove_image_ids')
        if remove_image_ids:
            PostImage.objects.filter(post=post, id__in=remove_image_ids).delete()

        remove_file_ids = self.request.POST.getlist('remove_file_ids')
        if remove_file_ids:
            # Удаляем по одной записи, а не queryset.delete(): пометка
            # атрибуции ставится на конкретный экземпляр, и её подхватывает
            # сигнал storage, переводящий blob в ORPHAN.
            for post_file in PostFile.objects.filter(post=post, id__in=remove_file_ids):
                attribute_deletion(post_file, user=self.request.user, consumer='posts.PostFile')
                post_file.delete()

        existing_count = post.images.count()
        for order, image_file in enumerate(self.request.FILES.getlist('images')):
            PostImage.objects.create(post=post, image=image_file, order=existing_count + order)

        for uploaded_file in self.request.FILES.getlist('attachments'):
            file_object = StorageService.upload(
                uploaded_file, user=self.request.user, category=FileObject.Category.DOCUMENT,
            )
            PostFile.objects.create(post=post, file_object=file_object)

        return response