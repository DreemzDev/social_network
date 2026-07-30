from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import ListView, FormView

from .forms import UploadImageForm
from .models import GalleryImage


class AddImages(LoginRequiredMixin, FormView, ListView):
    model = GalleryImage
    form_class = UploadImageForm
    context_object_name = 'gallery'
    template_name = 'gallery/gallery.html'
    success_url = reverse_lazy('home')

    def get_queryset(self):
        return GalleryImage.objects.order_by('-uploaded_at')

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        if not form.is_valid():
            return self.form_invalid(form)

        for image in request.FILES.getlist('images'):
            GalleryImage.objects.create(image=image)
        return HttpResponseRedirect('')

    def get_context_data(self, *, object_list=None, **kwargs):
        context = super().get_context_data(**kwargs)
        context["birthday"] = get_user_model().objects.filter(
            birthday__day=date.today().day, birthday__month=date.today().month
        )
        return context