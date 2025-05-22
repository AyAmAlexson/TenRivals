from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import CustomUser, TelegramVerification, PasswordResetToken
from rivals.models import Player


class CustomUserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'is_staff', 'is_telegram_verified', 'is_player', 'is_author', 'is_test_user')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups', 'is_telegram_verified', 'is_player', 'is_author', 'is_test_user')

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'telegram')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
        ('Status', {'fields': ('is_player', 'is_author', 'is_test_user', 'is_telegram_verified')}),
        ('Preferences', {'fields': ('preferred_city', 'preferred_geo')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password', 'password2'),
        }),
    )
    search_fields = ('email', 'first_name', 'last_name', 'telegram')
    ordering = ('email',)


admin.site.register(CustomUser, CustomUserAdmin)


@admin.register(TelegramVerification)
class TelegramVerificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'telegram_username', 'telegram_id', 'is_verified', 'verified_at')
    search_fields = ('user__email', 'telegram_username', 'telegram_id')
    list_filter = ('is_verified',)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'code', 'created_at', 'is_used', 'is_valid')
    search_fields = ('user__email', 'code')
    list_filter = ('is_used', 'created_at')

@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ('user', 'city', 'geo', 'avatar', 'is_new', 'is_fake')
    search_fields = ('user__email', 'city', 'geo')
    list_filter = ('is_new', 'is_fake')




