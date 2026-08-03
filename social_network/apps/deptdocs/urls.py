from django.urls import path

from .views import (
    DepartmentFolderListView,
    DepartmentDocumentTrashView,
    CreateDepartmentFolderView,
    UpdateFolderAccessView,
    UploadDepartmentDocumentView,
    DownloadDepartmentDocumentView,
    TrashDepartmentDocumentView,
    RestoreDepartmentDocumentView,
    PurgeDepartmentDocumentView,
)

urlpatterns = [
    path('deptdocs/', DepartmentFolderListView.as_view(), name='deptdocs_list'),
    path('deptdocs/folder/<int:folder_id>/', DepartmentFolderListView.as_view(), name='deptdocs_folder'),
    path('deptdocs/trash/', DepartmentDocumentTrashView.as_view(), name='deptdocs_trash'),
    path('deptdocs/folder/create/', CreateDepartmentFolderView.as_view(), name='deptdocs_folder_create'),
    path('deptdocs/folder/<int:folder_id>/access/', UpdateFolderAccessView.as_view(), name='deptdocs_folder_access'),
    path('deptdocs/upload/', UploadDepartmentDocumentView.as_view(), name='deptdocs_upload'),
    path('deptdocs/download/<int:doc_id>/', DownloadDepartmentDocumentView.as_view(), name='deptdocs_download'),
    path('deptdocs/trash-doc/<int:doc_id>/', TrashDepartmentDocumentView.as_view(), name='deptdocs_trash_doc'),
    path('deptdocs/restore/<int:doc_id>/', RestoreDepartmentDocumentView.as_view(), name='deptdocs_restore'),
    path('deptdocs/purge/<int:doc_id>/', PurgeDepartmentDocumentView.as_view(), name='deptdocs_purge'),
]
