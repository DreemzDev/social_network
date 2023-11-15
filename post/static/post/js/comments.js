$(document).ready(function() {
    // Добавление комментария
    $('#comment-form').submit(function(e) {
        e.preventDefault();
 
        var form = $(this);
        var url = form.attr('action');
        var data = form.serialize();
 
        $.ajax({
            type: 'POST',
            url: url,
            data: data,
            success: function(response) {
                if (response.status === 'success') {
                    // Очистка формы
                    form[0].reset();
                    // Обновление списка комментариев
                    loadComments();
                } else {
                    console.log('Ошибка при добавлении комментария');
                }
            },
            error: function() {
                console.log('Ошибка при отправке запроса');
            },
        });
    });
 
    // Редактирование комментария
    $('.edit-comment-link').click(function(e) {
        e.preventDefault();
 
        var commentId = $(this).data('comment-id');
        var commentDiv = $('#comment-' + commentId);
        var commentText = commentDiv.find('p').text().trim();
 
        var editForm = $('<form>');
        editForm.attr('id', 'edit-comment-form-' + commentId);
        editForm.attr('method', 'post');
        editForm.attr('action', '/edit-comment/' + commentId);
 
        var csrfToken = $('input[name="csrfmiddlewaretoken"]').val();
        var csrfInput = $('<input>');
        csrfInput.attr('type', 'hidden');
        csrfInput.attr('name', 'csrfmiddlewaretoken');
        csrfInput.val(csrfToken);
 
        var textarea = $('<textarea>');
        textarea.attr('name', 'text');
        textarea.text(commentText);
 
        var submitButton = $('<button>');
        submitButton.attr('type', 'submit');
        submitButton.text('Сохранить');
 
        editForm.append(csrfInput);
        editForm.append(textarea);
        editForm.append(submitButton);
 
        commentDiv.html(editForm);
    });
 
    // Удаление комментария
    $('.delete-comment-link').click(function(e) {
        e.preventDefault();
 
        var commentId = $(this).data('comment-id');
 
        $.ajax({
            type: 'POST',
            url: '/delete-comment/' + commentId,
            data: {'csrfmiddlewaretoken': $('input[name="csrfmiddlewaretoken"]').val()},
            success: function(response) {
                if (response.status === 'success') {
                    // Удаление комментария из списка
                    $('#comment-' + commentId).remove();
                } else {
                    console.log('Ошибка при удалении комментария');
                }
            },
            error: function() {
                console.log('Ошибка при отправке запроса');
            },
        });
    });
 
    // Загрузка списка комментариев
    function loadComments() {
        $.ajax({
            type: 'GET',
            url: '/load-comments/',
            success: function(response) {
                if (response.status === 'success') {
                    // Обновление списка комментариев
                    $('#comments-list').html(response.data);
                } else {
                    console.log('Ошибка при загрузке комментариев');
                }
            },
            error: function() {
                console.log('Ошибка при отправке запроса');
            },
        });
    }
 
    // Загрузка списка комментариев при загрузке страницы
    loadComments();
});