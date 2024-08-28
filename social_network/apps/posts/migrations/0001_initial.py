# Generated by Django 5.0.1 on 2024-02-15 11:48

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Post',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(blank=True, verbose_name='Текст поста')),
                ('photo', models.ImageField(blank=True, null=True, upload_to='photos/%Y/%m/%d/', verbose_name='Изображение')),
                ('time_create', models.DateTimeField(auto_now_add=True, verbose_name='Время создания')),
                ('time_update', models.DateTimeField(auto_now=True, verbose_name='Время изменения')),
            ],
            options={
                'verbose_name': 'Все новости организации',
                'verbose_name_plural': 'Все новости организации',
                'ordering': ['-time_create'],
            },
        ),
    ]