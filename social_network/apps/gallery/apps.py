from django.apps import AppConfig


class GalleryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gallery'

    def ready(self):
        # Фотография галереи — обычный ImageField, а не storage: прав, TTL и
        # дедупликации ей не нужно (ARCHITECTURE.md, 1.1). Убрать за собой
        # файл при удалении записи всё равно надо — этим и занимается
        # register_file_cleanup.
        from storage.mediafiles import register_file_cleanup

        register_file_cleanup(self.get_model('GalleryImage'), 'image')
