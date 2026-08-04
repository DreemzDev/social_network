from django.urls import path

from .views import RunTrashCleanupView, StorageDashboardView, TaskStatusView, UnifiedTrashView

urlpatterns = [
    path('storage/', StorageDashboardView.as_view(), name='storage_dashboard'),
    path('storage/trash/', UnifiedTrashView.as_view(), name='storage_trash'),
    path('storage/cleanup/<str:module>/', RunTrashCleanupView.as_view(), name='storage_run_cleanup'),
    path('storage/task-status/<str:task_id>/', TaskStatusView.as_view(), name='storage_task_status'),
]
