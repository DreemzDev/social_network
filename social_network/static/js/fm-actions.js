/*
 * Файловый менеджер — общий клиентский слой для всех трёх модулей
 * (обменник, информационный каталог, приватный доступ).
 *
 * Главное отличие от первой редакции: страница больше не перезагружается.
 * Раньше каждое действие заканчивалось window.location.reload() — при
 * переименовании одного файла терялись позиция скролла, раскрытые
 * дропдауны и выделение, а чужие изменения не появлялись вообще, пока
 * пользователь сам не обновит страницу. Теперь:
 *
 *   1. действие уходит POST'ом и подтверждается тостом (компонент Midone
 *      $.toast, тот же, что и во всей теме);
 *   2. сетка карточек дозапрашивается у той же вьюхи с ?partial=1 и
 *      подменяется куском DOM (storage.utils.PartialGridMixin);
 *   3. остальные, кто сейчас смотрит эту же папку, получают WebSocket-
 *      событие (storage/realtime.py) и делают то же самое у себя.
 *
 * Разметка карточек при этом остаётся серверной: HTML для подмены отдаёт
 * Django-шаблон, а не JS. Собирать карточку на клиенте означало бы вторую
 * реализацию тех же прав ({% if item.owner_id == request.user.pk %}), и
 * расходиться эти две реализации начали бы при первом изменении правил.
 *
 * Действия объявляются декларативно прямо на кнопке:
 *   data-fm-action="trash" data-fm-url="/exchange/trash-file/12/"
 *   data-fm-confirm="Удалить файл в корзину?" data-fm-success="Файл удалён"
 * благодаря чему в шаблонах модулей почти не остаётся своего JS.
 */
