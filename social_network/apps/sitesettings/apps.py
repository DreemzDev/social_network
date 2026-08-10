from django.apps import AppConfig


class SitesettingsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'sitesettings'
    verbose_name = 'Настройки сайта'

    def ready(self):
        # Запись здесь одна и не удаляется (SiteSettings.delete() — no-op),
        # поэтому смысл регистрации в другом: каждая смена логотипа в админке
        # оставляла прежний файл на диске.
        from storage.mediafiles import register_file_cleanup

        register_file_cleanup(self.get_model('SiteSettings'), 'logo')
