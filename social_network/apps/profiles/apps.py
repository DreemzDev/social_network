from django.apps import AppConfig


class ProfilesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'profiles'

    def ready(self):
        from . import signals  # noqa: F401
        from storage.mediafiles import register_file_cleanup

        # Аватар и обложку меняют, а не удаляют, и до сих пор каждая смена
        # оставляла прежний файл на диске навсегда.
        register_file_cleanup(self.get_model('User'), 'avatar', 'cover')
