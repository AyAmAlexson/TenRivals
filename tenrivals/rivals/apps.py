from django.apps import AppConfig


class RivalsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rivals'

    def ready(self):
        import rivals.tasks  # Важно для регистрации задач
        import rivals.signals # Импортируем сигналы при готовности приложения
