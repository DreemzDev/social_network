from django.apps import AppConfig


class PostsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'posts'

    def ready(self):
        from storage.mediafiles import register_file_cleanup

        # Post.photo — устаревшее поле (изображения переехали в PostImage),
        # но пока оно есть и в нём есть данные, файл за собой оно оставляет
        # так же, как любое другое.
        register_file_cleanup(self.get_model('Post'), 'photo')
        register_file_cleanup(self.get_model('PostImage'), 'image')

        # Вложенные документы поста (PostFile) сюда не входят: они лежат в
        # storage, где у файла своя судьба — дедупликация и отложенное
        # удаление через detach() (storage/signals.py).
