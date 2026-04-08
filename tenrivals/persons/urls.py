from django.urls import path
from . import views
from django.views.generic import TemplateView

app_name = 'persons'

urlpatterns = [
    # Аутентификация
    path('login/', views.CustomLoginView.as_view(), name='account_login'),
    path('signup/', views.CustomSignupView.as_view(), name='account_signup'),
    
    # Управление аккаунтом
    path('my_account/', views.AccountDetailView.as_view(), name='account_details'),
    path('my_account/orders/', views.ShopOrderHistoryView.as_view(), name='shop_order_history'),
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

    path('staff/users/', views.staff_users, name='staff_users'),
    path(
        'staff/users/<int:user_id>/send-email-verification/',
        views.superuser_send_email_verification,
        name='superuser_send_email_verification',
    ),
    path(
        'staff/users/<int:user_id>/edit/',
        views.superuser_user_edit,
        name='superuser_user_edit',
    ),
    path('staff/stock/', views.staff_stock_list, name='staff_stock'),
    path('staff/preorder/', views.staff_preorder_list, name='staff_preorder'),
    path('staff/blog/', views.staff_blog_posts, name='staff_blog_posts'),
    path('staff/blog/new/', views.staff_blog_edit, name='staff_blog_new'),
    path('staff/blog/<int:post_id>/', views.staff_blog_edit, name='staff_blog_edit'),
    path('staff/banners/', views.staff_home_banners, name='staff_home_banners'),

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
