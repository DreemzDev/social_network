# Контекст для новых сессий — storage и файловые модули

Служебный файл, не документация проекта. Цель — дать новой сессии Claude
достаточно контекста без пересказа всей переписки. Архитектурные решения и
их обоснования — в `ARCHITECTURE.md` рядом с этим файлом; здесь только
состояние и то, что не попало бы в архитектурный документ.

## Что это за проект

Django 5.0.1 + Channels/Daphne (ASGI) внутренний портал организации
(«Сеть-УК»). Windows-разработка (`c:\Users\User\Desktop\social_network`),
прод предполагается на Astra Linux. Git user `DreemzDev`, ветка `main`.

Реальное окружение: PostgreSQL (`portal`/`postgres`/`123`@127.0.0.1:5432),
Redis для Channels и Celery (`db=1` для Celery, чтобы не смешивать с
WS-трафиком). Виртуальное окружение — `.venv` в корне репозитория (НЕ `venv`,
тот путь битый и упоминается только в устаревшем `start_server.bat`).

Способ запуска в этой сессии:
```bash
cd c:/Users/User/Desktop/social_network/social_network
/c/Users/User/Desktop/social_network/.venv/Scripts/daphne.exe -b 127.0.0.1 -p 8000 social_network.asgi:application
```

## Статус git — ВАЖНО

На момент написания этого файла всё, что описано ниже (`storage`, `exchange`,
`catalog`, `deptdocs`, celery, вёрстка Midone) **не закоммичено**. Это
рабочее дерево, не история. Если новая сессия видит другое состояние
`git log` — верить коду и `git status`, не этому файлу.

## Модуль storage — единый сервис хранения файлов

Полное архитектурное обоснование — `apps/storage/ARCHITECTURE.md` (читать
его, если нужно понять «почему», не «что»). Коротко: `FileBlob` (физическое
содержимое, `checksum` unique, дедупликация) + `FileObject` (именованное
использование, на него ссылаются потребители). Права доступа — не в storage,
их проверяет каждый модуль-потребитель сам.

Ключевые баги, найденные аудитом и исправленные (см. `git log` в этом
приложении после коммита, тесты в `apps/storage/tests/`):

1. **`_object_has_references()` всегда возвращала `False`** — обходила
   `FileObject._meta.get_fields()`, но все потребители используют
   `related_name='+'`, который подавляет обратные связи. Исправлено на обход
   `apps.get_models()`. Тест: `test_references.py`.
2. **Прямой доступ к файлам в обход прав** — `/media/storage/blobs/<checksum>`
   раздавался как обычная медиатека. Закрыто перехватчиком `^media/storage/`
   в корневом `urls.py` (404 до `static()`). Тест: `test_direct_access.py`.
3. **Автоочистка по TTL не работала** — `purge_expired_objects()` в storage
   вызывала `detach()`, который защищал файлы с живыми ссылками, но функция
   отчитывалась об успехе, не удалив ничего. Убрана из storage; каждый
   потребитель, которому нужен TTL, пишет свою periodic task (см.
   `exchange.tasks.cleanup_expired_exchange_files`).
4. **Каскадное удаление обходило `detach()`** — чек-лист требовал ручного
   вызова `detach()`, но каскад от FK сносит запись потребителя сам, и
   вызвать его некому. Четыре реальных пути: `CatalogFolder` →
   `CatalogDocument`, `DepartmentFolder` → `DepartmentDocument`, дерево папок
   через `parent`, `User` → `ExchangeFile` (`owner`). Blob оставался `ACTIVE`
   без ссылок навсегда: `ORPHAN` его не видит, `purge_expired_orphans()` не
   трогает, `find_untracked_files()` не считает потерянным. Исправлено
   сигналом `post_delete` в `storage/signals.py` — `detach()` выполняется
   автоматически для всех потребителей (интроспекция та же, что в 5.2).
   Атрибуция «кто удалил» передаётся пометкой `attribute_deletion()` перед
   `delete()`. Подробности — ARCHITECTURE.md, раздел 5.5. Тесты:
   `test_cascade.py`, `test_consumers.py`.

