from django.core.management.base import BaseCommand
from django.conf import settings
import telebot
import logging
from persons.models import TelegramVerification, CustomUser
from django.utils import timezone
import hashlib
import time
from django.db.models import Q

logger = logging.getLogger(__name__)

def generate_verification_code():
    """Генерирует случайный код верификации"""
    return hashlib.sha256(str(time.time()).encode()).hexdigest()[:6]

class Command(BaseCommand):
    help = 'Runs the Telegram bot for user registration and verification'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting Telegram bot...'))
        
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stdout.write(self.style.ERROR('Telegram bot token is not set in settings'))
            return
            
        try:
            bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN)
            
            @bot.message_handler(commands=['start'])
            def start_command(message):
                """
                Обработчик команды /start для приветствия и верификации
                """
                chat_id = message.chat.id
                username = message.from_user.username
                start_param = message.text.split()
                
                logger.info(f"Start command from user: {username}, chat_id: {chat_id}, params: {start_param}")
                
                # Обработка параметра верификации
                if len(start_param) > 1 and start_param[1].startswith('verify_'):
                    verification_code = start_param[1].replace('verify_', '')
                    
                    try:
                        # Ищем верификацию с таким кодом
                        verification = TelegramVerification.objects.get(verification_code=verification_code)
                        
                        # Обновляем данные верификации
                        verification.telegram_id = str(chat_id)
                        if username and verification.telegram_username != username:
                            verification.telegram_username = username
                        verification.save()
                        
                        # Формируем URL для автоматического возврата на сайт с кодом
                        verification_url = f"{settings.WEBSITE_URL}/verify-telegram/?code={verification_code}"
                        
                        # Создаем клавиатуру с кнопкой
                        markup = telebot.types.InlineKeyboardMarkup()
                        markup.add(telebot.types.InlineKeyboardButton(
                            text="Enter Code Automatically",
                            url=verification_url
                        ))
                        
                        # Отправляем сообщение с кодом и кнопкой
                        bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ *Your verification code:*\n\n`{verification_code}`\n\nEnter this code on the website or click the button below to verify automatically:",
                            parse_mode='Markdown',
                            reply_markup=markup
                        )
                        logger.info(f"Sent verification code via deep link to {username}")
                        
                    except TelegramVerification.DoesNotExist:
                        bot.send_message(
                            chat_id=chat_id,
                            text="This verification code is invalid or has expired. Please request a new code from the website."
                        )
                        logger.warning(f"Invalid verification code from deep link: {verification_code}")
                        return
                
                # Отправляем приветственное сообщение (в любом случае)
                welcome_message = (
                    "👋 *Welcome to Tennis Rivals Bot!*\n\n"
                    "I'm your assistant for tennis matches and tournaments. "
                    "Through me, you'll receive important notifications about:\n"
                    "• Match schedules and court information\n"
                    "• Opponent details and contact info\n"
                    "• Tournament updates and results\n"
                    "• Score confirmation requests\n\n"
                    "Type /help anytime to see available commands."
                )
                
                bot.send_message(
                    chat_id=chat_id,
                    text=welcome_message,
                    parse_mode='Markdown'
                )

            @bot.message_handler(commands=['verify'])
            def verify_command(message):
                """
                Обработчик команды /verify для получения кода верификации
                """
                chat_id = message.chat.id
                username = message.from_user.username
                
                logger.info(f"Verify command from user: {username}, chat_id: {chat_id}")
                
                # Ищем пользователя только по username, игнорируем telegram_id
                try:
                    verification = TelegramVerification.objects.get(telegram_username=username)
                    
                    # Обновляем telegram_id
                    verification.telegram_id = str(chat_id)
                    verification.save()
                    logger.info(f"Updated telegram_id for user: {verification.user.username}")
                    
                    # Отправляем код
                    bot.send_message(
                        chat_id=chat_id,
                        text=f"Your verification code is: *{verification.verification_code}*\n\nEnter this code on the verification page.",
                        parse_mode='Markdown'
                    )
                    logger.info(f"Sent verification code to {username}")
                    
                except TelegramVerification.DoesNotExist:
                    bot.send_message(
                        chat_id=chat_id,
                        text="I couldn't find your account. Make sure you're using the same Telegram account you registered with."
                    )
                    logger.warning(f"Verification not found for user: {username}")

            @bot.message_handler(commands=['help'])
            def help_command(message):
                bot.send_message(
                    message.chat.id,
                    "Доступные команды:\n"
                    "/start - Начать работу с ботом\n"
                    "/verify - Получить код верификации\n"
                    "/help - Показать эту справку\n\n"
                    "Этот бот используется для регистрации и верификации аккаунтов на нашем сайте."
                )

            @bot.message_handler(func=lambda message: True)
            def echo_all(message):
                username = message.from_user.username
                self.stdout.write(f"Received message from @{username}: {message.text}")
                
                bot.reply_to(
                    message, 
                    "Для получения кода верификации используйте команду /verify\n"
                    "Для начала работы с ботом используйте команду /start\n"
                    "Для получения справки используйте команду /help"
                )

            self.stdout.write(self.style.SUCCESS(f'Bot @{settings.TELEGRAM_BOT_USERNAME} is running...'))
            bot.polling(none_stop=True)
            
        except Exception as e:
            logger.exception(f"Bot error: {e}")
            self.stdout.write(self.style.ERROR(f'Bot error: {e}')) 