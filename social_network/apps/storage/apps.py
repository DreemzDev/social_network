from django.apps import AppConfig


class StorageConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'storage'

    def ready(self):
        # Подключается здесь, а не на уровне модуля: интроспекция ищет FK на
        # FileObject по всем приложениям, а к моменту ready() Django уже
        # загрузила все модели проекта.
        from .signals import register_consumer_signals

        register_consumer_signals()
