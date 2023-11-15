from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.urls import reverse_lazy

from post.models import Post
from .models import Comment
from .forms import CommentForm
from django.views.generic import ListView, DetailView, CreateView, TemplateView, FormView, UpdateView
 
