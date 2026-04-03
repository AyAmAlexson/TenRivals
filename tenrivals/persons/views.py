from allauth.account.views import LoginView, SignupView
from .forms import CustomLoginForm, CustomSignupForm
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.urls import reverse, reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin,PermissionRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View, FormView
from .models import CustomUser, TelegramVerification
from .forms import TelegramVerificationForm, ChangeEmailForm, AccountUpdateForm
from rivals.models import Player
from django.shortcuts import render, redirect
import telebot
from django.conf import settings
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth import logout, login
import hashlib
import hmac
from django.utils.encoding import force_str
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
from django.shortcuts import get_object_or_404
import time
from .services import generate_verification_code_service
from django.db.models import Q
import qrcode
import base64
from io import BytesIO

logger = logging.getLogger(__name__)

def get_telegram_bot_instance():
    """Возвращает инициализированный экземпляр бота или None."""
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME and settings.TELEGRAM_BOT_ID:
        try:
            # Создаем экземпляр ТОЛЬКО при вызове функции
            bot_instance = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN)
            # Логгирование можно оставить здесь или убрать, если не нужно при каждом получении
            # logger.info(f"Telegram bot instance requested and created.")
            return bot_instance
        except Exception as e:
            logger.error(f"Failed to create Telegram bot instance: {e}")
            return None
    else:
        logger.warning("Telegram bot configuration is missing, cannot create instance.")
        return None

class CustomLoginView(LoginView):
    form_class = CustomLoginForm
    template_name = 'account/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['signup_url'] = reverse_lazy('account_signup')
        return context

class CustomSignupView(SignupView):
    form_class = CustomSignupForm
    template_name = 'account/signup.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['login_url'] = reverse_lazy('account_login')
        return context

class AccountDetailView(LoginRequiredMixin, UpdateView):
    model = CustomUser
    form_class = AccountUpdateForm
    template_name = 'account_details.html'
    success_url = reverse_lazy('persons:account_details')

    def get_object(self):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, 'Account updated successfully.')
        return super().form_valid(form)

def generate_verification_code():
    # Генерация случайного кода верификации
    return generate_verification_code_service()

@login_required
def verify_telegram(request):
    # Добавляем логирование
    logger.info(f"Starting Telegram verification for user: {request.user.username}")
    
    verification, created = TelegramVerification.objects.get_or_create(
        user=request.user,
        defaults={'verification_code': generate_verification_code()}
    )
    
    logger.info(f"Verification object: {verification}, Created: {created}")
    print(f"DEBUG!!! TG_VC: {verification.verification_code}")
    
    # Проверяем, соответствует ли telegram в верификации текущему telegram пользователя
    if verification.telegram_username != request.user.telegram:
        verification.telegram_username = request.user.telegram
        verification.telegram_id = ''  # Сбрасываем ID, так как это новый аккаунт
        verification.verification_code = generate_verification_code()
        verification.is_verified = False
        verification.save()
        logger.info(f"Updated verification telegram_username to match user.telegram: {request.user.telegram}")
    
    if request.method == 'POST':
        form = TelegramVerificationForm(request.POST)
        if form.is_valid():
            entered_code = form.cleaned_data['verification_code']
            logger.info(f"User entered code: {entered_code}, Expected: {verification.verification_code}")
            
            if entered_code == verification.verification_code:
                verification.is_verified = True
                verification.verified_at = timezone.now()
                verification.save()
                
                # Также обновляем статус верификации в модели пользователя
                request.user.is_telegram_verified = True
                request.user.save()

                # Также обновляем основной username
                request.user.username = verification.telegram_username
                request.user.save()
                
                messages.success(request, 'Telegram account verified successfully!')
                logger.info(f"Verification successful for user: {request.user.username}")
                return redirect('rivals:player_update')
            else:
                messages.error(request, 'Invalid verification code.')
                logger.warning(f"Invalid verification code for user: {request.user.username}")
    else:
        form = TelegramVerificationForm()
    
    # Создаем глубокую ссылку с кодом
    bot_username = settings.TELEGRAM_BOT_USERNAME
    start_param = f"verify_{verification.verification_code}"
    deep_link = f"https://t.me/{bot_username}?start={start_param}"
    
    # Генерируем QR-код
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(deep_link)
    qr.make(fit=True)
    
    # Создаем изображение QR-кода
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Конвертируем изображение в base64 для отображения в HTML
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
    
    # Логика отправки кода остается прежней, но скрываем уведомления об ошибках,
    # так как у пользователя есть альтернативный метод
    send_code_success = False
    bot = get_telegram_bot_instance()
    if bot:
        try:
            telegram_username = verification.telegram_username
            if telegram_username.startswith('@'):
                telegram_username = telegram_username[1:]
                
            try:
                username_chat = bot.get_chat(f"@{telegram_username}")
                if username_chat and username_chat.id:
                    message_text = (
                        f'Your verification code for Tennis Rivals is: *{verification.verification_code}*\n\n'
                        f'Enter this code on the website to verify your Telegram account.'
                    )
                    
                    bot.send_message(
                        chat_id=username_chat.id,
                        text=message_text,
                        parse_mode='Markdown'
                    )
                    
                    verification.telegram_id = str(username_chat.id)
                    verification.save()
                    
                    send_code_success = True
                    logger.info(f"Verification code sent to username: @{telegram_username}")
            except Exception as e:
                logger.error(f"Error sending message to username @{telegram_username}: {e}")
        except Exception as e:
            logger.error(f"General error in code sending: {e}")
    
    return render(request, 'persons/verify_telegram.html', {
        'form': form,
        'telegram_username': verification.telegram_username,
        'telegram_bot_name': settings.TELEGRAM_BOT_USERNAME,
        'deep_link': deep_link,
        'qr_code_base64': qr_code_base64,
        'send_code_success': send_code_success,
        'verification_code': verification.verification_code  # Для демонстрационных целей (убрать в продакшне)
    })

