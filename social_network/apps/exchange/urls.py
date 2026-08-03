from django.urls import path

from .views import (
    ExchangeFolderListView,
    ExchangeFolderView,
    ExchangeTrashView,
    UploadExchangeFileView,
    DownloadExchangeFileView,
    TrashExchangeFileView,
    RestoreExchangeFileView,
    PurgeExchangeFileView,
)

urlpatterns = [
    path('exchange/', ExchangeFolderListView.as_view(), name='exchange_inbox'),
    path('exchange/folder/<int:user_id>/', ExchangeFolderView.as_view(), name='exchange_folder'),
    path('exchange/trash/', ExchangeTrashView.as_view(), name='exchange_trash'),
    path('exchange/upload/<int:user_id>/', UploadExchangeFileView.as_view(), name='exchange_upload'),
    path('exchange/download/<int:file_id>/', DownloadExchangeFileView.as_view(), name='exchange_download'),
    path('exchange/trash-file/<int:file_id>/', TrashExchangeFileView.as_view(), name='exchange_trash_file'),
    path('exchange/restore/<int:file_id>/', RestoreExchangeFileView.as_view(), name='exchange_restore'),
    path('exchange/purge/<int:file_id>/', PurgeExchangeFileView.as_view(), name='exchange_purge'),
]
