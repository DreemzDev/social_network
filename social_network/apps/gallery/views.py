from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect
from .models import GalleryImage
from .forms import UploadImageForm
from django.views.generic import ListView, DetailView, CreateView, TemplateView, FormView, UpdateView
from django.urls import reverse_lazy
from datetime import date
from django.contrib.auth import get_user_model


class AddImages(FormView, ListView):
    model = GalleryImage
    form_class = UploadImageForm
    context_object_name = 'gallery'
    template_name = 'gallery/gallery.html'  # Replace with your template.
    success_url = reverse_lazy('home')
    
    def post(self, request, *args, **kwargs):
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        files = request.FILES.getlist('images')
        if form.is_valid():
            for image in files:
                    GalleryImage.objects.create(image=image)
                    
            return HttpResponseRedirect('')

        else:
            form = UploadImageForm()
        
        return self.form_invalid(form)
        
    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["birthday"] = get_user_model().objects.filter(birthday__day=date.today().day, birthday__month=date.today().month)
        dt =date.today().day+1
        context["delta_birthday"] = get_user_model().objects.filter(birthday__day=dt, birthday__month=date.today().month)
        return context