Гонки на конкурентную загрузку/purge проверены на **реальном Postgres**
(`storage/tests/test_concurrency.py`) — на SQLite `select_for_update` и
`pg_advisory_xact_lock` не работают как надо, тесты были бы декоративными.

### Как запускать тесты — грабли

Приложения лежат в `apps/`, которая добавлена в `sys.path`, поэтому метка
теста — `storage`, а **не** `apps.storage` (с последней unittest падает в
discovery с `TypeError: path should be string... not NoneType`).

```bash
.venv/Scripts/python.exe manage.py test storage --top-level-directory=apps
```

Голый `manage.py test` без аргументов находит **0 тестов** — discovery
стартует из корня проекта и в `apps/` не заходит. Единственные реальные
тесты в проекте сейчас — в `apps/storage/tests/` (27 штук); `tests.py` в
остальных приложениях — пустые заглушки Django.

## Потребители storage (текущее состояние)

| Модуль | Модель | Права | TTL |
|---|---|---|---|
| `exchange` | `ExchangeFile` | папка = пользователь, видит любой, удаляет владелец папки или загрузивший | 7 дней (`exchange.tasks`) |
| `catalog` | `CatalogFolder` + `CatalogDocument` | видно всем аутентифицированным | нет (бессрочно) |
| `deptdocs` | `DepartmentFolder` + `DepartmentDocument` | `allowed_users` M2M на конкретных людей, любой участник равноправен | нет |
| `posts` | `PostFile` | видно всем, кто видит пост | нет |
| `phonebook` | `Phonebook.file_object` | как раньше (публично) | нет |

`posts.PostImage`, `gallery.GalleryImage`, `profiles.User.avatar/cover` —
**намеренно не в storage**, обычные `ImageField` (нет прав/TTL/шаринга, см.
ARCHITECTURE.md раздел 1.1).

### exchange — важно про модель

Изначально был «файл адресован конкретному получателю» (как email-вложение).
**Переделан по прямому запросу пользователя** в сетевую папку: папка =
пользователь (нет отдельной модели папки — список папок = список
пользователей), содержимое видно всем, удалять может владелец папки или тот,
кто загрузил. Если видите в истории упоминания старой модели — это
устаревшее, реальный код смотреть в `apps/exchange/`.

### deptdocs — важно про модель

Тоже переделан один раз в процессе. Было: документ сам хранит
`allowed_departments` (M2M на `Category`). Стало: `DepartmentFolder` с
`allowed_users` (M2M на конкретных **людей**, не отделы), документ наследует
права от папки. Любой сотрудник может создать папку и раздать доступ; любой
участник списка доступа равноправен с создателем, включая право убрать
самого себя (после чего мгновенно теряет видимость папки — проверено
тестом).

## Celery

Установлен по явному запросу (расписание должно работать одинаково на
Windows-разработке и Astra Linux-проде, без Task Scheduler/cron-развилки).
`django-celery-beat`, расписание хранится в БД, заводится дата-миграцией
`storage/migrations/0002_seed_periodic_tasks.py` (потом
`0003_replace_expired_objects_task.py` убрал задачу storage под п.3 таблицы
выше). Проверено вживую: worker и beat поднимались, задача реально
выполнилась через полную цепочку `PeriodicTask → beat → worker`, не только
вызовом `.delay()` вручную.

Локальный запуск (Redis должен быть поднят отдельно, в этой сессии не
управляется):
```bash
celery -A social_network worker --loglevel=info --pool=solo
celery -A social_network beat --loglevel=info
```

## Вёрстка — Midone theme, важные грабли

Источник макетов: `Midone-master/Midone v1.0.4/HTML Version/Compiled/`.
Пользователь несколько раз просил «пиксель в пиксель» — при расхождениях
**сверяться напрямую с файлом макета**, не с памятью о нём.

