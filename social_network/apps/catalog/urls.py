from django.urls import path

from .views import (
    CatalogFolderView,
    CatalogTrashView,
    CreateFolderView,
    UploadCatalogDocumentView,
    DownloadCatalogDocumentView,
    TrashCatalogDocumentView,
    RestoreCatalogDocumentView,
    PurgeCatalogDocumentView,
)

urlpatterns = [
    path('catalog/', CatalogFolderView.as_view(), name='catalog_root'),
    path('catalog/folder/<int:folder_id>/', CatalogFolderView.as_view(), name='catalog_folder'),
    path('catalog/trash/', CatalogTrashView.as_view(), name='catalog_trash'),
    path('catalog/folder/create/', CreateFolderView.as_view(), name='catalog_folder_create'),
    path('catalog/upload/', UploadCatalogDocumentView.as_view(), name='catalog_upload'),
    path('catalog/download/<int:doc_id>/', DownloadCatalogDocumentView.as_view(), name='catalog_download'),
    path('catalog/trash-doc/<int:doc_id>/', TrashCatalogDocumentView.as_view(), name='catalog_trash_doc'),
    path('catalog/restore/<int:doc_id>/', RestoreCatalogDocumentView.as_view(), name='catalog_restore'),
    path('catalog/purge/<int:doc_id>/', PurgeCatalogDocumentView.as_view(), name='catalog_purge'),
]
