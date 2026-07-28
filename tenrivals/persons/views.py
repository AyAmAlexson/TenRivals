from allauth.account.views import ConfirmEmailView as AllauthConfirmEmailView, LoginView, SignupView
from .forms import CustomLoginForm, CustomSignupForm
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from django.contrib.auth.decorators import login_required, user_passes_test
from django.urls import reverse, reverse_lazy
from django.contrib.auth.mixins import LoginRequiredMixin,PermissionRequiredMixin, UserPassesTestMixin
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View, FormView
from .models import CustomUser, TelegramVerification
from .forms import (
    AccountUpdateForm,
    ChangeEmailForm,
    SuperuserCreateUserForm,
    SuperuserUserEditForm,
    TelegramVerificationForm,
)
from allauth.account.models import EmailAddress
from allauth.account.utils import send_email_confirmation
from django.db import transaction
from rivals.models import Player
from django.shortcuts import render, redirect
import telebot
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth import logout, login
from django.utils.text import slugify
from django.core.mail import send_mail

from shop.models import (
    BlogPost,
    Customer,
    HomeHeroContent,
    HomeHeroSlide,
    HomePromoBanner,
    OrderForMe,
    OrderForMeItem,
    Product,
    ProductCollection,
    ProductCollectionGroup,
    ProductListing,
    ProductListingChannel,
    SalesOrder,
    StockReceipt,
)
from shop.staff_stock_stats import build_stock_stats
from shop.stock_receipts import receive_stock_lines
from shop.sales_order_utils import allocate_order_for_me_number
import hashlib
import hmac
from django.utils.encoding import force_str
import logging
from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
import json
from urllib.parse import urlencode

from django.shortcuts import get_object_or_404
import time
from .services import generate_verification_code_service
from django.db.models import Max, Q
import qrcode
import base64
from io import BytesIO
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)
_ORDER_FOR_ME_EMAILS = [
    'a.molodenko@gmail.com',
    'mr.alexson.assistant@gmail.com',
    'andy.rivals@tenrivals.com',
]


def _send_order_for_me_cancelled_email(request, order: OrderForMe):
    website = (getattr(settings, 'WEBSITE_URL', '') or '').rstrip('/')
    staff_path = reverse('administration:staff_order_for_me_list')
    staff_url = f'{website}{staff_path}' if website else request.build_absolute_uri(staff_path)
    send_mail(
        subject=f'Order For Me {order.order_number} CANCELLED',
        message=(
            f'Order: {order.order_number}\n'
            f'Status: Cancelled\n'
            f'Customer: {order.first_name} {order.last_name}\n'
            f'Email: {order.email}\n'
            f'Phone: {order.phone}\n'
            f'Staff list: {staff_url}\n'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None) or 'no-reply@tenrivals.com',
        recipient_list=_ORDER_FOR_ME_EMAILS,
        fail_silently=True,
    )

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

@method_decorator(
    ratelimit(key='ip', rate='30/m', method='POST', block=True),
    name='dispatch',
)
class CustomLoginView(LoginView):
    form_class = CustomLoginForm
    template_name = 'account/login.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['signup_url'] = reverse_lazy('account_signup')
        return context

@method_decorator(
    ratelimit(key='ip', rate='15/m', method='POST', block=True),
    name='dispatch',
)
class CustomSignupView(SignupView):
    form_class = CustomSignupForm
    template_name = 'account/signup.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['login_url'] = reverse_lazy('account_login')
        return context


class ConfirmEmailWithGtmView(AllauthConfirmEmailView):
    """
    With ACCOUNT_CONFIRM_EMAIL_ON_GET, allauth confirms on GET and returns a redirect,
    so GTM never loads. Return a short HTML page (with GTM) that JS-redirects instead.
    """

    def get(self, *args, **kwargs):
        response = super().get(*args, **kwargs)
        if getattr(settings, 'ACCOUNT_CONFIRM_EMAIL_ON_GET', False) and isinstance(
            response, (HttpResponseRedirect, HttpResponsePermanentRedirect)
        ):
            redirect_url = response.url
            if redirect_url:
                return render(
                    self.request,
                    'account/email_confirmation_redirecting.html',
                    {'redirect_url': redirect_url},
                    status=200,
                )
        return response


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


class ShopOrderHistoryView(LoginRequiredMixin, ListView):
    model = SalesOrder
    template_name = 'account_order_history.html'
    context_object_name = 'orders'

    def get_queryset(self):
        return (
            SalesOrder.objects.filter(customer__user=self.request.user)
            .select_related('customer')
            .prefetch_related('lines', 'lines__product')
            .order_by('-order_date', '-id')
        )


class AccountOrderForMeHistoryView(LoginRequiredMixin, ListView):
    model = OrderForMe
    template_name = 'account_order_for_me_history.html'
    context_object_name = 'orders'

    def get_queryset(self):
        return (
            OrderForMe.objects.filter(customer__user=self.request.user)
            .prefetch_related('items')
            .order_by('-created_at', '-id')
        )


@login_required
@require_POST
def account_order_for_me_cancel(request, order_id: int):
    order = get_object_or_404(OrderForMe, pk=order_id, customer__user=request.user)
    if order.status in {OrderForMe.Status.CANCELLED, OrderForMe.Status.ORDERED}:
        messages.info(request, 'This request can no longer be cancelled.')
        return redirect('persons:order_for_me_history')

    order.status = OrderForMe.Status.CANCELLED
    order.save(update_fields=['status', 'status_changed_at', 'updated_at'])
    _send_order_for_me_cancelled_email(request, order)
    messages.success(request, f'Request {order.order_number} was cancelled.')
    return redirect('persons:order_for_me_history')

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


def _superuser_required(user):
    return user.is_authenticated and user.is_superuser


def _sync_primary_email_address(user):
    """Keep allauth primary EmailAddress in sync with CustomUser.email."""
    email_lower = user.email.strip().lower()
    primary = EmailAddress.objects.filter(user=user, primary=True).first()
    if primary:
        if primary.email.lower() != email_lower:
            primary.email = email_lower
            primary.save(update_fields=["email"])
    else:
        EmailAddress.objects.update_or_create(
            user=user,
            email=email_lower,
            defaults={"primary": True, "verified": True},
        )


def _parse_order_for_me_items(request, max_items: int = 6):
    items = []
    for idx in range(1, max_items + 1):
        item_url = (request.POST.get(f'item_url_{idx}') or '').strip()
        item_comment = (request.POST.get(f'item_comment_{idx}') or '').strip()
        if not item_url:
            continue
        items.append(
            {
                'sort_order': idx,
                'item_url': item_url,
                'item_comment': item_comment,
            }
        )
    return items


