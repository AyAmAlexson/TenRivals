from django.conf import settings

def telegram_settings(request):
    return {
        'telegram_bot_name': settings.TELEGRAM_BOT_USERNAME or None,
        'telegram_bot_id': settings.TELEGRAM_BOT_ID or None,
    } 