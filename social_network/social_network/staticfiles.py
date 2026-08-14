"""Адрес статики меняется вместе с самим файлом.

    /app/static/js/fm-actions.js?v=1786443760

В ответе статики есть только `Last-Modified`, и браузер кеширует такой файл
«на глазок», не спрашивая сервер часами: на этом уже сгорела починка
удаления в корзину (ccf0d250). Версия в адресе делает правку видимой сразу,
не трогая кеш неизменившихся файлов.

`ManifestStaticFilesStorage` не подошёл: он требует `collectstatic`, а
`STATIC_ROOT` здесь — он же каталог исходной статики.
"""
import os

from django.contrib.staticfiles.storage import StaticFilesStorage


class VersionedStaticFilesStorage(StaticFilesStorage):
    """`StaticFilesStorage` + `?v=<время правки>` в адресе."""

    def url(self, name):
        url = super().url(name)

        try:
            version = int(os.path.getmtime(self.path(name)))
        except Exception:
            # Статика сторонних приложений лежит мимо STATIC_ROOT. Версия —
            # дополнение, а не условие работы: рендер из-за неё не валим.
            return url

        return '{}{}v={}'.format(url, '&' if '?' in url else '?', version)