(function () {
  'use strict';

  var FM = {};
  var config = {};
  var socket = null;
  var reconnectDelay = 1000;
  var refreshTimer = null;

  /* ------------------------------------------------------------------ */
  /* Базовое                                                             */
  /* ------------------------------------------------------------------ */

  function csrfToken() {
    var input = document.querySelector('input[name=csrfmiddlewaretoken]');
    if (input) return input.value;
    var match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  /**
   * Тост в стилистике темы: Midone поставляет jquery-toast-plugin в своём
   * бандле (.jq-toast-* в app.css), поэтому берём готовый компонент, а не
   * рисуем собственные всплывашки. Если по какой-то причине плагина нет —
   * тихо падаем в console, но не роняем действие пользователя.
   */
  FM.toast = function (text, type) {
    var $ = window.jQuery;
    if (!$ || !$.toast) {
      if (type === 'error') console.error(text); else console.log(text);
      return;
    }
    $.toast({
      text: text,
      showHideTransition: 'slide',
      position: 'bottom-right',
      hideAfter: type === 'error' ? 6000 : 3000,
      icon: type === 'error' ? 'error' : (type === 'info' ? 'info' : 'success'),
      loader: false,
      stack: 4,
    });
  };

  /**
   * Подтверждение действия модалкой вместо window.confirm(). Нативный
   * диалог выбивался из оформления портала и, что важнее, применялся
   * непоследовательно: массовое удаление спрашивало подтверждение, а
   * удаление одного файла — нет.
   */
  FM.confirm = function (options) {
    options = options || {};
    return new Promise(function (resolve) {
      var overlay = document.createElement('div');
      overlay.className = 'fm-confirm fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4';
      overlay.innerHTML =
        '<div class="w-full max-w-sm box p-6" role="alertdialog" aria-modal="true">' +
        '  <h3 class="text-lg font-medium">' + escapeHtml(options.title || 'Подтвердите действие') + '</h3>' +
        (options.text ? '  <p class="mt-2 text-sm text-gray-600">' + escapeHtml(options.text) + '</p>' : '') +
        '  <div class="flex justify-end gap-2 mt-5">' +
        '    <button type="button" data-fm-confirm-cancel class="button bg-gray-200 text-gray-600">Отмена</button>' +
        '    <button type="button" data-fm-confirm-ok class="button text-white ' +
        (options.danger === false ? 'bg-theme-1' : 'bg-theme-6') + '">' +
        escapeHtml(options.confirmLabel || 'Удалить') + '</button>' +
        '  </div>' +
        '</div>';

      function close(result) {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(result);
      }
      function onKey(e) {
        if (e.key === 'Escape') close(false);
        if (e.key === 'Enter') close(true);
      }

      overlay.querySelector('[data-fm-confirm-ok]').addEventListener('click', function () { close(true); });
      overlay.querySelector('[data-fm-confirm-cancel]').addEventListener('click', function () { close(false); });
      overlay.addEventListener('click', function (e) { if (e.target === overlay) close(false); });
      document.addEventListener('keydown', onKey);

      document.body.appendChild(overlay);
      overlay.querySelector('[data-fm-confirm-ok]').focus();
    });
  };

  function escapeHtml(value) {
    var div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  }

  /**
   * POST с CSRF. Возвращает {ok, data} и НИКОГДА не выбрасывает: раньше
   * ответ 403 (PermissionDenied отдаёт HTML) ронял r.json(), промис
   * отклонялся, .catch() нигде не было — и кнопка просто молча не
   * срабатывала, без единого признака ошибки для пользователя.
   */
  FM.post = function (url, data) {
    var body = new FormData();
    Object.keys(data || {}).forEach(function (key) {
      var value = data[key];
      if (Array.isArray(value)) {
        value.forEach(function (item) { body.append(key, item); });
      } else if (value !== undefined && value !== null) {
        body.append(key, value);
      }
    });

    return fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken() }, body: body })
      .then(function (response) {
        return response.text().then(function (text) {
          var data;
          try { data = JSON.parse(text); } catch (err) { data = null; }
          if (data === null) {
            data = { success: false, error: httpErrorText(response.status) };
          }
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .catch(function () {
        return { ok: false, status: 0, data: { success: false, error: 'Нет связи с сервером' } };
      });
  };

  function httpErrorText(status) {
    if (status === 403) return 'Недостаточно прав для этого действия';
    if (status === 404) return 'Объект не найден — возможно, его уже удалили';
    if (status >= 500) return 'Ошибка на сервере, попробуйте позже';
    return 'Не удалось выполнить действие';
  }

  /**
   * POST + тост + обновление сетки. Единая точка: любое действие обязано
   * либо показать успех, либо показать причину отказа.
   */
  FM.action = function (url, data, options) {
    options = options || {};
    return FM.post(url, data).then(function (result) {
      if (result.ok && result.data.success !== false) {
        if (options.successText) FM.toast(options.successText, 'success');
        if (options.refresh !== false) FM.refresh();
        if (options.onSuccess) options.onSuccess(result.data);
      } else {
        FM.toast(result.data.error || httpErrorText(result.status), 'error');
        if (options.onError) options.onError(result.data);
      }
      return result;
    });
  };

  /* ------------------------------------------------------------------ */
  /* Обновление сетки                                                    */
  /* ------------------------------------------------------------------ */

  function gridElement() {
    return document.getElementById('fm-grid');
  }

  /**
   * Перезапрашивает текущий список у той же вьюхи с ?partial=1 и заменяет
   * содержимое #fm-grid. Выделенные чекбоксы восстанавливаются по id —
   * иначе чужое изменение в папке сбрасывало бы уже набранное выделение
   * перед массовой операцией.
   */
  FM.refresh = function (url) {
    var grid = gridElement();
    if (!grid) return Promise.resolve();

    var target = url || window.location.href;
    var separator = target.indexOf('?') === -1 ? '?' : '&';
    var selected = selectedIds();

    grid.classList.add('fm-grid--loading');

    return fetch(target + separator + 'partial=1', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (response) {
        if (!response.ok) throw new Error('partial failed');
        return response.text();
      })
      .then(function (html) {
        grid.innerHTML = html;
        restoreSelection(selected);
        updateBulkBar();
      })
      .catch(function () {
        FM.toast('Не удалось обновить список', 'error');
      })
      .finally(function () {
        grid.classList.remove('fm-grid--loading');
      });
  };

  // Пачка изменений (массовая операция, несколько загрузок подряд) не должна
  // выливаться в пачку одинаковых запросов сетки.
  function refreshDebounced() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () { FM.refresh(); }, 250);
  }

  /**
   * Переход по странице/сортировке/размеру страницы — тоже без
   * перезагрузки: меняем URL через history.pushState и перерисовываем
   * сетку. Ссылка остаётся обычной <a href> и продолжает работать при
   * открытии в новой вкладке и без JS.
   */
  FM.navigate = function (url) {
    window.history.pushState({}, '', url);
    return FM.refresh(url);
  };

  /* ------------------------------------------------------------------ */
  /* Выделение и массовые действия                                       */
  /* ------------------------------------------------------------------ */

  function selectedCheckboxes() {
    var grid = gridElement();
    return grid ? Array.prototype.slice.call(grid.querySelectorAll('.fm-select:checked')) : [];
  }

  function selectedIds() {
    return selectedCheckboxes()
      .filter(function (el) { return el.dataset.fmKind !== 'folder'; })
      .map(function (el) { return el.dataset.fmId; });
  }

  function selectedFolderIds() {
    return selectedCheckboxes()
      .filter(function (el) { return el.dataset.fmKind === 'folder'; })
      .map(function (el) { return el.dataset.fmId; });
  }

  function restoreSelection(ids) {
    if (!ids.length) return;
    var grid = gridElement();
    if (!grid) return;
    ids.forEach(function (id) {
      var box = grid.querySelector('.fm-select[data-fm-id="' + id + '"]');
      if (box) box.checked = true;
    });
  }

  function updateBulkBar() {
    var bar = document.getElementById('fm-bulk-bar');
    if (!bar) return;

    var files = selectedIds();
    var folders = selectedFolderIds();
    var total = files.length + folders.length;

    bar.classList.toggle('hidden', total === 0);

    var counter = bar.querySelector('.fm-bulk-count');
    if (counter) counter.textContent = total;

    // Кнопки, работающие только с файлами, гаснут, если выбраны одни папки —
    // раньше чекбокс на папке вообще ничего не значил (не имел .fm-select),
    // то есть выглядел рабочим и молча игнорировался.
    bar.querySelectorAll('[data-fm-files-only]').forEach(function (btn) {
      btn.disabled = files.length === 0;
      btn.classList.toggle('opacity-50', files.length === 0);
    });

    var selectAll = document.getElementById('fm-select-all');
    if (selectAll) {
      var boxes = gridElement() ? gridElement().querySelectorAll('.fm-select') : [];
      selectAll.checked = boxes.length > 0 && total === boxes.length;
      selectAll.indeterminate = total > 0 && total < boxes.length;
    }
  }

  /* ------------------------------------------------------------------ */
  /* Фоновые задачи (массовые операции через Celery)                      */
  /* ------------------------------------------------------------------ */

  FM.pollTask = function (taskId, options) {
    options = options || {};
    var intervalMs = options.intervalMs || 700;

    /**
     * Сколько ждать, пока задачу подхватит воркер. Состояние PENDING
     * означает «в очереди, никто не взял»: при незапущенном Celery оно
     * держится вечно, и опрос крутился бы бесконечно, показывая
     * «Перемещение…» — то есть действие молча не работало бы, вопреки
     * правилу «либо успех, либо причина отказа».
     */
    var pendingTimeoutMs = options.pendingTimeoutMs || 20000;
    var startedAt = Date.now();

    function fail(text) {
      if (options.onError) options.onError(text);
    }

    function poll() {
      fetch('/storage/task-status/' + taskId + '/')
        .then(function (r) {
          // Ответ разбирается вместе со статусом: на 404 (чужая или
          // забытая задача) и 403 тело — это {error}, а не {state}, и
          // прежняя версия уходила в ветку «неизвестное состояние» и
          // опрашивала дальше без конца.
          return r.json().catch(function () { return {}; }).then(function (payload) {
            return { ok: r.ok, status: r.status, payload: payload };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            fail(result.payload.error || httpErrorText(result.status));
            return;
          }

          var payload = result.payload;

          if (payload.state === 'PROGRESS') {
            startedAt = Date.now();  // пошёл прогресс — отсчёт ожидания сбрасываем
            if (options.onProgress) options.onProgress(payload);
            setTimeout(poll, intervalMs);
          } else if (payload.state === 'SUCCESS') {
            if (options.onSuccess) options.onSuccess(payload);
          } else if (payload.state === 'FAILURE') {
            fail(payload.error || 'Задача завершилась с ошибкой');
          } else if (Date.now() - startedAt > pendingTimeoutMs) {
            fail('Задача так и не начала выполняться. Возможно, служба фоновых задач не запущена.');
          } else {
            setTimeout(poll, intervalMs);
          }
        })
        .catch(function () {
          fail('Не удалось получить статус задачи');
        });
    }
    poll();
  };

  function runBulk(url, payload, progressText) {
    var progress = document.getElementById('fm-bulk-progress');
    if (progress) {
      progress.classList.remove('hidden');
      progress.textContent = progressText + '…';
    }

    return FM.post(url, payload).then(function (result) {
      if (!result.ok || result.data.success === false) {
        if (progress) progress.classList.add('hidden');
        FM.toast(result.data.error || httpErrorText(result.status), 'error');
        return;
      }

      if (!result.data.task_id) {
        if (progress) progress.classList.add('hidden');
        // message приходит, когда сервер сознательно ничего не сделал —
        // например, среди выбранного не оказалось ни одного объекта, на
        // который у пользователя есть права. Такой ответ намеренно 200
        // (чтобы не подтверждать существование чужих id), и без текста
        // выглядел бы как «кнопка не работает».
        if (result.data.message) {
          FM.toast(result.data.message, 'info');
        } else {
          FM.toast(progressText + ': готово', 'success');
        }
        FM.refresh();
        return;
      }

      FM.pollTask(result.data.task_id, {
        onProgress: function (payload) {
          if (progress) progress.textContent = progressText + ': ' + payload.done + ' из ' + payload.total;
        },
        onSuccess: function (payload) {
          if (progress) progress.classList.add('hidden');
          FM.toast(progressText + ': готово (' + (payload.done || 0) + ')', 'success');
          FM.refresh();
        },
        onError: function (error) {
          if (progress) progress.classList.add('hidden');
          FM.toast(error, 'error');
        },
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* Загрузка файлов                                                     */
  /* ------------------------------------------------------------------ */

  /**
   * XHR, а не fetch: прогресс ЗАГРУЗКИ (upload.onprogress) fetch не даёт —
   * он умеет отслеживать только скачивание ответа.
   */
  FM.upload = function (url, formData, options) {
    options = options || {};
    var xhr = new XMLHttpRequest();
    xhr.open('POST', url, true);
    xhr.setRequestHeader('X-CSRFToken', csrfToken());

    xhr.upload.addEventListener('progress', function (e) {
      if (e.lengthComputable && options.onProgress) {
        options.onProgress(Math.round((e.loaded / e.total) * 100));
      }
    });

    xhr.addEventListener('load', function () {
      var data;
      try { data = JSON.parse(xhr.responseText); } catch (err) { data = null; }
      if (data === null) data = { success: false, error: httpErrorText(xhr.status) };

      if (xhr.status >= 200 && xhr.status < 300 && data.success) {
        if (options.onSuccess) options.onSuccess(data);
      } else if (options.onError) {
        options.onError(data.error || httpErrorText(xhr.status));
      }
    });

    xhr.addEventListener('error', function () {
      if (options.onError) options.onError('Не удалось загрузить файл — проверьте соединение');
    });

    xhr.send(formData);
    return xhr;
  };

  /**
   * Загрузка перетаскиванием на список — то, чего в первую очередь ждут от
   * файлового менеджера. Раньше единственным путём была модалка с
   * <input type=file>.
   */
  function initDragAndDrop() {
    if (!config.uploadUrl) return;

    var zone = document.getElementById('fm-dropzone');
    var hint = document.getElementById('fm-drop-hint');
    if (!zone) return;

    var depth = 0;

    function show() { if (hint) hint.classList.add('is-visible'); }
    function hide() { depth = 0; if (hint) hint.classList.remove('is-visible'); }

    ['dragenter', 'dragover'].forEach(function (type) {
      zone.addEventListener(type, function (e) {
        if (!e.dataTransfer || Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') === -1) return;
        e.preventDefault();
        if (type === 'dragenter') depth += 1;
        show();
      });
    });

    zone.addEventListener('dragleave', function () {
      depth -= 1;
      if (depth <= 0) hide();
    });

    zone.addEventListener('drop', function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault();
      hide();
      uploadFiles(e.dataTransfer.files);
    });
  }

  function uploadFiles(files) {
    var formData = new FormData();
    Array.prototype.forEach.call(files, function (file) {
      formData.append(config.uploadFileField || 'files', file);
    });
    Object.keys(config.uploadExtra || {}).forEach(function (key) {
      formData.append(key, config.uploadExtra[key]);
    });

    var count = files.length;
    FM.toast('Загрузка: ' + count + ' файл(ов)…', 'info');

    FM.upload(config.uploadUrl, formData, {
      onSuccess: function () {
        FM.toast('Загружено файлов: ' + count, 'success');
        FM.refresh();
      },
      onError: function (error) { FM.toast(error, 'error'); },
    });
  }

  /* ------------------------------------------------------------------ */
  /* WebSocket — чужие изменения в этой же папке                          */
  /* ------------------------------------------------------------------ */

  function connectRealtime() {
    if (!config.scope || config.location === undefined || config.location === null) return;
    if (!window.WebSocket) return;

    var protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
    var url = protocol + window.location.host + '/ws/fm/' + config.scope + '/' + config.location + '/';

    try {
      socket = new WebSocket(url);
    } catch (err) {
      return;
    }

    socket.onopen = function () { reconnectDelay = 1000; };

    socket.onmessage = function (event) {
      var payload;
      try { payload = JSON.parse(event.data); } catch (err) { return; }

      // Своё же действие пользователь уже видел — тост о нём был показан
      // локально сразу после ответа сервера. Сетку всё равно обновляем:
      // это дёшево и гарантирует, что видно ровно то, что в базе.
      if (payload.actor_id && config.userId && String(payload.actor_id) === String(config.userId)) {
        refreshDebounced();
        return;
      }

      if (payload.text) {
        FM.toast((payload.actor_name ? payload.actor_name + ' ' : '') + payload.text, 'info');
      }
      refreshDebounced();
    };

    // Соединение рвётся при спящем ноутбуке, перезапуске daphne, обрыве
    // сети. Без переподключения страница молча перестаёт быть живой, и
    // пользователь узнаёт об этом, только не увидев чужих изменений.
    socket.onclose = function () {
      setTimeout(function () {
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        connectRealtime();
      }, reconnectDelay);
    };
  }

  /* ------------------------------------------------------------------ */
  /* Инициализация                                                       */
  /* ------------------------------------------------------------------ */

  FM.init = function (options) {
    config = options || {};

    var grid = gridElement();
    if (grid) {
      grid.addEventListener('change', function (e) {
        if (e.target.classList.contains('fm-select')) updateBulkBar();
      });
    }

    var selectAll = document.getElementById('fm-select-all');
    if (selectAll) {
      selectAll.addEventListener('change', function () {
        var boxes = gridElement().querySelectorAll('.fm-select');
        boxes.forEach(function (box) { box.checked = selectAll.checked; });
        updateBulkBar();
      });
    }

    initBulkButtons();
    initDragAndDrop();
    connectRealtime();
    updateBulkBar();

    window.addEventListener('popstate', function () { FM.refresh(); });
  };

  /*
   * Делегирование на document, а не на контейнер сетки: те же кнопки
   * (восстановить / удалить окончательно) живут на странице корзины, где
   * никакого #fm-grid нет, а карточки лежат в трёх независимых списках по
   * табам. Один обработчик на документ избавляет от третьей копии этой же
   * логики. Кнопки сами несут в data-атрибутах и URL, и текст
   * подтверждения, поэтому обработчику не нужно знать, какой это модуль.
   */
  document.addEventListener('click', function (e) {
    var link = e.target.closest('.fm-page-link, .fm-sort-link');
    if (link && gridElement()) {
      e.preventDefault();
      FM.navigate(link.getAttribute('href'));
      return;
    }

    var button = e.target.closest('[data-fm-action]');
    if (!button) return;

    e.preventDefault();
    var url = button.dataset.fmUrl;
    if (!url) return;

    var successText = button.dataset.fmSuccess || 'Готово';
    // На странице корзины обновлять нечего — там нет сетки с ?partial=1,
    // поэтому карточка просто убирается из своего списка.
    var card = button.closest('.fm-card');
    var removeCard = button.dataset.fmRemoveCard === '1';

    function run() {
      FM.action(url, {}, {
        successText: successText,
        refresh: !removeCard,
        onSuccess: function () {
          if (removeCard && card) card.remove();
        },
      });
    }

    var confirmText = button.dataset.fmConfirm;
    if (!confirmText) {
      run();
      return;
    }

    FM.confirm({
      title: button.dataset.fmConfirmTitle || 'Подтвердите действие',
      text: confirmText,
      confirmLabel: button.dataset.fmConfirmLabel || 'Удалить',
    }).then(function (confirmed) { if (confirmed) run(); });
  });

  // Селектор размера страницы (в макете Midone он был декоративным).
  document.addEventListener('change', function (e) {
    var select = e.target.closest('.fm-per-page');
    if (!select) return;
    FM.navigate(select.value);
  });

  /**
   * Скачивание выбранного одним zip.
   *
   * Два шага, и это не перестраховка. Сам архив забирается обычной
   * навигацией (window.location), потому что скачать его через fetch
   * означало бы держать весь zip в памяти вкладки — на 1 ГБ это верное
   * падение. Но у навигации нет способа показать ошибку: ответ с текстом
   * отказа просто заменил бы собой страницу. Поэтому сначала спрашиваем
   * разрешение (?check=1) и показываем причину отказа тостом, и лишь
   * потом уходим по ссылке.
   */
  function bulkDownload(ids) {
    var url = config.bulkDownloadUrl + '?ids=' + ids.join(',');

    fetch(url + '&check=1', { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (payload) {
          return { ok: r.ok, status: r.status, payload: payload };
        });
      })
      .then(function (result) {
        if (!result.ok || result.payload.success === false) {
          FM.toast(result.payload.error || httpErrorText(result.status), 'error');
          return;
        }

        // Часть выбранного могла оказаться недоступной (чужая закрытая
        // папка) или уже удалённой. Молча отдать неполный архив нельзя —
        // человек не узнал бы, что в нём не всё.
        if (result.payload.skipped > 0) {
          // Именно 'info': FM.toast знает только error/info/success, и
          // несуществующий тип молча отрисовался бы зелёной галочкой —
          // предупреждение выглядело бы как «всё хорошо».
          FM.toast('Пропущено файлов без доступа: ' + result.payload.skipped, 'info');
        }

        FM.toast('Готовим архив: файлов ' + result.payload.count, 'success');
        window.location = url;
      })
      .catch(function () {
        FM.toast('Не удалось подготовить архив', 'error');
      });
  }

  function initBulkButtons() {
    var trashBtn = document.getElementById('fm-bulk-trash');
    if (trashBtn && config.bulkTrashUrl) {
      trashBtn.addEventListener('click', function () {
        var ids = selectedIds();
        if (!ids.length) return;

        FM.confirm({
          title: 'Удалить в корзину',
          text: 'Выбрано объектов: ' + ids.length + '. Их можно будет восстановить из корзины.',
          confirmLabel: 'Удалить',
        }).then(function (confirmed) {
          if (!confirmed) return;
          var payload = {};
          payload[config.idField || 'doc_ids'] = ids;
          runBulk(config.bulkTrashUrl, payload, 'Удаление в корзину');
        });
      });
    }

    var moveBtn = document.getElementById('fm-bulk-move');
    if (moveBtn && config.bulkMoveUrl && config.onBulkMove) {
      moveBtn.addEventListener('click', function () {
        var ids = selectedIds();
        if (!ids.length) return;
        config.onBulkMove(ids);
      });
    }

    var downloadBtn = document.getElementById('fm-bulk-download');
    if (downloadBtn && config.bulkDownloadUrl) {
      downloadBtn.addEventListener('click', function () {
        var ids = selectedIds();
        if (!ids.length) return;
        bulkDownload(ids);
      });
    }

    var clearBtn = document.getElementById('fm-bulk-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        var grid = gridElement();
        if (grid) grid.querySelectorAll('.fm-select').forEach(function (box) { box.checked = false; });
        updateBulkBar();
      });
    }
  }

  FM.runBulkMove = function (ids, folderId) {
    var payload = { folder_id: folderId };
    payload[config.idField || 'doc_ids'] = ids;
    return runBulk(config.bulkMoveUrl, payload, 'Перемещение');
  };

  FM.selectedIds = selectedIds;
  FM.config = function () { return config; };

  window.FM = FM;

  /* Совместимость с ранее написанными обработчиками в шаблонах. */
  window.fmPostForm = FM.post;
  window.fmUploadWithProgress = FM.upload;
  window.fmPollTaskStatus = FM.pollTask;
})();