@csrf_exempt
@require_http_methods(["POST", "GET"])
def telegram_callback(request):
    logger.info("=== Telegram callback start ===")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request path: {request.path}")
    logger.info(f"Request headers: {dict(request.headers)}")
    
    if request.method == "GET":
        # Рендерим HTML-страницу с JavaScript для обработки данных Telegram
        return render(request, "persons/telegram_callback.html")
    
    elif request.method == "POST":
        try:
            # Получаем тело запроса
            body = request.body.decode()
            
            if not body:
                logger.error("Empty request body")
                return JsonResponse({'success': False, 'error': 'Empty request body'})
            
            # Парсим JSON
            data = json.loads(body)
            logger.info(f"Parsed data: {data}")
            
            # Проверка обязательных полей
            required_fields = ['id', 'first_name', 'username', 'auth_date', 'hash']
            missing = [f for f in required_fields if f not in data]
            
            if missing:
                logger.error(f"Missing required fields: {missing}")
                return JsonResponse({'success': False, 'error': f'Missing fields: {", ".join(missing)}'})
            
            # Проверка хеша для безопасности
            # В продакшене раскомментируйте этот блок
            """
            secret_key = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.encode()).digest()
            data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted(
                [(k, v) for k, v in data.items() if k != 'hash'],
                key=lambda x: x[0]
            )])
            data_check_hash = hmac.new(
                secret_key,
                data_check_string.encode(),
                hashlib.sha256
            ).hexdigest()
            
            if data_check_hash != data['hash']:
                logger.error("Invalid hash")
                return JsonResponse({'success': False, 'error': 'Invalid hash'})
            
            # Проверяем время аутентификации (не старше 24 часов)
            auth_date = int(data['auth_date'])
            now = int(time.time())
            if now - auth_date > 86400:
                logger.error("Authentication data is outdated")
                return JsonResponse({'success': False, 'error': 'Authentication data is outdated'})
            """
            
            # Обработка данных Telegram
            telegram_id = str(data['id'])
            username = data.get('username', '')
            first_name = data.get('first_name', '')
            last_name = data.get('last_name', '')
            
            logger.info(f"Processing Telegram user: {username} (ID: {telegram_id})")
            
            # Ищем верификацию по telegram_id или username
            try:
                telegram_verification = TelegramVerification.objects.get(
                    Q(telegram_id=telegram_id) | Q(telegram_username=username)
                )
                user = telegram_verification.user
                logger.info(f"Found existing verification for user: {user.username}")
                
                # Обновляем данные верификации, если нужно
                if not telegram_verification.telegram_id or telegram_verification.telegram_id != telegram_id:
                    telegram_verification.telegram_id = telegram_id
                    telegram_verification.save()
                    logger.info(f"Updated telegram_id for user: {user.username}")
                
                # Если верификация не была подтверждена, подтверждаем её
                if not telegram_verification.is_verified:
                    telegram_verification.is_verified = True
                    telegram_verification.verified_at = timezone.now()
                    telegram_verification.save()
                    
                    # Также обновляем статус верификации в модели пользователя
                    user.is_telegram_verified = True
                    user.save()
                    
                    logger.info(f"Automatically verified Telegram for user: {user.username}")
            
            except TelegramVerification.DoesNotExist:
                telegram_verification = None
            
            if telegram_verification is None:
                # Создаем нового пользователя
                display_name = f"{first_name} {last_name}".strip() or username
                email = f"tg_{telegram_id}@telegram.user"
                
                try:
                    user = CustomUser.objects.get(email=email)
                    logger.info(f"Found existing user by email: {email}")
                except CustomUser.DoesNotExist:
                    logger.info(f"Creating new user with email: {email}")
                    user = CustomUser.objects.create_user(
                        username=username or f"tg_{telegram_id}",
                        email=email,
                        password=None  # Пароль не нужен
                    )
                    user.first_name = first_name
                    user.last_name = last_name
                    user.set_unusable_password()  # Делаем пароль неиспользуемым
                    user.save()
                    logger.info(f"User created: {user.username} (ID: {user.id})")
                
                # Создаем верификацию Telegram с ID
                telegram_verification = TelegramVerification.objects.create(
                    user=user,
                    telegram_id=telegram_id,
                    telegram_username=username,
                    is_verified=True,  # Автоматически верифицируем через Login Widget
                    verified_at=timezone.now()
                )
                logger.info(f"Created verification for user: {user.username}")
                
                # Создаем профиль игрока, если нужно
                try:
                    Player.objects.get(user=user)
                except Player.DoesNotExist:
                    Player.objects.create(user=user)
                    logger.info(f"Created player profile for user: {user.username}")
            
            # Аутентифицируем пользователя
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            logger.info(f"User authenticated: {user.username}")
            
            # Возвращаем успешный ответ
            return JsonResponse({
                'success': True,
                'redirect_url': '/'
            })
        
        except json.JSONDecodeError:
            logger.exception("Invalid JSON")
            return JsonResponse({'success': False, 'error': 'Invalid JSON'})
        
        except Exception as e:
            logger.exception("Error in telegram_callback")
            return JsonResponse({'success': False, 'error': str(e)})

