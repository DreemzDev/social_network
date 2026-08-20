/*
 * Общий поиск в шапке портала.
 *
 * Показ и скрытие выпадающего списка делает сама тема: её search.js вешает
 * focus/focusout на .search input и переключает класс .show у .search-result.
 * Здесь только содержимое — разметку рисует сервер (includes/layout/
 * search_results.html), тем же шаблоном, что и страницу результатов.
 *
 * Главная тонкость — focusout темы срабатывает РАНЬШЕ click по ссылке
 * результата: список успевает закрыться, и переход не происходит. Поэтому
 * mousedown внутри списка гасится preventDefault — поле не теряет фокус,
 * focusout не наступает, click доходит до ссылки.
 */
(function () {
  'use strict';

  var DEBOUNCE_MS = 300;
  var MIN_LENGTH = 2;

  document.addEventListener('DOMContentLoaded', function () {
    var input = document.getElementById('global-search');
    var box = document.getElementById('global-search-result');
    if (!input || !box) return;

    var timer = null;
    var lastQuery = null;
    var requestId = 0;

    function render(html) {
      box.innerHTML = html;
      // Иконки в подставленной разметке — <i data-feather>, их надо
      // превратить в svg: feather.replace() отработал при загрузке страницы,
      // когда этих узлов ещё не было.
      if (window.feather) window.feather.replace();
    }

    function load(query) {
      var current = ++requestId;

      fetch('/search/?q=' + encodeURIComponent(query), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (response) {
          if (!response.ok) throw new Error('search failed');
          return response.text();
        })
        .then(function (html) {
          // Ответы могут прийти не в том порядке, в каком уходили запросы:
          // без этой проверки медленный ответ на «ив» затирал бы готовый
          // результат по «иванов».
          if (current !== requestId) return;
          render(html);
        })
        .catch(function () {
          if (current !== requestId) return;
          render('<div class="search-result__content">'
            + '<div class="px-1 py-2 text-sm text-gray-600">Не удалось выполнить поиск</div></div>');
        });
    }

    input.addEventListener('input', function () {
      var query = input.value.trim();
      clearTimeout(timer);

      if (query === lastQuery) return;
      lastQuery = query;

      if (query.length < MIN_LENGTH) {
        requestId++;                       // отменяем ещё не пришедший ответ
        box.innerHTML = '';
        return;
      }

      timer = setTimeout(function () { load(query); }, DEBOUNCE_MS);
    });

    // Enter — на страницу со всеми результатами.
    input.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var query = input.value.trim();
      if (query.length >= MIN_LENGTH) {
        window.location = '/search/?q=' + encodeURIComponent(query);
      }
    });

    // См. комментарий в шапке файла: без этого клик по результату не доходит.
    box.addEventListener('mousedown', function (e) { e.preventDefault(); });
  });
})();
