from allauth.account.views import LoginView, SignupView
from .forms import CustomLoginForm, CustomSignupForm
from django.urls import reverse


class CustomLoginView(LoginView):
    form_class = CustomLoginForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['signup_url'] = reverse('account_signup')
        return context

class CustomSignupView(SignupView):
    form_class = CustomSignupForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['login_url'] = reverse('account_login')
        return context

