"""Опрос, прикреплённый к записи.

Главное, что здесь защищается, — правила голоса: один человек не может
насчитать себе несколько голосов в опросе с одним вариантом ответа, а
передумать может. Плюс то, ради чего результаты считаются в Python по
prefetch: лента из десятков записей не должна ходить в БД за каждой.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from posts.models import Poll, PollOption, PollVote, Post

User = get_user_model()


class PollTestCase(TestCase):

    def setUp(self):
        self.author = User.objects.create_user(username='poll_author', password='pass12345')
        self.voter = User.objects.create_user(username='poll_voter', password='pass12345')
        self.client.force_login(self.author)

    def create_post_with_poll(self, options=('Да', 'Нет'), multiple=False, content='Идём в отпуск?'):
        payload = {'content': content, 'poll_options': list(options)}
        if multiple:
            payload['poll_multiple'] = '1'
        response = self.client.post(
            reverse('addpost', args=[self.author.username]), payload
        )
        return response

    def poll(self, options=('Да', 'Нет'), multiple=False, show_voters=False):
        post = Post.objects.create(content='Идём в отпуск?', author=self.author)
        poll = Poll.objects.create(post=post, is_multiple=multiple, show_voters=show_voters)
        for order, text in enumerate(options):
            PollOption.objects.create(poll=poll, text=text, order=order)
        return poll

    def vote(self, poll, option, user=None):
        if user:
            self.client.force_login(user)
        return self.client.post(
            reverse('poll_vote', args=[poll.post_id]), {'option_id': option.pk}
        )


class PollCreationTest(PollTestCase):

    def test_poll_is_attached_to_the_new_post(self):
        self.create_post_with_poll(options=('Да', 'Нет', 'Не знаю'))

        poll = Poll.objects.get()
        self.assertEqual(poll.post.content, 'Идём в отпуск?')
        self.assertEqual(
            [option.text for option in poll.options.all()], ['Да', 'Нет', 'Не знаю'],
            'порядок вариантов должен совпадать с тем, что ввёл автор',
        )
        self.assertFalse(poll.is_multiple)

    def test_multiple_choice_flag_is_saved(self):
        self.create_post_with_poll(multiple=True)

        self.assertTrue(Poll.objects.get().is_multiple)

    def test_blank_options_do_not_count(self):
        """Пустые поля остаются в форме от «добавить вариант» — если их
        считать, опрос уедет с пустыми кнопками, за которые можно голосовать."""
        self.create_post_with_poll(options=('Да', '   ', 'Нет', ''))

        self.assertEqual(
            [option.text for option in Poll.objects.get().options.all()], ['Да', 'Нет']
        )

    def test_post_is_not_created_when_poll_is_broken(self):
        """Запись сохраняется только вместе с годным опросом: иначе она уже
        опубликована, а автору остаётся удалить её и написать заново."""
        response = self.create_post_with_poll(options=('Единственный',))

        self.assertEqual(Post.objects.count(), 0)
        self.assertEqual(Poll.objects.count(), 0)
        self.assertContains(response, 'не меньше 2 вариантов')

    def test_too_many_options_are_refused(self):
        response = self.create_post_with_poll(options=[f'Вариант {i}' for i in range(11)])

        self.assertEqual(Post.objects.count(), 0)
        self.assertContains(response, 'не больше 10 вариантов')

    def test_post_without_poll_still_works(self):
        self.client.post(reverse('addpost', args=[self.author.username]), {'content': 'Просто запись'})

        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(Poll.objects.count(), 0)


class SingleChoiceVotingTest(PollTestCase):

    def test_vote_is_counted(self):
        poll = self.poll()
        option = poll.options.first()

        response = self.vote(poll, option, user=self.voter)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PollVote.objects.count(), 1)
        self.assertContains(response, '100%')

    def test_second_option_replaces_the_first(self):
        """В опросе с одним вариантом ответа человек не может насчитать себе
        два голоса — иначе итог опроса ничего не значит."""
        poll = self.poll()
        first, second = list(poll.options.all())

        self.vote(poll, first, user=self.voter)
        self.vote(poll, second)

        self.assertEqual(PollVote.objects.count(), 1)
        self.assertEqual(PollVote.objects.get().option, second)

    def test_repeated_click_retracts_the_vote(self):
        """Передумать можно: без этого ошибочный клик остаётся навсегда."""
        poll = self.poll()
        option = poll.options.first()

        self.vote(poll, option, user=self.voter)
        self.vote(poll, option)

        self.assertEqual(PollVote.objects.count(), 0)

    def test_option_from_another_poll_is_refused(self):
        """option_id приходит из тела запроса, и без сверки с опросом им
        можно было бы подложить голос в чужой опрос."""
        poll = self.poll()
        other_option = self.poll(options=('А', 'Б')).options.first()

        response = self.vote(poll, other_option, user=self.voter)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(PollVote.objects.count(), 0)

    def test_anonymous_cannot_vote(self):
        poll = self.poll()
        self.client.logout()

        response = self.client.post(
            reverse('poll_vote', args=[poll.post_id]), {'option_id': poll.options.first().pk}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PollVote.objects.count(), 0)


class MultipleChoiceVotingTest(PollTestCase):

    def test_several_options_can_be_chosen(self):
        poll = self.poll(options=('А', 'Б', 'В'), multiple=True)
        first, second, _ = list(poll.options.all())

        self.vote(poll, first, user=self.voter)
        self.vote(poll, second)

        self.assertEqual(
            set(PollVote.objects.values_list('option__text', flat=True)), {'А', 'Б'}
        )

    def test_each_option_toggles_separately(self):
        poll = self.poll(options=('А', 'Б'), multiple=True)
        first, second = list(poll.options.all())

        self.vote(poll, first, user=self.voter)
        self.vote(poll, second)
        self.vote(poll, first)

        self.assertEqual(list(PollVote.objects.values_list('option__text', flat=True)), ['Б'])


class PollResultsTest(PollTestCase):

    def test_percentages_are_counted_per_option(self):
        poll = self.poll(options=('А', 'Б'))
        first, second = list(poll.options.all())
        PollVote.objects.create(option=first, user=self.author)
        PollVote.objects.create(option=first, user=self.voter)
        PollVote.objects.create(
            option=second, user=User.objects.create_user(username='third', password='x'),
        )

        results = Poll.objects.prefetch_related('options__votes').get(pk=poll.pk).results_for(self.voter)

        self.assertEqual(results['total'], 3)
        self.assertEqual([option['percent'] for option in results['options']], [67, 33])
        self.assertTrue(results['options'][0]['chosen'], 'свой голос должен быть отмечен')
        self.assertFalse(results['options'][1]['chosen'])

    def test_empty_poll_does_not_divide_by_zero(self):
        results = Poll.objects.prefetch_related('options__votes').get(
            pk=self.poll().pk
        ).results_for(self.voter)

        self.assertEqual(results['total'], 0)
        self.assertEqual([option['percent'] for option in results['options']], [0, 0])


class PollRenderingTest(PollTestCase):

    def test_feed_shows_poll_options(self):
        self.poll(options=('Да', 'Нет'))

        response = self.client.get(reverse('home'))

        self.assertContains(response, 'poll-option')
        self.assertContains(response, 'Ещё никто не голосовал')

    def test_post_page_shows_poll(self):
        poll = self.poll()

        response = self.client.get(reverse('post', args=[poll.post_id]))

        self.assertContains(response, 'poll-option')

    def test_feed_does_not_query_per_post(self):
        """Опросы собираются одним prefetch. Запрос на каждую карточку в
        ленте из десятков записей — ровно тот N+1, который здесь ловится
        глазами при чтении вьюхи и перестаёт быть заметен, стоит добавить в
        карточку ещё один блок.

        Считаются только запросы к таблицам опроса: общее число запросов
        страницы меняется и от посторонних вещей (уборка уведомлений,
        настройки сайта), и сравнение по нему давало бы ложные срабатывания.
        """
        def poll_queries(context):
            return [
                query for query in context.captured_queries
                if 'posts_poll' in query['sql']
            ]

        self.poll(options=('Да', 'Нет'))
        with CaptureQueriesContext(connection) as one_poll:
            self.client.get(reverse('home'))

        for _ in range(3):
            self.poll(options=('Да', 'Нет'))
        with CaptureQueriesContext(connection) as four_polls:
            self.client.get(reverse('home'))

        self.assertEqual(
            len(poll_queries(four_polls)), len(poll_queries(one_poll)),
            'число запросов к опросам выросло вместе с числом записей в ленте',
        )


class PollEditingTest(PollTestCase):

    def test_author_can_remove_the_poll(self):
        poll = self.poll()
        PollVote.objects.create(option=poll.options.first(), user=self.voter)

        self.client.post(
            reverse('settingpost', args=[poll.post_id]),
            {'content': 'Идём в отпуск?', 'remove_poll': '1'},
        )

        self.assertEqual(Poll.objects.count(), 0)
        self.assertEqual(PollVote.objects.count(), 0)
        self.assertEqual(Post.objects.count(), 1, 'сама запись остаётся')

    def test_editing_without_the_checkbox_keeps_the_poll(self):
        poll = self.poll()

        self.client.post(
            reverse('settingpost', args=[poll.post_id]), {'content': 'Новый текст'},
        )

        self.assertEqual(Poll.objects.count(), 1)

    def test_deleting_post_deletes_the_poll(self):
        poll = self.poll()
        PollVote.objects.create(option=poll.options.first(), user=self.voter)

        self.client.post(reverse('delete-post', args=[poll.post_id]))

        self.assertEqual(Poll.objects.count(), 0)
        self.assertEqual(PollOption.objects.count(), 0)
        self.assertEqual(PollVote.objects.count(), 0)


class OpenPollTest(PollTestCase):
    """Опрос, в котором видно, кто какой вариант выбрал.

    Анонимность остаётся умолчанием: голосовать «как все» проще, когда
    никто не смотрит, и ради этого опросы и заводят. Открытый режим — выбор
    автора, и главное здесь то, что этот выбор нельзя обойти по прямому
    адресу: имена лежат в БД в любом случае.
    """

    def open_poll(self, options=('Да', 'Нет')):
        return self.poll(options=options, show_voters=True)

    def test_flag_is_saved_from_the_form(self):
        self.client.post(
            reverse('addpost', args=[self.author.username]),
            {'content': 'Кто идёт?', 'poll_options': ['Да', 'Нет'], 'poll_show_voters': '1'},
        )

        self.assertTrue(Poll.objects.get().show_voters)

    def test_poll_is_anonymous_unless_asked(self):
        self.create_post_with_poll()

        self.assertFalse(Poll.objects.get().show_voters)

    def test_results_carry_voters_in_open_poll(self):
        poll = self.open_poll()
        option = poll.options.first()
        PollVote.objects.create(option=option, user=self.voter)

        results = Poll.objects.prefetch_related('options__votes__user').get(
            pk=poll.pk
        ).results_for(self.author)

        self.assertTrue(results['show_voters'])
        self.assertEqual(len(results['options'][0]['voters']), 1)
        self.assertIn('avatar', results['options'][0]['voters'][0])

    def test_anonymous_poll_hides_voters_from_results(self):
        poll = self.poll()
        PollVote.objects.create(option=poll.options.first(), user=self.voter)

        results = Poll.objects.prefetch_related('options__votes__user').get(
            pk=poll.pk
        ).results_for(self.author)

        self.assertFalse(results['show_voters'])
        self.assertEqual(results['options'][0]['voters'], [])

    def test_voters_list_is_served_for_an_open_poll(self):
        poll = self.open_poll()
        option = poll.options.first()
        PollVote.objects.create(option=option, user=self.voter)

        response = self.client.get(reverse('poll_voters', args=[option.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['users']), 1)
        self.assertEqual(response.json()['option'], option.text)

    def test_voters_list_is_closed_for_an_anonymous_poll(self):
        """Имена лежат в БД и у анонимного опроса. Не закрой этот адрес — и
        обещанная анонимность снимается одним переходом по ссылке."""
        poll = self.poll()
        option = poll.options.first()
        PollVote.objects.create(option=option, user=self.voter)

        response = self.client.get(reverse('poll_voters', args=[option.pk]))

        self.assertEqual(response.status_code, 404)

    def test_anonymous_visitor_cannot_read_voters(self):
        poll = self.open_poll()
        PollVote.objects.create(option=poll.options.first(), user=self.voter)
        self.client.logout()

        response = self.client.get(reverse('poll_voters', args=[poll.options.first().pk]))

        self.assertEqual(response.status_code, 302)

    def test_feed_shows_faces_only_in_an_open_poll(self):
        open_poll = self.open_poll()
        PollVote.objects.create(option=open_poll.options.first(), user=self.voter)

        response = self.client.get(reverse('home'))
        self.assertContains(response, 'poll-voters')
        self.assertContains(response, 'видно, кто как ответил')

        Poll.objects.filter(pk=open_poll.pk).update(show_voters=False)

        response = self.client.get(reverse('home'))
        self.assertNotContains(response, 'poll-voters')
        self.assertContains(response, 'анонимно')

    def test_open_poll_does_not_query_per_voter(self):
        """Лица собираются тем же prefetch, что и голоса: запрос на каждого
        проголосовавшего превратил бы ленту в сотню запросов."""
        def poll_queries(context):
            return [q for q in context.captured_queries if 'posts_poll' in q['sql']]

        poll = self.open_poll()
        PollVote.objects.create(option=poll.options.first(), user=self.voter)
        with CaptureQueriesContext(connection) as one_voter:
            self.client.get(reverse('home'))

        for index in range(3):
            user = User.objects.create_user(username=f'voter{index}', password='x')
            PollVote.objects.create(option=poll.options.first(), user=user)
        with CaptureQueriesContext(connection) as four_voters:
            self.client.get(reverse('home'))

        self.assertEqual(len(poll_queries(four_voters)), len(poll_queries(one_voter)))
