from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from allauth.account.models import EmailAddress


def _verified_user(email, password, **extra):
    user = get_user_model().objects.create_user(email=email, password=password, **extra)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return user


@override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False, ACCOUNT_EMAIL_VERIFICATION='mandatory')
class LoginFailureTests(TestCase):
    def test_telegram_verified_user_without_player_can_sign_in(self):
        email = 'tg-no-player@example.com'
        password = 'LoginCheck-2026'
        user = _verified_user(email, password)
        user.is_telegram_verified = True
        user.save(update_fields=['is_telegram_verified'])

        response = self.client.post(
            '/accounts/login/',
            {'login': email, 'password': password},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('went wrong', response.content.decode().lower())

    def test_unconfigured_social_login_is_not_a_server_error(self):
        for path in ('/accounts/google/login/', '/accounts/facebook/login/'):
            response = self.client.get(path)
            self.assertLess(response.status_code, 500, path)
            self.assertNotContains(response, 'Something went wrong', status_code=response.status_code)
            self.assertContains(response, 'Social Network Login Failure', status_code=response.status_code)