@login_required
def change_telegram(request):
    logger.info(f"Change Telegram request for user: {request.user.username}")
    
    if request.method == 'POST':
        new_telegram = request.POST.get('telegram_username')
        
        # Убираем @ в начале, если он есть
        if new_telegram.startswith('@'):
            new_telegram = new_telegram[1:]
        
        logger.info(f"User {request.user.username} is changing Telegram to: {new_telegram}")
        
        try:
            # Проверка, что такой Telegram не занят другим пользователем
            if TelegramVerification.objects.filter(telegram_username=new_telegram).exclude(user=request.user).exists():
                messages.error(request, f"Telegram username '{new_telegram}' is already registered to another account.")
                return render(request, 'persons/change_telegram.html')
                
            # Получаем или создаем запись верификации
            verification, created = TelegramVerification.objects.get_or_create(
                user=request.user,
                defaults={
                    'telegram_username': new_telegram,
                    'verification_code': generate_verification_code(),
                    'is_verified': False,
                    'telegram_id': ''  # Сбрасываем telegram_id, так как это новый аккаунт
                }
            )
            
            if not created:
                # Если запись существует, обновляем данные
                verification.telegram_username = new_telegram
                verification.verification_code = generate_verification_code()
                verification.is_verified = False
                verification.telegram_id = ''  # Очень важно! Сбрасываем telegram_id
                verification.save()
            
            # Также обновляем поле telegram в модели пользователя
            request.user.telegram = new_telegram
            request.user.is_telegram_verified = False
            request.user.save()

           
            
            logger.info(f"Updated Telegram username for user {request.user.username} to {new_telegram}")
            
            # Редирект на страницу верификации
            return redirect('persons:verify_telegram')
            
        except Exception as e:
            logger.error(f"Error changing Telegram for user {request.user.username}: {e}")
            messages.error(request, f"An error occurred: {e}")
            
    return render(request, 'persons/change_telegram.html')

@login_required
def delete_profile(request):
    if request.method == 'POST':
        if request.POST.get('confirm_delete') == 'yes':
            user = request.user
            logout(request)
            user.delete()
            messages.success(request, 'Your profile has been deleted.')
            return redirect('/')
    return render(request, 'persons/delete_profile.html')

