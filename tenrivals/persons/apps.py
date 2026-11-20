from django.apps import AppConfig


class PersonsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'persons'

    def ready(self):
        from persons.social_login_guard import install

        install()
