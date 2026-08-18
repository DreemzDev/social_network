/*
 * Подгрузка следующей страницы списка при прокрутке.
 *
 * Вынесено из шаблона «Коллег», когда такой же список понадобился
 * справочнику организации: копия этого кода во второй странице разошлась бы
 * с оригиналом при первой же правке. Вьюха отвечает на XMLHttpRequest
 * куском разметки ({'html': ..., 'has_next': ...}) — тем же партиалом, что
 * рендерит первую страницу, поэтому карточки собирает сервер, а не JS.
 *
 * Настраивается атрибутами на маячке, своего кода на странице не нужно:
 *   <div data-infinite-sentinel data-url="/phones/"
 *        data-list="#phones-list" data-loader="#phones-loader"
 *        data-has-next="1" data-next-page="2"></div>
 */
(function () {
  'use strict';

  function setup(sentinel) {
    var list = document.querySelector(sentinel.dataset.list);
    var loader = document.querySelector(sentinel.dataset.loader);
    if (!list) return;

    var loading = false;

    function loadNextPage() {
      if (sentinel.dataset.hasNext !== '1' || loading) return;

      loading = true;
      if (loader) {
        loader.classList.remove('hidden');
        loader.classList.add('flex');
      }

      // Фильтры и поиск живут в адресной строке — берём их оттуда, иначе
      // подгруженная страница пришла бы без учёта текущего фильтра.
      var params = new URLSearchParams(window.location.search);
      params.delete('page');
      params.set('page', sentinel.dataset.nextPage);

      fetch(sentinel.dataset.url + '?' + params.toString(), {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          list.insertAdjacentHTML('beforeend', data.html);
          if (data.has_next) {
            sentinel.dataset.nextPage = String(parseInt(sentinel.dataset.nextPage, 10) + 1);
          } else {
            sentinel.dataset.hasNext = '0';
          }
        })
        .catch(function () {})
        .finally(function () {
          loading = false;
          if (loader) {
            loader.classList.add('hidden');
            loader.classList.remove('flex');
          }
        });
    }

    var observer = new IntersectionObserver(function (entries) {
      if (entries[0].isIntersecting) loadNextPage();
    }, { rootMargin: '200px' });

    observer.observe(sentinel);
  }

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(
      document.querySelectorAll('[data-infinite-sentinel]'), setup
    );
  });
})();
