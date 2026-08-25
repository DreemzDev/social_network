/**
 * Голосование в опросе записи.
 *
 * Обработчик один на страницу и делегированный: блоки опроса приходят и с
 * первой отрисовкой, и позже — кнопкой «Есть новые посты», и подменяются
 * целиком после каждого голоса. Обработчик, навешенный на сами кнопки,
 * после первой же подмены перестал бы работать.
 *
 * Разметка блока приходит с сервера тем же шаблоном, что рисует его при
 * загрузке страницы (includes/posts/poll.html) — своей копии разметки
 * здесь нет намеренно.
 */
(function () {
  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[2]) : '';
  }

  // Список выбравших вариант — в общей модалке портала (base.html).
  document.addEventListener('click', function (event) {
    const voters = event.target.closest('.poll-voters');
    if (!voters) return;
    window.openLikersModal(
      voters.dataset.votersUrl,
      'Выбрали «' + voters.dataset.option + '»',
      'Этот вариант пока никто не выбрал',
    );
  });

  document.addEventListener('click', function (event) {
    const button = event.target.closest('.poll-option');
    if (!button) return;

    const poll = button.closest('.poll');
    if (!poll || poll.dataset.busy === '1') return;

    // Пока идёт запрос, второй клик игнорируется: иначе быстрый двойной
    // клик по варианту успевал бы поставить и тут же снять голос.
    poll.dataset.busy = '1';

    const body = new FormData();
    body.append('csrfmiddlewaretoken', getCookie('csrftoken'));
    body.append('option_id', button.dataset.optionId);

    fetch(poll.dataset.voteUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': getCookie('csrftoken') },
      body: body,
    })
      .then(function (response) {
        if (!response.ok) throw new Error('Не удалось учесть голос');
        return response.json();
      })
      .then(function (data) {
        poll.outerHTML = data.html;
      })
      .catch(function (error) {
        poll.dataset.busy = '';
        alert(error.message);
      });
  });
})();
