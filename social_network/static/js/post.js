
        function getPosts() {
            $.ajax({
                url: "{% url 'home' %}",
                type: 'get',
                dataType: 'json',
                success: function(response) {
                    // Если данные успешно получены, то создаем карточки для каждого поста
                    $.each(response, function(index, post) {
                        var card = '<div class="col-md-4">' +
                                   '<div class="card">' +
                                   '<div class="card-body">' +
                                   
                                   '<p class="card-text">' + content + '</p>' +
                                   '</div>' +
                                   '</div>' +
                                   '</div>';
                        $('#post-container').append(card);
                    });
                }
            });
        }
        // Вызываем функцию для получения списка постов
        getPosts();

