from django.urls import path

from . import views

urlpatterns = [
    path('netmap/', views.NetmapView.as_view(), name='netmap'),
    path('netmap/address/<int:pk>/', views.AddressUpdateView.as_view(), name='netmap_address'),
    path('netmap/scan/<int:subnet_id>/', views.ScanLaunchView.as_view(), name='netmap_scan'),
]
