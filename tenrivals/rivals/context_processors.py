from persons.models import CustomUser
from .forms import TicketForm

def test_users(request):
    """Добавляет список тестовых пользователей в контекст для админа"""
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_test_user:
            return {
                'test_users': CustomUser.objects.filter(
                    is_active=True,
                    is_test_user=True,
                ).exclude(
                    is_superuser=True
                ).order_by('first_name', 'last_name')[:15]  
            }
    return {'test_users': []} 

def ticket_form(request):
    if request.user.is_authenticated:
        form = TicketForm()
        
        return {'ticket_form': form}
    return {'ticket_form': None}