Найденные фактами (не догадкой) причины расхождений между нашей вёрсткой и
макетом:

1. **`data-feather` иконки не отрисовывались в Alpine-дропдаунах.**
   `feather.replace()` в `app.js` вызывается один раз при загрузке страницы,
   не видит содержимое, которое Alpine рендерит позже (`x-show`). Решение:
   inline-SVG вместо `<i data-feather>` — см. `includes/fm_icons.html`,
   вызывается как `{% include "includes/fm_icons.html" with icon='trash' cls='...' %}`.

2. **Сетка была 4 колонки вместо 6.** Макет использует `xxl:col-span-2`
   (порог 1600px). У нас между контентом и краем экрана есть боковое меню
   портала (~230px), которого нет в изолированном HTML-макете — контент не
   дотягивал до 1600px. Решение: продублировать те же пропорции на `xl:`
   (1280px) рядом с `xxl:` — классы `xl:col-span-*` уже есть в скомпилированном
   `app.css`, свой CSS-файл не понадобился (была одна ложная попытка создать
   `xxl-grid.css` из-за ошибки в `grep`, удалена).

3. **Поле поиска выглядело иначе, чем в макете.** Поверх Midone в проекте
   подключены Tailwind (`js/tailwinds.js`) и Flowbite (`src/flowbite.min.css`),
   которые перебивают `.input` из `app.css` (`border-radius: 0`, другой
   цвет рамки, другой шрифт). Решение: `static/midone/css/fm-overrides.css`,
   область действия ограничена классом `.fm-scope` на корневом `<div>`
   файловых страниц — остального портала не касается.

Все восемь файловых страниц (`exchange/folders.html`, `exchange/folder.html`,
`exchange/trash.html`, `catalog/folder.html`, `catalog/trash.html`,
`deptdocs/list.html`, `deptdocs/trash.html`) используют общие partial'ы:
`includes/fm_menu.html`, `includes/fm_filter.html`, `includes/fm_pagination.html`,
`includes/fm_icons.html`. Корневой блок каждой — `class="fm-scope grid
grid-cols-12 gap-6 mt-8"`, эти страницы **не используют** `sidebar_wrapper`
из `base.html` — левая и правая колонки сидят в одной `grid-cols-12`, как в
оригинальном макете Midone.

Был временный эталонный маршрут `/fm-reference/` (дословная копия HTML из
макета, без бэкенда) для визуальной сверки пиксель-в-пиксель — **удалён**
после того как вёрстка сошлась. Если понадобится пересобрать — брать
`simple-menu-file-manager.html` из папки макета, менять только
`dist/images/` → `{% static 'midone/images/...' %}`.

## Демо-данные

Есть суперюзер `admin`/`admin123` и несколько демо-сотрудников
(`petrova`/`ivanov`/`sidorov`/`kuznetsova`/`smirnov`, пароль у всех
`demo12345`), с отделами (`Category`), постами, файлами в обменнике/каталоге/
документах отдела — для визуальной проверки. Список актуальных
пользователей и данных смотреть в БД, этот файл не гарантирует, что они
ещё существуют (могли быть очищены между сессиями).

## Как продолжать в новой сессии

1. Прочитать `apps/storage/ARCHITECTURE.md` — там «почему» для storage.
2. Прочитать этот файл — «что уже сделано и на чём споткнулись».
3. `git status` — проверить, что из этого списка уже закоммичено, а что
   всё ещё черновик.
4. При работе с вёрсткой файловых страниц — сверяться с оригиналом в
   `Midone-master/.../Compiled/simple-menu-file-manager.html`, не с памятью.
5. При работе со storage — гонки и права проверять на Postgres, не на
   SQLite (`manage.py test` в этом проекте настроен на реальный Postgres,
   не тестовый SQLite — см. `DATABASES` в `settings.py`).
