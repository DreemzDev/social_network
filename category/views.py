from django.views.generic import ListView
from django.shortcuts import render

from category.models import Category
from post.models import Post

# Create your views here.
class PostCategory(ListView):
    paginate_by = 3
    model = Post
    template_name = 'post/index.html'
    context_object_name = 'posts'
    def get_queryset(self):
        return Post.objects.filter(cat__id=self.kwargs['cat_id'])# выбираем только те, где внешний cat_id совпадет с внутренним
    
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context['cats'] = Category.objects.all()
        
        return context