@login_required
def change_email(request):
    if request.method == 'POST':
        form = ChangeEmailForm(request.user, request, request.POST)
        if form.is_valid():
            try:
                form.save()
            except Exception:
                logger.exception(
                    "change_email: failed to send confirmation for user_id=%s",
                    request.user.pk,
                )
                messages.error(
                    request,
                    "We could not send the confirmation email. Please try again in a few "
                    "minutes and check your SMTP settings. If the problem continues, contact support.",
                )
                return render(request, 'persons/change_email.html', {'form': form})
            messages.success(
                request,
                'We sent a confirmation link to your new email address. Please check your inbox.',
            )
            return redirect('persons:account_details')
    else:
        form = ChangeEmailForm(request.user, request)

    return render(request, 'persons/change_email.html', {'form': form})

class ConfirmEmailChangeView(View):
    def get(self, request, uidb64, token):
        try:
            # Декодируем uid из base64
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = get_user_model().objects.get(pk=uid)
            
            # Проверяем токен
            if default_token_generator.check_token(user, token):
                # Подтверждаем email
                user.is_email_verified = True
                user.save()
                
                logger.info("Email verified successfully for user: %s", user)
                messages.success(request, "Your email has been verified successfully!")
                
                # Если пользователь авторизован, перенаправляем на страницу профиля
                if request.user.is_authenticated:
                    return redirect('rivals:player_update')
                # Иначе на страницу входа
                return redirect('account_login')
            else:
                logger.warning("Invalid email confirmation token for user: %s", uid)
                messages.error(request, "The confirmation link is invalid or has expired.")
                
        except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist) as e:
            logger.error("Error confirming email: %s", e)
            messages.error(request, "The confirmation link is invalid.")
        
        # В случае ошибки показываем страницу с сообщением об ошибке
        return render(request, 'email/confirmation_failed.html')

@login_required
def resend_verification_code(request):
    """
    Функция для повторной отправки кода верификации
    """
    logger.info(f"Resending verification code for user: {request.user.username}")
    
    try:
        verification = TelegramVerification.objects.get(user=request.user)
        
        # Генерируем новый код
        verification.verification_code = generate_verification_code()
        verification.save()
        
        # Отправляем код через бота
        sent_successfully = False
        error_message = ""
        
        bot = get_telegram_bot_instance()
        if bot:
            telegram_id = verification.telegram_id
            telegram_username = verification.telegram_username
            
            message_text = (
                f'Your new verification code for Tennis Rivals is: *{verification.verification_code}*\n\n'
                f'Enter this code on the website to verify your Telegram account.'
            )
            
            # Пытаемся отправить по ID
            if telegram_id:
                try:
                    bot.send_message(
                        chat_id=telegram_id,
                        text=message_text,
                        parse_mode='Markdown'
                    )
                    sent_successfully = True
                    logger.info(f"New verification code sent to telegram_id: {telegram_id}")
                except Exception as e:
                    error_message = str(e)
                    logger.error(f"Error sending message to telegram_id: {e}")
            
            # Если у нас нет ID или была ошибка, пробуем отправить по username
            if not sent_successfully and telegram_username:
                # Убираем @ если есть
                if telegram_username.startswith('@'):
                    telegram_username = telegram_username[1:]
                
                try:
                    # Пытаемся получить chat_id по username
                    username_chat = bot.get_chat(f"@{telegram_username}")
                    if username_chat and username_chat.id:
                        bot.send_message(
                            chat_id=username_chat.id,
                            text=message_text,
                            parse_mode='Markdown'
                        )
                        
                        # Сохраняем полученный chat_id для будущего использования
                        verification.telegram_id = str(username_chat.id)
                        verification.save()
                        
                        sent_successfully = True
                        logger.info(f"New verification code sent to username: @{telegram_username}")
                except Exception as username_error:
                    error_message = str(username_error)
                    logger.error(f"Error sending message to username @{telegram_username}: {username_error}")
        
        if sent_successfully:
            messages.success(request, 'New verification code has been sent to your Telegram account.')
        else:
            if error_message:
                messages.warning(request, f'Unable to send verification code: {error_message}. Please use the QR code method instead.')
            else:
                messages.warning(request, 'Unable to send verification code. Please use the QR code method.')
    
    except TelegramVerification.DoesNotExist:
        logger.error(f"No verification record found for user: {request.user.username}")
        messages.error(request, 'Verification record not found. Please contact support.')
    
    return redirect('persons:verify_telegram')