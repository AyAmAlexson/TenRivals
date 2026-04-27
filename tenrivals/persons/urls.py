from django.urls import path
from . import views
from django.views.generic import TemplateView, RedirectView

app_name = 'persons'

urlpatterns = [
    # Аутентификация
    path('login/', views.CustomLoginView.as_view(), name='account_login'),
    path('signup/', views.CustomSignupView.as_view(), name='account_signup'),
    
    # Управление аккаунтом
    path('my_account/', views.AccountDetailView.as_view(), name='account_details'),
    path('my_account/orders/', views.ShopOrderHistoryView.as_view(), name='shop_order_history'),
    path('my_account/order-for-me/', views.AccountOrderForMeHistoryView.as_view(), name='order_for_me_history'),
    path('my_account/order-for-me/<int:order_id>/cancel/', views.account_order_for_me_cancel, name='order_for_me_cancel'),
    path('delete/', views.delete_profile, name='delete_profile'),
    
    # Управление email
    path('email/change/', views.change_email, name='change_email'),
    path('email/confirm/<uidb64>/<token>/', 
         views.ConfirmEmailChangeView.as_view(), name='confirm_email_change'),
    
    # Управление Telegram
    path('verify-telegram/', views.verify_telegram, name='verify_telegram'),
    path('resend-verification-code/', views.resend_verification_code, name='resend_verification_code'),
    path('telegram/change/', views.change_telegram, name='change_telegram'),
    path('telegram/callback/', views.telegram_callback, name='telegram_callback'),


    path(
        'accounts/password/tg-request/',
        TemplateView.as_view(template_name="account/password_reset_request_tg.html"),
        name='account_password_request_tg'
    ),

    path('staff/users/', RedirectView.as_view(pattern_name='administration:staff_users', permanent=False)),
    path(
        'staff/users/<int:user_id>/send-email-verification/',
        RedirectView.as_view(pattern_name='administration:superuser_send_email_verification', permanent=False),
    ),
    path(
        'staff/users/<int:user_id>/edit/',
        RedirectView.as_view(pattern_name='administration:superuser_user_edit', permanent=False),
    ),
    path('staff/stock/', RedirectView.as_view(pattern_name='administration:staff_stock', permanent=False)),
    path('staff/preorder/', RedirectView.as_view(pattern_name='administration:staff_preorder', permanent=False)),
    path('staff/blog/', RedirectView.as_view(pattern_name='administration:staff_blog_posts', permanent=False)),
    path('staff/blog/new/', RedirectView.as_view(pattern_name='administration:staff_blog_new', permanent=False)),
    path('staff/blog/<int:post_id>/', RedirectView.as_view(pattern_name='administration:staff_blog_edit', permanent=False)),
    path('staff/banners/', RedirectView.as_view(pattern_name='administration:staff_home_banners', permanent=False)),
    path(
        'staff/promo-codes/',
        RedirectView.as_view(pattern_name='administration:staff_promo_codes', permanent=False),
    ),

    path('superuser/users/', views.redirect_legacy_superuser_users, name='superuser_users'),
    path(
        'superuser/users/<int:user_id>/send-email-verification/',
        views.redirect_legacy_superuser_send_verification,
    ),
    path(
        'superuser/users/<int:user_id>/edit/',
        views.redirect_legacy_superuser_user_edit,
    ),

]
