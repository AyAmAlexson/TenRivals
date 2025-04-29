from telebot import TeleBot
from django.conf import settings
from .models import TelegramVerification

bot = TeleBot(settings.TELEGRAM_BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start_command(message):
    chat_id = message.chat.id
    username = message.from_user.username
    
    bot.reply_to(message, f"Привет, @{username}! Ваш Telegram ID: {chat_id}. Используйте его для верификации на сайте.")
    
    # Попытка найти и обновить запись верификации
    try:
        verification = TelegramVerification.objects.filter(telegram_username=username).first()
        if verification:
            verification.telegram_id = str(chat_id)
            verification.save()
            bot.send_message(chat_id, f"Ваш код верификации: {verification.verification_code}")
    except Exception as e:
        print(f"Error updating verification: {e}")

@bot.message_handler(commands=['verify'])
def verify_command(message):
    chat_id = message.chat.id
    username = message.from_user.username
    
    try:
        verification = TelegramVerification.objects.filter(telegram_username=username).first()
        if verification:
            bot.send_message(chat_id, f"Ваш код верификации: {verification.verification_code}")
        else:
            bot.reply_to(message, "Аккаунт не найден. Пожалуйста, зарегистрируйтесь на сайте.")
    except Exception as e:
        bot.reply_to(message, f"Произошла ошибка: {e}") 