@login_required
@user_passes_test(_superuser_required)
def staff_order_for_me_list(request):
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        oid_raw = (request.POST.get('order_id') or '').strip()
        order = OrderForMe.objects.filter(pk=int(oid_raw)).first() if oid_raw.isdigit() else None
        if not order:
            messages.error(request, 'Order for me record not found.')
            return redirect('administration:staff_order_for_me_list')
        if action == 'cancel':
            if order.status == OrderForMe.Status.CANCELLED:
                messages.info(request, f'{order.order_number} is already cancelled.')
            else:
                order.status = OrderForMe.Status.CANCELLED
                order.save(update_fields=['status', 'status_changed_at', 'updated_at'])
                _send_order_for_me_cancelled_email(request, order)
                messages.success(request, f'{order.order_number} marked as Cancelled.')
            return redirect('administration:staff_order_for_me_list')
        messages.error(request, 'Unknown action.')
        return redirect('administration:staff_order_for_me_list')

    orders = (
        OrderForMe.objects.select_related('customer')
        .prefetch_related('items')
        .order_by('-created_at', '-id')[:500]
    )
    return render(
        request,
        'persons/staff_order_for_me_list.html',
        {
            'orders': orders,
            'staff_nav_active': 'order_for_me',
            'page_heading': 'Order for me',
            'page_note': 'Custom sourcing requests from users. Cancel action is soft (status only).',
            'status_choices': OrderForMe.Status.choices,
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_order_for_me_edit(request, order_id=None):
    order = get_object_or_404(OrderForMe, pk=order_id) if order_id else None
    order_view = order
    max_items = 6
    if request.method == 'POST':
        first_name = (request.POST.get('first_name') or '').strip()[:120]
        last_name = (request.POST.get('last_name') or '').strip()[:120]
        email = (request.POST.get('email') or '').strip()
        phone = (request.POST.get('phone') or '').strip()[:32]
        telegram = (request.POST.get('telegram') or '').strip()[:64]
        contact_method = (request.POST.get('contact_method') or 'EMAIL').strip().upper()
        status = (request.POST.get('status') or OrderForMe.Status.SUBMITTED).strip().upper()
        general_comment = (request.POST.get('general_comment') or '').strip()
        estimated_total_raw = (request.POST.get('estimated_total') or '').strip()
        items = _parse_order_for_me_items(request, max_items=max_items)

        errors = []
        if not first_name or not last_name:
            errors.append('First and last name are required.')
        if not email or '@' not in email:
            errors.append('Valid email is required.')
        if not phone:
            errors.append('Phone is required.')
        if contact_method not in {OrderForMe.ContactMethod.EMAIL, OrderForMe.ContactMethod.WHATSAPP, OrderForMe.ContactMethod.TELEGRAM}:
            errors.append('Invalid contact method.')
        valid_statuses = {choice[0] for choice in OrderForMe.Status.choices}
        if status not in valid_statuses:
            errors.append('Invalid status.')
        if not items:
            errors.append('Add at least one item URL.')
        if len(items) > max_items:
            errors.append(f'At most {max_items} items are allowed.')
        for item in items:
            if not (item['item_url'].startswith('http://') or item['item_url'].startswith('https://')):
                errors.append(f'Invalid URL: {item["item_url"]}')
                break
            if len(item['item_url']) > 1000:
                errors.append('One of item links is too long (max 1000 chars).')
                break

        estimated_total = None
        if estimated_total_raw:
            try:
                estimated_total = Decimal(estimated_total_raw)
                if estimated_total < 0:
                    raise InvalidOperation()
                if estimated_total > Decimal('99999999.99'):
                    raise InvalidOperation()
                estimated_total = estimated_total.quantize(Decimal('0.01'))
            except Exception:
                errors.append('Estimated total must be a non-negative number.')

        if errors:
            for err in errors:
                messages.error(request, err)
            order_view = {
                'order_number': order.order_number if order else '',
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'phone': phone,
                'telegram': telegram,
                'contact_method': contact_method,
                'status': status,
                'estimated_total': estimated_total_raw,
                'general_comment': general_comment,
            }
        else:
            old_status = order.status if order else None
            try:
                with transaction.atomic():
                    customer = Customer.objects.filter(email__iexact=email).first()
                    if customer:
                        customer.first_name = first_name
                        customer.last_name = last_name
                        customer.phone = phone
                        customer.email = email
                        customer.tg_account = telegram
                        customer.save(
                            update_fields=['first_name', 'last_name', 'phone', 'email', 'tg_account', 'updated_at']
                        )
                    if order is None:
                        order = OrderForMe(
                            order_number=allocate_order_for_me_number(timezone.localdate().year),
                            customer=customer,
                            first_name=first_name,
                            last_name=last_name,
                            email=email,
                            phone=phone,
                            telegram=telegram,
                            contact_method=contact_method,
                            status=status,
                            estimated_total=estimated_total,
                            general_comment=general_comment,
                        )
                        order.full_clean()
                        order.save()
                    else:
                        order.customer = customer
                        order.first_name = first_name
                        order.last_name = last_name
                        order.email = email
                        order.phone = phone
                        order.telegram = telegram
                        order.contact_method = contact_method
                        order.status = status
                        order.estimated_total = estimated_total
                        order.general_comment = general_comment
                        order.full_clean()
                        order.save()
                        order.items.all().delete()
                    item_models = []
                    for item in items:
                        item_model = OrderForMeItem(
                            order=order,
                            sort_order=item['sort_order'],
                            item_url=item['item_url'],
                            item_comment=item['item_comment'],
                        )
                        item_model.full_clean()
                        item_models.append(item_model)
                    OrderForMeItem.objects.bulk_create(item_models)
                if old_status != OrderForMe.Status.CANCELLED and order.status == OrderForMe.Status.CANCELLED:
                    _send_order_for_me_cancelled_email(request, order)
                messages.success(request, f'{order.order_number} saved.')
                return redirect('administration:staff_order_for_me_edit', order_id=order.pk)
            except Exception as exc:
                logger.exception('staff_order_for_me_edit save failed: %s', exc)
                messages.error(
                    request,
                    f'Could not save this request: {exc.__class__.__name__} ({exc}). Please retry.',
                )
                order_view = {
                    'order_number': order.order_number if order else '',
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': email,
                    'phone': phone,
                    'telegram': telegram,
                    'contact_method': contact_method,
                    'status': status,
                    'estimated_total': estimated_total_raw,
                    'general_comment': general_comment,
                }

    if request.method == 'POST':
        # After validation/save error, keep the user-entered rows instead of reloading stale DB rows.
        item_rows = _parse_order_for_me_items(request, max_items=max_items)
    elif order:
        item_rows = list(order.items.order_by('sort_order', 'id').values('item_url', 'item_comment')[:max_items])
    else:
        item_rows = []
    if not item_rows:
        item_rows = [{'item_url': '', 'item_comment': ''}]

    return render(
        request,
        'persons/staff_order_for_me_form.html',
        {
            'order_obj': order,
            'order_view': order_view or order,
            'item_rows': item_rows,
            'max_items': max_items,
            'staff_nav_active': 'order_for_me',
            'page_heading': 'Edit Order for me' if order else 'New Order for me',
            'page_note': 'Staff can adjust status, estimate, and line items.',
            'status_choices': OrderForMe.Status.choices,
            'contact_method_choices': OrderForMe.ContactMethod.choices,
        },
    )


@login_required
@user_passes_test(_superuser_required)
def redirect_legacy_superuser_users(request):
    return redirect("administration:staff_users")


@login_required
@user_passes_test(_superuser_required)
def redirect_legacy_superuser_send_verification(request, user_id):
    return redirect("administration:superuser_send_email_verification", user_id=user_id)


@login_required
@user_passes_test(_superuser_required)
def redirect_legacy_superuser_user_edit(request, user_id):
    return redirect("administration:superuser_user_edit", user_id=user_id)


@login_required
@user_passes_test(_superuser_required)
@require_POST
def superuser_send_email_verification(request, user_id):
    target = get_object_or_404(CustomUser, pk=user_id)
    if target.is_primary_email_verified:
        messages.info(request, f"{target.email} is already verified.")
        return redirect("administration:staff_users")
    try:
        send_email_confirmation(request, target, signup=False)
        messages.success(
            request,
            f"Verification email sent to {target.email}.",
        )
    except Exception as e:
        logger.exception("superuser_send_email_verification: %s", e)
        messages.error(
            request,
            "Could not send verification email. Check SMTP or logs.",
        )
    return redirect("administration:staff_users")


@login_required
@user_passes_test(_superuser_required)
def superuser_user_edit(request, user_id):
    target = get_object_or_404(CustomUser, pk=user_id)
    if request.method == "POST":
        old_email = target.email.strip().lower()
        old_telegram = target.telegram or ""
        form = SuperuserUserEditForm(
            request.POST,
            instance=target,
            editor=request.user,
        )
        if form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    _sync_primary_email_address(user)
                    if user.email.strip().lower() != old_email:
                        EmailAddress.objects.filter(
                            user=user, primary=True
                        ).update(verified=False)
                    new_tg = user.telegram or ""
                    if new_tg != old_telegram:
                        user.is_telegram_verified = False
                        user.save(update_fields=["is_telegram_verified"])
                        TelegramVerification.objects.update_or_create(
                            user=user,
                            defaults={
                                "is_verified": False,
                                "telegram_id": "",
                                "verification_code": generate_verification_code(),
                                "telegram_username": new_tg,
                                "verified_at": None,
                            },
                        )
            except Exception as e:
                logger.exception("superuser_user_edit failed: %s", e)
                messages.error(
                    request,
                    "Could not save user. Check for duplicate email/username or see logs.",
                )
            else:
                messages.success(request, f"User {user.email} (ID {user.pk}) updated.")
                return redirect("administration:staff_users")
    else:
        form = SuperuserUserEditForm(instance=target, editor=request.user)

    return render(
        request,
        "persons/superuser_user_edit.html",
        {
            "form": form,
            "edit_user": target,
            "staff_nav_active": "users",
        },
    )


def _staff_listings_redirect(request, redirect_name: str):
    rq = (request.POST.get("return_q") or "").strip()
    base = reverse(redirect_name)
    if rq:
        return redirect(f"{base}?{urlencode({'q': rq})}")
    return redirect(base)


def _staff_listings_page(request, channel: str, nav_key: str):
    redirect_name = "administration:staff_stock" if nav_key == "stock" else "administration:staff_preorder"
    search_q = (request.GET.get("q") or "").strip()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove_listing":
            try:
                lid = int(request.POST.get("listing_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid listing.")
                return _staff_listings_redirect(request, redirect_name)
            deleted, _ = ProductListing.objects.filter(pk=lid, channel=channel).delete()
            if deleted:
                messages.success(request, "Removed from this catalog channel.")
            else:
                messages.warning(request, "Listing not found.")
        elif action == "bulk_remove_listings":
            raw_ids = request.POST.getlist("listing_ids")
            ids: list[int] = []
            for raw in raw_ids:
                try:
                    ids.append(int(raw))
                except (TypeError, ValueError):
                    continue
            ids = sorted(set(x for x in ids if x > 0))
            if not ids:
                messages.warning(request, "No listings selected.")
                return _staff_listings_redirect(request, redirect_name)
            deleted, _ = ProductListing.objects.filter(pk__in=ids, channel=channel).delete()
            if deleted:
                messages.success(request, f"Removed {deleted} listing(s) from this catalog channel.")
            else:
                messages.warning(request, "No selected listings were found.")
        elif action == "backfill_listings":
            created = 0
            existing = 0
            with transaction.atomic():
                for p in Product.objects.filter(is_active=True).only("pk").iterator():
                    _, was_created = ProductListing.objects.get_or_create(
                        product_id=p.pk,
                        channel=channel,
                        defaults={"quantity": 0},
                    )
                    if was_created:
                        created += 1
                    else:
                        existing += 1
            messages.success(
                request,
                f"Listings synced: {created} created, {existing} already in this channel.",
            )
            return _staff_listings_redirect(request, redirect_name)
        return _staff_listings_redirect(request, redirect_name)

    listings = (
        ProductListing.objects.filter(channel=channel)
        .select_related(
            "product",
            "product__shoe",
            "product__racket",
            "product__apparel",
        )
        .order_by("product__name", "product__id")
    )
    if search_q:
        listings = listings.filter(
            Q(product__name__icontains=search_q)
            | Q(product__brand__icontains=search_q)
            | Q(product__sku__icontains=search_q)
            | Q(product__short_description__icontains=search_q)
            | Q(product__color__icontains=search_q)
            | Q(product__type__icontains=search_q)
        )
    heading = (
        "In stock"
        if channel == ProductListingChannel.STOCK
        else "Preorder"
    )
    catalog_is_implicit = not ProductListing.objects.filter(channel=channel).exists()
    note = (
        "Each row is a catalog entry. Edit a product to set channel and quantity. "
        "Removing a row only drops it from this channel (product stays)."
    )
    if catalog_is_implicit:
        note += (
            " While this table is empty, the storefront still shows every active product "
            "for this channel. Use 'Add all active products' below to create one row per "
            "product (quantity 0) so the public list matches explicit listings."
        )
    return render(
        request,
        "persons/staff_listings.html",
        {
            "listings": listings,
            "listing_channel": channel,
            "staff_nav_active": nav_key,
            "page_heading": heading,
            "page_note": note,
            "catalog_is_implicit": catalog_is_implicit,
            "search_q": search_q,
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_stock_list(request):
    return _staff_listings_page(request, ProductListingChannel.STOCK, "stock")


@login_required
@user_passes_test(_superuser_required)
def staff_stock_receive(request):
    """Receive a purchase batch: multi-line cost update ± stock increment."""
    from decimal import Decimal, InvalidOperation

    if request.method == "POST":
        errors: list[str] = []
        note = (request.POST.get("note") or "").strip()
        try:
            total_forms = int(request.POST.get("lines-TOTAL_FORMS") or 0)
        except (TypeError, ValueError):
            total_forms = 0
        if total_forms < 1:
            errors.append("Add at least one product line.")
        if total_forms > 80:
            errors.append("Too many lines (max 80).")

        line_specs: list[dict] = []
        for i in range(max(0, total_forms)):
            prefix = f"lines-{i}"
            if request.POST.get(f"{prefix}-DELETE"):
                continue
            product = None
            raw_pid = (request.POST.get(f"{prefix}-product") or "").strip()
            if not raw_pid:
                continue  # blank trailing row
            try:
                product = Product.objects.get(pk=int(raw_pid))
            except (TypeError, ValueError, Product.DoesNotExist):
                errors.append(f"Line {i + 1}: select a valid product.")
                continue
            try:
                qty = int(request.POST.get(f"{prefix}-quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty < 1:
                errors.append(f"Line {i + 1} ({product.name}): quantity must be at least 1.")
            unit_cost = None
            try:
                unit_cost = Decimal(
                    str(request.POST.get(f"{prefix}-unit_cost") or "")
                    .replace(",", ".")
                    .strip()
                )
            except (InvalidOperation, ValueError):
                pass
            if unit_cost is None or unit_cost < 0:
                errors.append(
                    f"Line {i + 1} ({product.name}): enter a valid landed cost / unit (₾)."
                )
            on_hand = None
            on_hand_raw = (request.POST.get(f"{prefix}-on_hand") or "").strip()
            if on_hand_raw == "":
                on_hand = None  # live STOCK qty at write time (after prior lines)
            else:
                try:
                    on_hand = int(on_hand_raw)
                except (TypeError, ValueError):
                    errors.append(
                        f"Line {i + 1} ({product.name}): on-hand must be 0 or more."
                    )
                else:
                    if on_hand < 0:
                        errors.append(
                            f"Line {i + 1} ({product.name}): on-hand must be 0 or more."
                        )
            add_to_stock = bool(request.POST.get(f"{prefix}-add_to_stock"))
            variant_key = (request.POST.get(f"{prefix}-variant") or "").strip()
            if product is not None and qty >= 1 and unit_cost is not None and unit_cost >= 0:
                if on_hand_raw == "" or (on_hand is not None and on_hand >= 0):
                    line_specs.append(
                        {
                            "product": product,
                            "quantity": qty,
                            "unit_landed_cost_gel": unit_cost,
                            "on_hand_before": on_hand,
                            "add_to_stock": add_to_stock,
                            "variant_key": variant_key,
                        }
                    )

        if not line_specs and not errors:
            errors.append("Add at least one filled product line.")

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            try:
                receipts = receive_stock_lines(
                    lines=line_specs,
                    note=note,
                    created_by=request.user,
                )
            except ValueError as exc:
                messages.error(request, f"Batch not recorded: {exc}")
            else:
                n = len(receipts)
                units = sum(r.quantity for r in receipts)
                added = sum(1 for r in receipts if r.stock_added)
                messages.success(
                    request,
                    f"Received {n} line{'s' if n != 1 else ''} "
                    f"({units} unit{'s' if units != 1 else ''})"
                    f"{' — stock updated for ' + str(added) + ' line(s)' if added else ' — cost only'}."
                    + (f" Note: {note}" if note else ""),
                )
                return redirect("administration:staff_stock_receive")

    products = list(
        Product.objects.filter(is_active=True)
        .only("pk", "brand", "name", "color", "landed_cost_gel")
        .order_by("brand", "name", "id")
    )
    stock_qty_map = {
        str(pid): int(q)
        for pid, q in ProductListing.objects.filter(
            channel=ProductListingChannel.STOCK
        ).values_list("product_id", "quantity")
    }
    cost_map = {
        str(p.pk): (str(p.landed_cost_gel) if p.landed_cost_gel is not None else "")
        for p in products
    }
    # Size-grid products: existing variant keys for the datalist; presence of a
    # key in this map also tells the JS that a variant input is required.
    from shop.sales_order_stock import get_variant_qty_map, product_requires_variant

    variant_map = {}
    variant_products = Product.objects.filter(is_active=True).select_related(
        "racket", "shoe", "apparel", "string"
    )
    for p in variant_products:
        if product_requires_variant(p):
            variant_map[str(p.pk)] = sorted(get_variant_qty_map(p).keys())

    # Rebuild posted rows after validation errors; otherwise one empty starter row.
    initial_rows: list[dict] = []
    if request.method == "POST":
        try:
            posted_n = int(request.POST.get("lines-TOTAL_FORMS") or 0)
        except (TypeError, ValueError):
            posted_n = 0
        for i in range(posted_n):
            prefix = f"lines-{i}"
            if request.POST.get(f"{prefix}-DELETE"):
                continue
            initial_rows.append(
                {
                    "product": request.POST.get(f"{prefix}-product") or "",
                    "variant": request.POST.get(f"{prefix}-variant") or "",
                    "quantity": request.POST.get(f"{prefix}-quantity") or "",
                    "unit_cost": request.POST.get(f"{prefix}-unit_cost") or "",
                    "on_hand": request.POST.get(f"{prefix}-on_hand") or "",
                    "add_to_stock": bool(request.POST.get(f"{prefix}-add_to_stock")),
                }
            )
    if not initial_rows:
        preselect = ""
        raw_pre = request.GET.get("product") or ""
        if str(raw_pre).isdigit():
            preselect = str(int(raw_pre))
        initial_rows = [
            {
                "product": preselect,
                "variant": "",
                "quantity": "",
                "unit_cost": "",
                "on_hand": "",
                "add_to_stock": True,
            }
        ]

    note_value = request.POST.get("note", "") if request.method == "POST" else ""
    receipts = (
        StockReceipt.objects.select_related("product", "created_by")
        .order_by("-created_at", "-id")[:40]
    )
    return render(
        request,
        "persons/staff_stock_receive.html",
        {
            "products": products,
            "stock_qty_map": stock_qty_map,
            "cost_map": cost_map,
            "variant_map": variant_map,
            "initial_rows": initial_rows,
            "note_value": note_value,
            "receipts": receipts,
            "staff_nav_active": "stock",
            "page_heading": "Receive stock",
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_stock_stats(request):
    ctx = build_stock_stats()
    ctx.update(
        {
            "staff_nav_active": "stock_stats",
            "page_heading": "Stock stats",
            "page_note": (
                "In-stock catalog only: products with Stock channel quantity greater than zero. "
                "Stock value uses quantity × min(actual price, initial price) per SKU. "
                "Shoe matrix includes men's and women's shoes; unisex models count in both men's and women's columns. "
                "Junior shoe catalog type is excluded from the shoe matrix."
            ),
        }
    )
    return render(request, "persons/staff_stock_stats.html", ctx)


@login_required
@user_passes_test(_superuser_required)
def staff_preorder_list(request):
    return _staff_listings_page(request, ProductListingChannel.PREORDER, "preorder")


_MAX_HERO_SLIDES = 5
_MAX_HOME_PROMO_BANNERS = 5
_MAX_FEATURED_STORIES = 12
# Blog save runs Pillow + S3 in the request; Heroku default gunicorn timeout is 30s.
_MAX_BLOG_IMAGE_UPLOAD_BYTES = 30 * 1024 * 1024


def _parse_staff_datetime(raw):
    """Parse HTML datetime-local value into an aware datetime."""
    from datetime import datetime

    s = (raw or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@login_required
@user_passes_test(_superuser_required)
def staff_home_banners(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_hero_content":
            h = HomeHeroContent.load()
            h.headline = (request.POST.get("headline_en") or "").strip()
            h.subtext = (request.POST.get("subtext_en") or "").strip()
            h.cta_label = (request.POST.get("cta_label_en") or "").strip()[:120]
            h.cta_url = (request.POST.get("cta_url_en") or "").strip()[:500]
            h.secondary_link_label = (
                (request.POST.get("secondary_link_label_en") or "").strip()[:120]
            )
            h.secondary_link_url = (request.POST.get("secondary_link_url_en") or "").strip()[
                :500
            ]
            h.headline_ru = (request.POST.get("headline_ru") or "").strip()
            h.subtext_ru = (request.POST.get("subtext_ru") or "").strip()
            h.cta_label_ru = (request.POST.get("cta_label_ru") or "").strip()[:120]
            h.cta_url_ru = (request.POST.get("cta_url_ru") or "").strip()[:500]
            h.secondary_link_label_ru = (
                (request.POST.get("secondary_link_label_ru") or "").strip()[:120]
            )
            h.secondary_link_url_ru = (
                (request.POST.get("secondary_link_url_ru") or "").strip()[:500]
            )
            h.headline_ka = (request.POST.get("headline_ka") or "").strip()
            h.subtext_ka = (request.POST.get("subtext_ka") or "").strip()
            h.cta_label_ka = (request.POST.get("cta_label_ka") or "").strip()[:120]
            h.cta_url_ka = (request.POST.get("cta_url_ka") or "").strip()[:500]
            h.secondary_link_label_ka = (
                (request.POST.get("secondary_link_label_ka") or "").strip()[:120]
            )
            h.secondary_link_url_ka = (
                (request.POST.get("secondary_link_url_ka") or "").strip()[:500]
            )
            h.save()
            messages.success(request, "Hero text and links saved for all languages.")
            return redirect("administration:staff_home_banners")
        if action == "add_hero_slide":
            if HomeHeroSlide.objects.count() >= _MAX_HERO_SLIDES:
                messages.error(request, "You can have at most 5 hero slides.")
                return redirect("administration:staff_home_banners")
            image = request.FILES.get("image")
            if not image:
                messages.error(request, "Choose an image file for the new slide.")
                return redirect("administration:staff_home_banners")
            link = (request.POST.get("image_link_url") or "").strip()[:500]
            note = (request.POST.get("internal_note") or "").strip()[:200]
            next_order = (HomeHeroSlide.objects.aggregate(m=Max("sort_order"))["m"] or 0) + 1
            HomeHeroSlide.objects.create(
                image=image,
                sort_order=next_order,
                image_link_url=link,
                internal_note=note,
            )
            messages.success(request, "Hero slide added.")
            return redirect("administration:staff_home_banners")
        if action == "delete_hero_slide":
            try:
                sid = int(request.POST.get("slide_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid slide.")
                return redirect("administration:staff_home_banners")
            get_object_or_404(HomeHeroSlide, pk=sid).delete()
            messages.success(request, "Hero slide removed.")
            return redirect("administration:staff_home_banners")
        if action == "update_hero_slide":
            try:
                sid = int(request.POST.get("slide_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid slide.")
                return redirect("administration:staff_home_banners")
            slide = get_object_or_404(HomeHeroSlide, pk=sid)
            slide.image_link_url = (request.POST.get("image_link_url") or "").strip()[:500]
            slide.internal_note = (request.POST.get("internal_note") or "").strip()[:200]
            slide.save(update_fields=["image_link_url", "internal_note"])
            messages.success(request, "Slide link and note updated.")
            return redirect("administration:staff_home_banners")
        if action == "add_promo_banner":
            if HomePromoBanner.objects.count() >= _MAX_HOME_PROMO_BANNERS:
                messages.error(request, "You can have at most 5 promo banners.")
                return redirect("administration:staff_home_banners")
            image = request.FILES.get("promo_image")
            if not image:
                messages.error(request, "Choose an image file for the new promo banner.")
                return redirect("administration:staff_home_banners")
            link = (request.POST.get("promo_link_url") or "").strip()[:500]
            note = (request.POST.get("promo_internal_note") or "").strip()[:200]
            next_order = (HomePromoBanner.objects.aggregate(m=Max("sort_order"))["m"] or 0) + 1
            banner = HomePromoBanner(
                image=image,
                sort_order=next_order,
                image_link_url=link,
                internal_note=note,
            )
            try:
                banner.full_clean()
                banner.save()
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
                return redirect("administration:staff_home_banners")
            messages.success(request, "Promo banner added.")
            return redirect("administration:staff_home_banners")
        if action == "delete_promo_banner":
            try:
                sid = int(request.POST.get("promo_banner_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid promo banner.")
                return redirect("administration:staff_home_banners")
            get_object_or_404(HomePromoBanner, pk=sid).delete()
            messages.success(request, "Promo banner removed.")
            return redirect("administration:staff_home_banners")
        if action == "update_promo_banner":
            try:
                sid = int(request.POST.get("promo_banner_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid promo banner.")
                return redirect("administration:staff_home_banners")
            banner = get_object_or_404(HomePromoBanner, pk=sid)
            banner.image_link_url = (request.POST.get("promo_link_url") or "").strip()[:500]
            banner.internal_note = (request.POST.get("promo_internal_note") or "").strip()[:200]
            new_image = request.FILES.get("promo_replace_image")
            if new_image:
                if banner.image:
                    try:
                        banner.image.delete(save=False)
                    except OSError:
                        pass
                banner.image = new_image
            try:
                banner.full_clean()
                banner.save()
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
                return redirect("administration:staff_home_banners")
            messages.success(
                request,
                "Promo banner updated (including image)."
                if new_image
                else "Promo banner updated.",
            )
            return redirect("administration:staff_home_banners")
        messages.error(request, "Unknown action.")
        return redirect("administration:staff_home_banners")

    return render(
        request,
        "persons/staff_home_banners.html",
        {
            "staff_nav_active": "banners",
            "hero_content": HomeHeroContent.load(),
            "hero_slides": HomeHeroSlide.objects.all(),
            "promo_banners": HomePromoBanner.objects.all(),
            "max_hero_slides": _MAX_HERO_SLIDES,
            "max_promo_banners": _MAX_HOME_PROMO_BANNERS,
            "page_heading": "Shop home",
            "page_note": "Hero and promo carousel: up to 5 slides each. Hero overlay copy is editable per language "
            "(English, Russian, Georgian) below. Blog management moved to the Blog staff tab.",
        },
    )


def _unique_collection_slug(raw_slug: str, title: str, exclude_pk: int | None = None) -> str:
    base = slugify((raw_slug or "").strip())[:180] if raw_slug else slugify((title or "").strip())[:180]
    if not base:
        return ""
    candidate = base
    n = 2
    qs = ProductCollection.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=candidate).exists():
        suffix = f"-{n}"
        candidate = f"{base[: max(1, 180 - len(suffix))]}{suffix}"
        n += 1
    return candidate


@login_required
@user_passes_test(_superuser_required)
def staff_collections(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            cid = int(request.POST.get("collection_id", "0"))
        except (TypeError, ValueError):
            cid = 0
        collection = get_object_or_404(ProductCollection, pk=cid) if cid else None
        if action == "archive" and collection:
            collection.is_archived = not collection.is_archived
            collection.save(update_fields=["is_archived", "updated_at"])
            messages.success(
                request,
                "Collection archived." if collection.is_archived else "Collection restored.",
            )
            return redirect("administration:staff_collections")
        if action == "delete" and collection:
            collection.delete()
            messages.success(request, "Collection deleted.")
            return redirect("administration:staff_collections")
        messages.error(request, "Unknown action.")
        return redirect("administration:staff_collections")

    collections = ProductCollection.objects.prefetch_related("products").order_by("is_archived", "title", "id")
    return render(
        request,
        "persons/staff_collections_list.html",
        {
            "collections": collections,
            "staff_nav_active": "collections",
            "page_heading": "Collections",
            "page_note": "Curated storefront collections with optional banner and description.",
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_collection_edit(request, collection_id=None):
    target = get_object_or_404(ProductCollection, pk=collection_id) if collection_id else None
    max_groups = 5
    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()[:160]
        raw_slug = (request.POST.get("slug") or "").strip()[:180]
        description = (request.POST.get("description") or "").strip()
        if not title:
            messages.error(request, "Collection title is required.")
            return redirect(
                "administration:staff_collection_edit",
                collection_id=target.pk,
            ) if target else redirect("administration:staff_collection_new")
        slug = _unique_collection_slug(raw_slug, title, target.pk if target else None)
        if not slug:
            messages.error(request, "Collection slug could not be generated.")
            return redirect(
                "administration:staff_collection_edit",
                collection_id=target.pk,
            ) if target else redirect("administration:staff_collection_new")
        if target is None:
            target = ProductCollection(
                title=title,
                slug=slug,
                description=description,
                is_archived=request.POST.get("is_archived") == "1",
                banner_image=request.FILES.get("banner_image"),
            )
        else:
            target.title = title
            target.slug = slug
            target.description = description
            target.is_archived = request.POST.get("is_archived") == "1"
            new_banner = request.FILES.get("banner_image")
            if request.POST.get("remove_banner") == "1" and target.banner_image:
                target.banner_image.delete(save=False)
                target.banner_image = None
            if new_banner:
                target.banner_image = new_banner
        try:
            target.full_clean()
            target.save()
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
            if target.pk:
                return redirect(
                    "administration:staff_collection_edit",
                    collection_id=target.pk,
                )
            return redirect("administration:staff_collection_new")

        kept_group_ids: set[int] = set()
        group_product_union: set[int] = set()
        for idx in range(1, max_groups + 1):
            if request.POST.get(f"group_{idx}_enabled") != "1":
                continue
            raw_group_id = (request.POST.get(f"group_{idx}_id") or "").strip()
            group_title = (request.POST.get(f"group_{idx}_title") or "").strip()[:180]
            group_description = (request.POST.get(f"group_{idx}_description") or "").strip()
            group_products_raw = (request.POST.get(f"group_{idx}_product_ids") or "").strip()
            group_products: list[int] = []
            if group_products_raw:
                for part in group_products_raw.split(","):
                    try:
                        pid = int(part.strip())
                    except (TypeError, ValueError):
                        continue
                    if pid > 0:
                        group_products.append(pid)
            valid_ids = list(
                Product.objects.filter(pk__in=sorted(set(group_products)), is_active=True).values_list("pk", flat=True)
            )
            new_image = request.FILES.get(f"group_{idx}_image")
            remove_image = request.POST.get(f"group_{idx}_remove_image") == "1"

            group = None
            if raw_group_id.isdigit():
                group = ProductCollectionGroup.objects.filter(pk=int(raw_group_id), collection=target).first()
            if group is None:
                group = ProductCollectionGroup(collection=target, sort_order=idx)
            group.sort_order = idx
            group.title = group_title
            group.description = group_description
            if remove_image and group.image:
                group.image.delete(save=False)
                group.image = None
            if new_image:
                group.image = new_image
            try:
                group.full_clean()
                group.save()
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, f"Group {idx}: {msg}")
                return redirect(
                    "administration:staff_collection_edit",
                    collection_id=target.pk,
                )
            group.products.set(valid_ids)
            kept_group_ids.add(group.pk)
            group_product_union.update(valid_ids)

        ProductCollectionGroup.objects.filter(collection=target).exclude(pk__in=kept_group_ids).delete()
        target.products.set(sorted(group_product_union))
        messages.success(request, "Collection saved.")
        return redirect("administration:staff_collections")

    all_products = (
        Product.objects.filter(is_active=True)
        .select_related("category")
        .order_by("brand", "name", "id")
    )
    groups = (
        list(target.groups.prefetch_related("products").order_by("sort_order", "id"))
        if target
        else []
    )
    if not groups:
        legacy_ids = list(target.products.values_list("pk", flat=True)) if target else []
        if legacy_ids:
            seed = ProductCollectionGroup(
                collection=target,
                sort_order=1,
                title="",
                description="",
            )
            seed._seed_ids = sorted(set(legacy_ids))
            groups = [seed]

    group_forms = []
    for idx in range(1, max_groups + 1):
        grp = groups[idx - 1] if idx - 1 < len(groups) else None
        selected_ids = []
        if grp is not None:
            if hasattr(grp, "_seed_ids"):
                selected_ids = list(grp._seed_ids)
            elif getattr(grp, "pk", None):
                selected_ids = list(grp.products.values_list("pk", flat=True))
        group_forms.append(
            {
                "index": idx,
                "enabled": grp is not None,
                "group_id": getattr(grp, "pk", None) if grp is not None else None,
                "title": (getattr(grp, "title", "") or "") if grp is not None else "",
                "description": (getattr(grp, "description", "") or "") if grp is not None else "",
                "image": (getattr(grp, "image", None) if grp is not None else None),
                "selected_ids_csv": ",".join(str(pid) for pid in selected_ids),
            }
        )
    return render(
        request,
        "persons/staff_collection_form.html",
        {
            "collection": target,
            "group_forms": group_forms,
            "all_products": all_products,
            "max_groups": max_groups,
            "staff_nav_active": "collections",
            "page_heading": "Edit collection" if target else "New collection",
            "page_note": "Banner: raster 1600x560 px or SVG for sharp text. "
            "Add/remove products here without affecting the product database.",
        },
    )


def _blog_upload_is_svg(upload) -> bool:
    """Blog ImageFields are raster-only (Pillow); SVG can also trigger heavy dimension probing."""
    name = (getattr(upload, "name", "") or "").lower()
    if name.endswith(".svg"):
        return True
    ct = (getattr(upload, "content_type", "") or "").lower()
    if ct in ("image/svg+xml", "image/svg"):
        return True
    try:
        upload.seek(0)
        head = upload.read(8000)
        upload.seek(0)
    except Exception:
        return False
    stripped = head.lstrip(b"\xef\xbb\xbf").lower()
    if stripped.startswith(b"<svg"):
        return True
    if stripped.startswith(b"<?xml") and b"<svg" in stripped[:4000]:
        return True
    return False


def _unique_blog_slug(raw_slug: str, title: str, exclude_pk: int | None = None) -> str:
    base = slugify((raw_slug or "").strip())[:160] if raw_slug else slugify((title or "").strip())[:160]
    if not base:
        return ""
    candidate = base
    n = 2
    qs = BlogPost.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    while qs.filter(slug=candidate).exists():
        suffix = f"-{n}"
        candidate = (base[: 160 - len(suffix)] + suffix)
        n += 1
    return candidate


def _featured_limit_exceeded(target_post: BlogPost | None = None) -> bool:
    qs = BlogPost.objects.filter(is_featured_on_home=True)
    if target_post and target_post.pk and target_post.is_featured_on_home:
        return False
    return qs.count() >= _MAX_FEATURED_STORIES


@login_required
@user_passes_test(_superuser_required)
def staff_blog_posts(request):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            post_id = int(request.POST.get("post_id", "0"))
        except (TypeError, ValueError):
            post_id = 0
        post = BlogPost.objects.filter(pk=post_id).first() if post_id else None

        if action == "toggle_published" and post:
            post.is_published = not post.is_published
            if post.is_published and not post.published_at:
                post.published_at = timezone.now()
            if not post.is_published:
                post.published_at = None
            post.save(update_fields=["is_published", "published_at", "updated_at"])
            return redirect("administration:staff_blog_posts")

        if action == "toggle_featured" and post:
            turning_on = not post.is_featured_on_home
            if turning_on and _featured_limit_exceeded(post):
                messages.error(
                    request,
                    f"At most {_MAX_FEATURED_STORIES} posts can be featured on home.",
                )
                return redirect("administration:staff_blog_posts")
            post.is_featured_on_home = turning_on
            post.save(update_fields=["is_featured_on_home", "updated_at"])
            return redirect("administration:staff_blog_posts")

        if action == "delete_post" and post:
            post.delete()
            messages.success(request, "Blog post deleted.")
            return redirect("administration:staff_blog_posts")

        messages.error(request, "Unknown action.")
        return redirect("administration:staff_blog_posts")

    posts = BlogPost.objects.order_by("-published_at", "-id")
    return render(
        request,
        "persons/staff_blog_posts.html",
        {
            "staff_nav_active": "blog",
            "blog_posts": posts,
            "max_featured_stories": _MAX_FEATURED_STORIES,
            "page_heading": "Blog posts",
            "page_note": "Click a row to edit. Toggle Published/Featured directly in the table. Featured posts appear in home stories.",
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_blog_edit(request, post_id=None):
    post = get_object_or_404(BlogPost, pk=post_id) if post_id else None
    is_create = post is None

    def _back_to_edit():
        if is_create:
            return redirect("administration:staff_blog_new")
        return redirect("administration:staff_blog_edit", post_id=post_id)

    if request.method == "POST":
        title = (request.POST.get("title") or "").strip()[:220]
        if not title:
            messages.error(request, "Title is required.")
            return _back_to_edit()

        slug = _unique_blog_slug(
            request.POST.get("slug") or "",
            title,
            exclude_pk=None if is_create else post.pk,
        )
        if not slug:
            messages.error(request, "Could not build slug.")
            return _back_to_edit()

        target = BlogPost() if is_create else post
        target.title = title
        target.slug = slug
        target.lead = (request.POST.get("lead") or "").strip()
        target.body_block_1 = (request.POST.get("body_block_1") or "").strip()
        target.body_block_2 = (request.POST.get("body_block_2") or "").strip()
        target.body_block_3 = (request.POST.get("body_block_3") or "").strip()
        target.body_block_4 = (request.POST.get("body_block_4") or "").strip()
        # Keep legacy combined field in sync for compatibility/search.
        target.body = "\n\n".join(
            [
                b
                for b in (
                    target.body_block_1,
                    target.body_block_2,
                    target.body_block_3,
                    target.body_block_4,
                )
                if b
            ]
        )
        target.is_published = request.POST.get("is_published") == "1"
        dt = _parse_staff_datetime(request.POST.get("published_at"))
        target.published_at = (dt or target.published_at or timezone.now()) if target.is_published else None

        want_featured = request.POST.get("is_featured_on_home") == "1"
        if want_featured and _featured_limit_exceeded(target):
            messages.error(request, f"At most {_MAX_FEATURED_STORIES} featured posts are allowed.")
            return _back_to_edit()
        target.is_featured_on_home = want_featured
        try:
            target.featured_sort_order = max(0, int(request.POST.get("featured_sort_order") or 0))
        except ValueError:
            target.featured_sort_order = 0
        target.featured_cta_label = (request.POST.get("featured_cta_label") or "").strip()[:120]
        target.quote_text = (request.POST.get("quote_text") or "").strip()
        target.quote_author = (request.POST.get("quote_author") or "").strip()[:160]
        target.cta_mid_text = (request.POST.get("cta_mid_text") or "").strip()[:240]
        target.cta_mid_button_label = (request.POST.get("cta_mid_button_label") or "").strip()[:120]
        target.cta_mid_button_url = (request.POST.get("cta_mid_button_url") or "").strip()[:500]
        target.cta_end_text = (request.POST.get("cta_end_text") or "").strip()[:240]
        target.cta_end_button_label = (request.POST.get("cta_end_button_label") or "").strip()[:120]
        target.cta_end_button_url = (request.POST.get("cta_end_button_url") or "").strip()[:500]

        selected_products = []
        for i in range(1, 6):
            raw = (request.POST.get(f"featured_product_{i}") or "").strip()
            selected_products.append(int(raw) if raw.isdigit() else None)
        for i, pid in enumerate(selected_products, start=1):
            setattr(target, f"featured_product_{i}_id", pid)

        hero = request.FILES.get("hero_image")
        card = request.FILES.get("card_image")
        img1 = request.FILES.get("article_image_1")
        img2 = request.FILES.get("article_image_2")
        img3 = request.FILES.get("article_image_3")
        for upload, label in (
            (hero, "Hero image"),
            (card, "Card image"),
        ):
            if not upload:
                continue
            size = getattr(upload, "size", None) or 0
            if size > _MAX_BLOG_IMAGE_UPLOAD_BYTES:
                messages.error(
                    request,
                    f"{label}: file is too large (max {_MAX_BLOG_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB). "
                    "Resize or export a smaller image, then try again.",
                )
                return _back_to_edit()
            if _blog_upload_is_svg(upload):
                messages.error(
                    request,
                    f"{label}: SVG is not supported here. Use JPEG, PNG, or WebP for hero and card images.",
                )
                return _back_to_edit()

        for upload, label in (
            (img1, "Article image 1"),
            (img2, "Article image 2"),
            (img3, "Article image 3"),
        ):
            if not upload:
                continue
            size = getattr(upload, "size", None) or 0
            if size > _MAX_BLOG_IMAGE_UPLOAD_BYTES:
                messages.error(
                    request,
                    f"{label}: file is too large (max {_MAX_BLOG_IMAGE_UPLOAD_BYTES // (1024 * 1024)} MB). "
                    "Resize or export a smaller file, then try again.",
                )
                return _back_to_edit()
        if hero:
            target.hero_image = hero
        if card:
            target.card_image = card
        if img1:
            target.article_image_1 = img1
        if img2:
            target.article_image_2 = img2
        if img3:
            target.article_image_3 = img3
        target.save()
        messages.success(request, "Blog post saved.")
        return redirect("administration:staff_blog_edit", post_id=target.pk)

    active_products = Product.objects.filter(is_active=True).order_by("name", "id")
    return render(
        request,
        "persons/staff_blog_edit.html",
        {
            "staff_nav_active": "blog",
            "post_obj": post,
            "active_products": active_products,
            "is_create": is_create,
            "max_featured_stories": _MAX_FEATURED_STORIES,
            "page_heading": "New blog post" if is_create else "Edit blog post",
            "page_note": "Article order on the site: text 1 → quote → text 2 → mid CTA → text 3 → featured products (carousel) → text 4 → end CTA. Hero image: article page only. Card image: home carousel & blog list. Inline article images are stored but not shown in this layout.",
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_users(request):
    """
    Superuser-only: list users, create user, delete user (not self, not other superusers).
    """
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "delete":
            try:
                target_id = int(request.POST.get("user_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid user.")
                return redirect("administration:staff_users")
            if target_id == request.user.pk:
                messages.error(request, "You cannot delete your own account here.")
                return redirect("administration:staff_users")
            target = get_object_or_404(CustomUser, pk=target_id)
            if target.is_superuser:
                messages.error(
                    request,
                    "Superuser accounts cannot be removed from this page. Use Django admin if needed.",
                )
                return redirect("administration:staff_users")
            email = target.email
            target.delete()
            messages.success(request, f"User {email} has been deleted.")
            return redirect("administration:staff_users")

        if action == "create":
            form = SuperuserCreateUserForm(request.POST)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        user = CustomUser.objects.create_user(
                            email=form.cleaned_data["email"],
                            password=form.cleaned_data["password1"],
                        )
                        user.first_name = form.cleaned_data.get("first_name") or ""
                        user.last_name = form.cleaned_data.get("last_name") or ""
                        user.save(
                            update_fields=["first_name", "last_name"],
                        )
                        EmailAddress.objects.update_or_create(
                            user=user,
                            email=user.email.lower(),
                            defaults={
                                "primary": True,
                                "verified": True,
                            },
                        )
                        Player.objects.get_or_create(user=user)
                except Exception as e:
                    logger.exception("superuser_users create failed: %s", e)
                    messages.error(
                        request,
                        "Could not create user. Check logs or try a different email.",
                    )
                    users = CustomUser.objects.select_related(
                        "telegram_verification"
                    ).order_by("-date_joined")
                    return render(
                        request,
                        "persons/staff_users.html",
                        {
                            "users": users,
                            "create_form": form,
                            "staff_nav_active": "users",
                        },
                    )
                messages.success(
                    request,
                    f"User {user.email} created.",
                )
                return redirect("administration:staff_users")
        else:
            form = SuperuserCreateUserForm()
    else:
        form = SuperuserCreateUserForm()

    users = CustomUser.objects.select_related("telegram_verification").order_by(
        "-date_joined"
    )
    return render(
        request,
        "persons/staff_users.html",
        {
            "users": users,
            "create_form": form,
            "staff_nav_active": "users",
        },
    )