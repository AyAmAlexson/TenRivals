from allauth.account.views import LoginView, SignupView
from .forms import CustomLoginForm, CustomSignupForm
from django.utils.decorators import method_decorator
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
from django.contrib import messages
from django.utils import timezone
from django.contrib.auth import logout, login
from shop.models import (
    HomeBanner,
    HomeBannerSlot,
    Product,
    ProductListing,
    ProductListingChannel,
    ShopOrder,
)
import hashlib
import hmac
from django.utils.encoding import force_str
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
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


class ShopOrderHistoryView(LoginRequiredMixin, ListView):
    model = ShopOrder
    template_name = 'account_order_history.html'
    context_object_name = 'orders'

    def get_queryset(self):
        return ShopOrder.objects.filter(user=self.request.user).prefetch_related('items')

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


@login_required
@user_passes_test(_superuser_required)
def redirect_legacy_superuser_users(request):
    return redirect("persons:staff_users")


@login_required
@user_passes_test(_superuser_required)
def redirect_legacy_superuser_send_verification(request, user_id):
    return redirect("persons:superuser_send_email_verification", user_id=user_id)


@login_required
@user_passes_test(_superuser_required)
def redirect_legacy_superuser_user_edit(request, user_id):
    return redirect("persons:superuser_user_edit", user_id=user_id)


@login_required
@user_passes_test(_superuser_required)
@require_POST
def superuser_send_email_verification(request, user_id):
    target = get_object_or_404(CustomUser, pk=user_id)
    if target.is_primary_email_verified:
        messages.info(request, f"{target.email} is already verified.")
        return redirect("persons:staff_users")
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
    return redirect("persons:staff_users")


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
                return redirect("persons:staff_users")
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


def _staff_listings_page(request, channel: str, nav_key: str):
    redirect_name = "persons:staff_stock" if nav_key == "stock" else "persons:staff_preorder"
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove_listing":
            try:
                lid = int(request.POST.get("listing_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid listing.")
                return redirect(redirect_name)
            deleted, _ = ProductListing.objects.filter(pk=lid, channel=channel).delete()
            if deleted:
                messages.success(request, "Removed from this catalog channel.")
            else:
                messages.warning(request, "Listing not found.")
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
            return redirect(redirect_name)
        return redirect(redirect_name)

    listings = (
        ProductListing.objects.filter(channel=channel)
        .select_related("product")
        .order_by("product__name", "product__id")
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
        },
    )


@login_required
@user_passes_test(_superuser_required)
def staff_stock_list(request):
    return _staff_listings_page(request, ProductListingChannel.STOCK, "stock")


@login_required
@user_passes_test(_superuser_required)
def staff_preorder_list(request):
    return _staff_listings_page(request, ProductListingChannel.PREORDER, "preorder")


_VALID_BANNER_SLOTS = {c[0] for c in HomeBannerSlot.choices}


@login_required
@user_passes_test(_superuser_required)
def staff_home_banners(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "replace_banner":
            slot = request.POST.get("slot", "")
            if slot not in _VALID_BANNER_SLOTS:
                messages.error(request, "Invalid banner slot.")
                return redirect("persons:staff_home_banners")
            image = request.FILES.get("image")
            if not image:
                messages.error(request, "Choose an image file.")
                return redirect("persons:staff_home_banners")
            link_url = (request.POST.get("link_url") or "").strip()
            note = (request.POST.get("internal_note") or "").strip()[:200]
            with transaction.atomic():
                now = timezone.now()
                HomeBanner.objects.filter(slot=slot, archived_at__isnull=True).update(
                    archived_at=now
                )
                HomeBanner.objects.create(
                    slot=slot,
                    image=image,
                    link_url=link_url,
                    internal_note=note,
                )
            messages.success(request, "Homepage banner updated. Previous image moved to archive.")
            return redirect("persons:staff_home_banners")
        if action == "restore_banner":
            try:
                bid = int(request.POST.get("banner_id", "0"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid banner.")
                return redirect("persons:staff_home_banners")
            banner = get_object_or_404(HomeBanner, pk=bid)
            if banner.archived_at is None:
                messages.warning(request, "That banner is already live.")
                return redirect("persons:staff_home_banners")
            slot = banner.slot
            with transaction.atomic():
                now = timezone.now()
                HomeBanner.objects.filter(slot=slot, archived_at__isnull=True).update(
                    archived_at=now
                )
                banner.archived_at = None
                banner.save(update_fields=["archived_at"])
            messages.success(request, "Banner restored as current for its slot.")
            return redirect("persons:staff_home_banners")

    current_banners = {}
    for slot_value, _label in HomeBannerSlot.choices:
        current_banners[slot_value] = (
            HomeBanner.objects.filter(slot=slot_value, archived_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
    archived_banners = HomeBanner.objects.filter(archived_at__isnull=False).order_by(
        "-archived_at", "-id"
    )[:200]
    banner_slots = [
        {"value": v, "label": lbl, "current": current_banners[v]}
        for v, lbl in HomeBannerSlot.choices
    ]
    return render(
        request,
        "persons/staff_home_banners.html",
        {
            "staff_nav_active": "banners",
            "banner_slots": banner_slots,
            "archived_banners": archived_banners,
            "page_heading": "Homepage banners",
            "page_note": "Replace images for the shop home hero (3 slots). Uploading archives the previous image for that slot. Restore any archived row to make it live again.",
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
                return redirect("persons:staff_users")
            if target_id == request.user.pk:
                messages.error(request, "You cannot delete your own account here.")
                return redirect("persons:staff_users")
            target = get_object_or_404(CustomUser, pk=target_id)
            if target.is_superuser:
                messages.error(
                    request,
                    "Superuser accounts cannot be removed from this page. Use Django admin if needed.",
                )
                return redirect("persons:staff_users")
            email = target.email
            target.delete()
            messages.success(request, f"User {email} has been deleted.")
            return redirect("persons:staff_users")

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
                return redirect("persons:staff_users")
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