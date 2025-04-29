# Django core
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import TemplateView, View, FormView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.views.generic import ListView, DetailView, CreateView, UpdateView
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect
from django.http import HttpResponse
from django.db.models import QuerySet, Q
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.timezone import now


# Python standard library
from datetime import datetime, UTC, timedelta, date
import logging
import random
import string
from functools import wraps


# Third party
import telebot

# Local imports
from .const import TR_GEOS, TR_CITIES
from .forms import (
    TournamentForm, 
    StageProlongationRequestForm, 
    PlayerUpdateForm, 
    CityUpdateForm, 
    AvatarUpdateForm, 
    RateOpponentForm, 
    PlayerMatchScoreForm, 
    DualPlayerMatchScoreForm, 
    TicketForm,
    PlayerWizardForm,
    AddTelegramUsernameForm,
    PlayerWizardAvatarForm
)

from .services import (
    approve_match_result_service,
    reopen_match_result_service,
    check_overdue_matches_service,
    apply_match_result_service,
    delete_match_result_service
)
from .filters import TournamentsQuickFilter, AllRivalsFilter
from .models import (
    Tournament,
    Player,
    PlayerSeasonStats,
    PairSeasonStats,
    PairTotalStats,
    PlayerCurrentStats,
    Match,
    PlayerMatch,
    PairTournament,
    PlayerFeedbackAttributes,
    PlayerAttributes,
    PlayerTournament,
    PlayerCurrentStats,
    ReviewToken,
    Ticket,
    StageProlongationRequest,
    TimelineEvent,
    PlayerOnboarding
)
from persons.models import CustomUser, TelegramVerification
from persons.forms import ChangeEmailForm, TelegramVerificationForm
from persons.services import generate_verification_code_service
import qrcode
import base64
from io import BytesIO

# Initialize logger
logger = logging.getLogger(__name__)

# Get User model
User = get_user_model()

# Initialize Telegram bot if token is configured
if settings.TELEGRAM_BOT_TOKEN:
    try:
        bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN)
    except Exception as e:
        logger.error(f"Failed to initialize Telegram bot: {e}")
        bot = None
else:
    bot = None

class IndexView(TemplateView):
    template_name = 'tr-landing.html'

class BasicView(TemplateView):
    template_name = 'basic.html'

class AdminTestView(TemplateView):
    template_name = 'admin_test_page.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_tickets = Ticket.objects.all().order_by('is_closed', '-created_at')
        context['all_tickets'] = all_tickets
        
        overdue_matches = Match.objects.filter(status='OD')
        context['overdue_matches'] = overdue_matches

        stage_prolongation_requests = StageProlongationRequest.objects.filter(is_closed=False).order_by('-created_at')
        stage_prolongation_requests_archived = StageProlongationRequest.objects.filter(is_closed=True).order_by('-created_at')
        context['stage_prolongation_requests'] = stage_prolongation_requests
        context['stage_prolongation_requests_archived'] = stage_prolongation_requests_archived

        return context

class AdminPlayerUpdateView(View):
    

    def get(self, request, player_id, *args, **kwargs):
        player = get_object_or_404(Player.objects.select_related('user'), id=player_id)
        
        server_time = timezone.now()
        player_form = PlayerUpdateForm(instance=player)
        city_form = CityUpdateForm(
            geo_list=TR_GEOS,
            cities_list=TR_CITIES,
            initial={'city': player.city}
        )
        avatar_form = AvatarUpdateForm(instance=player)
        context = {
            'player_form': player_form,
            'city_form': city_form,
            'avatar_form': avatar_form,
            'player': player,
            
            'server_time': server_time,
        }
        return render(request, 'rivals/player_update.html', context)
    
    def post(self, request, player_id, *args, **kwargs):
        player = get_object_or_404(Player.objects.select_related('user'), id=player_id)
        
        logger.debug("Получен POST-запрос: %s", request.POST)

        if 'update_player' in request.POST:
            logger.debug("Processing player update form.")
            player_form = PlayerUpdateForm(request.POST, instance=player)
            city_form = CityUpdateForm(
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                initial={'city': player.city}
            )
            avatar_form = AvatarUpdateForm(instance=player)
            if player_form.is_valid():
                player_form.save()
                logger.info("Player profile updated: %s", player)
                messages.success(request, 'Player profile updated successfully.')
                return redirect('rivals:player_update')
            else:
                logger.error("Errors in player update form: %s", player_form.errors)
                messages.error(request, 'Please correct the errors in the form.')

        elif 'update_city' in request.POST:
            logger.debug("Processing city update form.")
            player_form = PlayerUpdateForm(instance=player)
            city_form = CityUpdateForm(
                request.POST,
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                
            )
            avatar_form = AvatarUpdateForm(instance=player)
            if city_form.is_valid():
                if city_form.cleaned_data['city'] == player.city:
                    messages.info(request, f'City is already set to {dict(TR_CITIES).get(player.city)}.')
                else:
                    try:
                        city_form.save(player, request.user)
                        logger.info("City updated successfully to %s", player.city)
                        messages.success(request, f'City updated successfully to {dict(TR_CITIES).get(player.city)}.')
                        return redirect('rivals:player_update')
                    except ValueError as e:
                        logger.error("Error saving city: %s", e)
                        messages.error(request, 'Error saving city.')
            else:
                logger.error("Errors in player or city update form: %s, %s", player_form.errors, city_form.errors)
                messages.error(request, 'Please correct the errors in the form.')
        
        elif 'update_avatar' in request.POST:
            logger.debug("Processing avatar update form.")
            avatar_form = AvatarUpdateForm(request.POST, request.FILES, instance=player)
            player_form = PlayerUpdateForm(instance=player)
            city_form = CityUpdateForm(
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                initial={'city': player.city}
            )
            if avatar_form.is_valid():
                avatar_form.save()
                logger.info("Avatar updated successfully: %s", player.avatar.url)
                messages.success(request, 'Avatar updated successfully.')
                return redirect('rivals:player_update')
            else:
                logger.error("Errors in avatar update form: %s", avatar_form.errors)
                messages.error(request, 'Please correct the errors in the avatar form.')

        elif 'change_email' in request.POST:
            logger.debug("Processing email change form.")
            email_form = ChangeEmailForm(user=request.user, request=request, data=request.POST)
            if email_form.is_valid():
                try:
                    email_form.save()
                    logger.info("Email change initiated for user: %s", request.user)
                    messages.success(
                        request,
                        "Please check your new email address for confirmation link."
                    )
                    return redirect('rivals:player_update')
                except Exception as e:
                    logger.error("Error changing email: %s", e)
                    messages.error(request, "Error sending confirmation email.")
            else:
                logger.error("Email change form errors: %s", email_form.errors)
                messages.error(request, "Please correct the errors below.")
            
            # Переинициализируем остальные формы
            player_form = PlayerUpdateForm(instance=player)
            city_form = CityUpdateForm(
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                initial={'city': player.city}
            )
            avatar_form = AvatarUpdateForm(instance=player)
            
            context = {
                'player_form': player_form,
                'city_form': city_form,
                'avatar_form': avatar_form,
                'player': player,
                'account_change_email_form': email_form,
                'telegram_verification': TelegramVerification.objects.get(user=request.user),
                'time_now': datetime.now(UTC),
            }
            return render(request, 'rivals/player_update.html', context)
            
        else:
            logger.warning("Unknown form sent.")
            messages.error(request, 'Unknown form sent.')
            return super().post(request, *args, **kwargs)

        # If form is not valid or unknown form
        player_form = PlayerUpdateForm(instance=player)
        city_form = CityUpdateForm(
            geo_list=TR_GEOS,
            cities_list=TR_CITIES,
            initial={'city': player.city}
        )
        avatar_form = AvatarUpdateForm(instance=player)
        context = {
            'player_form': player_form,
            'city_form': city_form,
            'avatar_form': avatar_form,
        }
        return render(request, 'rivals/player_update.html', context)
    
    def get_success_url(self):
        return reverse('rivals:player_detail', kwargs={'pk': self.player.pk})

class PlayerUpdateView(View):
    def get(self, request, *args, **kwargs):
        server_time = timezone.now()
        player = request.user.player
        player_form = PlayerUpdateForm(instance=player)
        city_form = CityUpdateForm(
            geo_list=TR_GEOS,
            cities_list=TR_CITIES,
            initial={'city': player.city}
        )
        avatar_form = AvatarUpdateForm(instance=player)
        email_form = ChangeEmailForm(user=request.user, request=request)

        # Получаем или создаем запись верификации Telegram
        telegram_verification, created = TelegramVerification.objects.get_or_create(
            user=request.user,
            defaults={
                'telegram_username': '',
                'verification_code': generate_verification_code(),
                'is_verified': False
            }
        )

        context = {
            'player_form': player_form,
            'city_form': city_form,
            'avatar_form': avatar_form,
            'player': player,
            'account_change_email_form': email_form,
            'server_time': server_time,
            'telegram_verification': telegram_verification,
        }
        return render(request, 'rivals/player_update.html', context)

    def post(self, request, *args, **kwargs):
        player = request.user.player
        
        logger.debug("Получен POST-запрос: %s", request.POST)

        if 'update_player' in request.POST:
            logger.debug("Processing player update form.")
            player_form = PlayerUpdateForm(request.POST, instance=player)
            city_form = CityUpdateForm(
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                initial={'city': player.city}
            )
            avatar_form = AvatarUpdateForm(instance=player)
            if player_form.is_valid():
                player_form.save()
                logger.info("Player profile updated: %s", player)
                messages.success(request, 'Player profile updated successfully.')
                return redirect('rivals:player_update')
            else:
                logger.error("Errors in player update form: %s", player_form.errors)
                messages.error(request, 'Please correct the errors in the form.')

        elif 'update_city' in request.POST:
            logger.debug("Processing city update form.")
            player_form = PlayerUpdateForm(instance=player)
            city_form = CityUpdateForm(
                request.POST,
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                
            )
            avatar_form = AvatarUpdateForm(instance=player)
            if city_form.is_valid():
                if city_form.cleaned_data['city'] == player.city:
                    messages.info(request, f'City is already set to {dict(TR_CITIES).get(player.city)}.')
                else:
                    try:
                        city_form.save(player, request.user)
                        logger.info("City updated successfully to %s", player.city)
                        messages.success(request, f'City updated successfully to {dict(TR_CITIES).get(player.city)}.')
                        return redirect('rivals:player_update')
                    except ValueError as e:
                        logger.error("Error saving city: %s", e)
                        messages.error(request, 'Error saving city.')
            else:
                logger.error("Errors in player or city update form: %s, %s", player_form.errors, city_form.errors)
                messages.error(request, 'Please correct the errors in the form.')
        
        elif 'update_avatar' in request.POST:
            logger.debug("Processing avatar update form.")
            avatar_form = AvatarUpdateForm(request.POST, request.FILES, instance=player)
            player_form = PlayerUpdateForm(instance=player)
            city_form = CityUpdateForm(
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                initial={'city': player.city}
            )
            if avatar_form.is_valid():
                avatar_form.save()
                logger.info("Avatar updated successfully: %s", player.avatar.url)
                messages.success(request, 'Avatar updated successfully.')
                return redirect('rivals:player_update')
            else:
                logger.error("Errors in avatar update form: %s", avatar_form.errors)
                messages.error(request, 'Please correct the errors in the avatar form.')

        elif 'change_email' in request.POST:
            logger.debug("Processing email change form.")
            email_form = ChangeEmailForm(user=request.user, request=request, data=request.POST)
            if email_form.is_valid():
                try:
                    email_form.save()
                    logger.info("Email change initiated for user: %s", request.user)
                    messages.success(
                        request,
                        "Please check your new email address for confirmation link."
                    )
                    return redirect('rivals:player_update')
                except Exception as e:
                    logger.error("Error changing email: %s", e)
                    messages.error(request, "Error sending confirmation email.")
            else:
                logger.error("Email change form errors: %s", email_form.errors)
                messages.error(request, "Please correct the errors below.")
            
            # Переинициализируем остальные формы
            player_form = PlayerUpdateForm(instance=player)
            city_form = CityUpdateForm(
                geo_list=TR_GEOS,
                cities_list=TR_CITIES,
                initial={'city': player.city}
            )
            avatar_form = AvatarUpdateForm(instance=player)
            
            context = {
                'player_form': player_form,
                'city_form': city_form,
                'avatar_form': avatar_form,
                'player': player,
                'account_change_email_form': email_form,
                'telegram_verification': TelegramVerification.objects.get(user=request.user),
                'time_now': datetime.now(UTC),
            }
            return render(request, 'rivals/player_update.html', context)
            
        elif 'verify_telegram' in request.POST:
            verification_code = request.POST.get('verification_code')
            telegram_verification = TelegramVerification.objects.get(user=request.user)
            
            if verification_code == telegram_verification.verification_code:
                telegram_verification.is_verified = True
                telegram_verification.verified_at = timezone.now()
                telegram_verification.save()
                logger.info("Telegram verified successfully for user: %s", request.user)
                messages.success(request, 'Telegram account verified successfully!')
                return redirect('rivals:player_update')
            else:
                logger.error("Invalid verification code for user: %s", request.user)
                messages.error(request, 'Invalid verification code.')

        elif 'change_telegram' in request.POST:
            new_telegram = request.POST.get('telegram_username')
            if new_telegram:
                telegram_verification = TelegramVerification.objects.get(user=request.user)
                telegram_verification.telegram_username = new_telegram
                telegram_verification.is_verified = False
                telegram_verification.verification_code = generate_verification_code()
                telegram_verification.save()
                
                # Отправляем новый код верификации через бота
                try:
                    bot = telebot.TeleBot(settings.TELEGRAM_BOT_TOKEN)
                    message = f"Your verification code is: {telegram_verification.verification_code}"
                    bot.send_message(new_telegram, message)
                    logger.info("Verification code sent to Telegram: %s", new_telegram)
                    messages.success(request, 'Please check your Telegram for the verification code.')
                except Exception as e:
                    logger.error("Error sending Telegram message: %s", e)
                    messages.error(request, 'Error sending verification code. Please try again.')
                
                return redirect('rivals:player_update')
            else:
                messages.error(request, 'Please provide a Telegram username.')
            
        else:
            logger.warning("Unknown form sent.")
            messages.error(request, 'Unknown form sent.')
            return super().post(request, *args, **kwargs)

        # If form is not valid or unknown form
        player_form = PlayerUpdateForm(instance=player)
        city_form = CityUpdateForm(
            geo_list=TR_GEOS,
            cities_list=TR_CITIES,
            initial={'city': player.city}
        )
        avatar_form = AvatarUpdateForm(instance=player)
        email_form = ChangeEmailForm(user=request.user, request=request)
        telegram_verification = TelegramVerification.objects.get(user=request.user)
        
        context = {
            'player_form': player_form,
            'city_form': city_form,
            'avatar_form': avatar_form,
            'account_change_email_form': email_form,
            'player': player,
            'telegram_verification': telegram_verification,
            'time_now': datetime.now(UTC),
        }
        return render(request, 'rivals/player_update.html', context)
    
    def get_success_url(self):
        return reverse('rivals:player_update')
        


class AllRivalsView(ListView):
    model = Player
    template_name = 'rivals/all_rivals.html'
    context_object_name = 'rivals'
    

    def get_queryset(self):
        queryset = super().get_queryset().select_related('current_stats')
        get_query = self.request.GET.copy()

        # Установка значения по умолчанию для 'geo', если оно не задано
        if 'geo' not in get_query and self.request.user.is_authenticated:
            try:
                get_query['geo'] = self.request.user.preferred_geo
            except AttributeError:
                preferred_geo = None

        # Инициализация фильтра с обновленным запросом
        self.filterset = AllRivalsFilter(get_query, queryset)

        if 'geo' in get_query:
            filters = {'geo': get_query.get('geo')}
            if 'category' in get_query:
                filters['category'] = get_query.get('category')
            self.total_players_by_geo_and_category = queryset.filter(**filters).count()
        else:
            self.total_players_by_geo_and_category = queryset.count()
        # Получение выбранного значения 'geo' без использования cleaned_data
        geo_value = get_query.get('geo')

        if geo_value:
            filtered_cities = [
                (city_code, city_name) 
                for city_code, city_name in TR_CITIES 
                if city_name[-2:] == geo_value
            ]
            if len(filtered_cities) <= 1:
                filtered_cities.pop(0)
            
            self.filterset.form.fields['city'].choices = filtered_cities
        else:
            self.filterset.form.fields['city'].choices = TR_CITIES

        return self.filterset.qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filterset'] = self.filterset
        context['total_players_by_geo_and_category'] = self.total_players_by_geo_and_category
        context['current_week'] = date.today().isocalendar()[1]
        context['current_season'] = date.today().year
        
        return context
    
class TournamentsListView(ListView):
    model = Tournament
    # Измените 'date_created' на существующее поле, например, 'id' или другое подходящее поле
    ordering = 'id'  # или другое поле, которое существует в модели Tournament
    # Указываем имя шаблона, в котором будут все инструкции о том,
    # как именно пользователю должны быть показаны наши объекты
    template_name = 'tournaments.html'
    # Это имя списка, в котором будут лежать все объекты.
    # Его надо указать, чтобы обратиться к списку объектов в html-шаблоне.
    context_object_name = 'tournaments'
    paginate_by = 6

    def get_queryset(self):
        # Получаем обычный запрос
        queryset = super().get_queryset()
        get_query = self.request.GET.copy()
        #if 'geo' not in get_query:
        #    get_query['geo'] = 'GE'
        ##if 'city' not in get_query:
        #    get_query['city'] = 'TBI'
        #if 'format' not in get_query:
        #    get_query['format'] = 'S'
        # Используем наш класс фильтрации.
        # Сохраняем нашу фильтрацию в объекте класса,
        # чтобы потом добавить в контекст и использовать в шаблоне.

        self.filterset = TournamentsQuickFilter(get_query, queryset)

        return self.filterset.qs

    def get_ordering(self):
        self.order = self.request.GET.get('order', 'asc')
        selected_ordering = self.request.GET.get('ordering', 'id')  # Измените на существующее поле
        if self.order == "desc":
            selected_ordering = "-" + selected_ordering
        return selected_ordering

    def get_context_data(self, **kwargs):
        # С помощью super() мы обращаемся к родительским классам
        # и вызываем у них метод get_context_data с теми же аргументами,
        # что и были переданы нам.
        # В ответе мы должны получить словарь.
        context = super().get_context_data(**kwargs)
        # К словарю добавим текущую дату в ключ 'time_now'.
        context['time_now'] = datetime.now(UTC)
        context['server_time'] = timezone.now()
        # Добавим ещё одну пустую переменную,
        # чтобы на её примере рассмотреть работу ещё одного фильтра.
        context['geo_list'] = TR_GEOS
        context['cities_list'] = TR_CITIES
        context['current_order'] = self.get_ordering()
        context['order'] = self.order
        context['filterset'] = self.filterset
        return context
    
def start_new_week(season=date.today().year, week=date.today().isocalendar()[1]):
    players = Player.objects.all()
    for player in players:
        player_season_week, created = PlayerSeasonStats.objects.get_or_create(
            player=player,
            season=season,
            week=week,
            defaults={
                
                'geo': player.geo,
                'ranking_absolute': 0,
                'ranking_category': 0,
            }
        )
        if created:
            player_previous_week = PlayerSeasonStats.objects.filter(player=player, season=season, week=week-1).first()
            if player_previous_week:
                player_season_week.ranking_absolute = player_previous_week.ranking_absolute
                player_season_week.ranking_category = player_previous_week.ranking_category
                player_season_week.season_pts = player_previous_week.season_pts
                player_season_week.season_start_NTRP = player_previous_week.season_start_NTRP
                player_season_week.season_final_NTRP = player_previous_week.season_final_NTRP
                player_season_week.max_season_NTRP = player_previous_week.max_season_NTRP
                player_season_week.min_season_NTRP = player_previous_week.min_season_NTRP
                player_season_week.matches_played = player_previous_week.matches_played
                player_season_week.matches_won = player_previous_week.matches_won
                player_season_week.matches_lost = player_previous_week.matches_lost
                player_season_week.save()
            else:
                player_season_week.ranking_absolute = 0
                player_season_week.ranking_category = 0
                player_season_week.season_pts = 0
                player_season_week.season_start_NTRP = 1000
                player_season_week.max_season_NTRP = 1000
                player_season_week.min_season_NTRP = 1000
                player_season_week.matches_played = 0
                player_season_week.matches_won = 0
                player_season_week.matches_lost = 0
                player_season_week.save()

def update_current_stats(players:QuerySet):
    current_season = date.today().year
    current_week = date.today().isocalendar()[1]
    
    for player in players:
        player_current_stats, created = PlayerCurrentStats.objects.get_or_create(player=player)
        player_current_stats.updated_week = current_week
        player_current_stats.updated_season = current_season

        last_week_stats = PlayerSeasonStats.objects.filter(player=player).order_by('-season','-week').first()
        if last_week_stats:
            
            player_current_stats.matches_played_this_season = last_week_stats.matches_played
            player_current_stats.matches_won_this_season = last_week_stats.matches_won
            player_current_stats.matches_rted_this_season = last_week_stats.matches_rted
            player_current_stats.matches_lost_this_season = last_week_stats.matches_lost
            player_current_stats.season_pts = last_week_stats.season_pts
            player_current_stats.current_NTRP = last_week_stats.season_final_NTRP
            player_current_stats.max_NTRP_this_season = last_week_stats.max_season_NTRP
            player_current_stats.min_NTRP_this_season = last_week_stats.min_season_NTRP
            player_current_stats.ranking_absolute = last_week_stats.ranking_absolute
            player_current_stats.ranking_category = last_week_stats.ranking_category

            if last_week_stats.max_season_NTRP > player_current_stats.max_NTRP:
                player_current_stats.max_NTRP = last_week_stats.max_season_NTRP
            if last_week_stats.min_season_NTRP < player_current_stats.min_NTRP:
                player_current_stats.min_NTRP = last_week_stats.min_season_NTRP

            player_current_stats.season_start_NTRP = last_week_stats.season_start_NTRP
        
        last_weeks = PlayerSeasonStats.objects.filter(player=player).values('season').annotate(max_week=Max('week'))
        latest_season_stats = PlayerSeasonStats.objects.filter(player=player, week__in=[entry['max_week'] for entry in last_weeks])
        player_current_stats.matches_played = latest_season_stats.aggregate(total_matches=Sum('matches_played'))['total_matches'] or 0 
        player_current_stats.matches_won = latest_season_stats.aggregate(won_matches=Sum('matches_won'))['won_matches'] or 0 
        player_current_stats.matches_rted = latest_season_stats.aggregate(rted_matches=Sum('matches_rted'))['rted_matches'] or 0 
        player_current_stats.matches_lost = latest_season_stats.aggregate(lost_matches=Sum('matches_lost'))['lost_matches'] or 0 

        player_current_stats.save()

    return redirect('rivals:player_detail', pk=player.pk)

def update_current_stats_all(request):
    current_season = date.today().year
    current_week = date.today().isocalendar()[1]

    players = Player.objects.all()
    for player in players:
        player_current_stats, created = PlayerCurrentStats.objects.get_or_create(player=player)
        player_current_stats.updated_week = current_week
        player_current_stats.updated_season = current_season
        last_week_stats = PlayerSeasonStats.objects.filter(player=player).order_by('-season','-week').first()
        if last_week_stats:
            player_current_stats.matches_played_this_season = last_week_stats.matches_played
            player_current_stats.matches_won_this_season = last_week_stats.matches_won
            player_current_stats.matches_rted_this_season = last_week_stats.matches_rted
            player_current_stats.matches_lost_this_season = last_week_stats.matches_lost
            player_current_stats.season_pts = last_week_stats.season_pts
            player_current_stats.current_NTRP = last_week_stats.season_final_NTRP
            player_current_stats.max_NTRP_this_season = last_week_stats.max_season_NTRP
            player_current_stats.min_NTRP_this_season = last_week_stats.min_season_NTRP

            if last_week_stats.max_season_NTRP > player_current_stats.max_NTRP:
                player_current_stats.max_NTRP = last_week_stats.max_season_NTRP
            if last_week_stats.min_season_NTRP < player_current_stats.min_NTRP:
                player_current_stats.min_NTRP = last_week_stats.min_season_NTRP

            player_current_stats.season_start_NTRP = last_week_stats.season_start_NTRP

            player_current_stats.ranking_absolute = last_week_stats.ranking_absolute
            player_current_stats.ranking_category = last_week_stats.ranking_category

        last_weeks = PlayerSeasonStats.objects.filter(player=player).values('season').annotate(max_week=Max('week'))
        latest_season_stats = PlayerSeasonStats.objects.filter(player=player, week__in=[entry['max_week'] for entry in last_weeks])
        player_current_stats.matches_played = latest_season_stats.aggregate(total_matches=Sum('matches_played'))['total_matches'] or 0 
        player_current_stats.matches_won = latest_season_stats.aggregate(won_matches=Sum('matches_won'))['won_matches'] or 0 
        player_current_stats.matches_rted = latest_season_stats.aggregate(rted_matches=Sum('matches_rted'))['rted_matches'] or 0 
        player_current_stats.matches_lost = latest_season_stats.aggregate(lost_matches=Sum('matches_lost'))['lost_matches'] or 0 
        player_current_stats.save()

    
    return redirect('rivals:all_rivals')

def update_season_ranking(request, *args, **kwargs):
    current_season = date.today().year
    current_week = date.today().isocalendar()[1]

    season = kwargs.get('season', current_season)
    week = kwargs.get('week', current_week)

    if not isinstance(season, int):
        season = current_season
    if not isinstance(week, int):
        week = current_week

    # Создаем или обновляем записи для всех игроков
    players = Player.objects.all()
    for player in players:
        # Получаем или создаем запись для текущей недели
        defaults = {
            'geo': player.geo,
            'ranking_absolute': 0,
            'ranking_category': 0,
            'season_pts': 0,
            'season_start_NTRP': 1000,
            'season_final_NTRP': 1000,
            'max_season_NTRP': 1000,
            'min_season_NTRP': 1000,
            'matches_played': 0,
            'matches_won': 0,
            'matches_lost': 0
        }

        # Проверяем данные предыдущей недели
        player_previous_week = PlayerSeasonStats.objects.filter(
            player=player, 
            season=season, 
            week=week-1
        ).first()

        if player_previous_week:
            defaults.update({
                'ranking_absolute': player_previous_week.ranking_absolute,
                'ranking_category': player_previous_week.ranking_category,
                'season_pts': player_previous_week.season_pts,
                'season_start_NTRP': player_previous_week.season_start_NTRP,
                'season_final_NTRP': player_previous_week.season_final_NTRP,
                'max_season_NTRP': player_previous_week.max_season_NTRP,
                'min_season_NTRP': player_previous_week.min_season_NTRP,
                'matches_played': player_previous_week.matches_played,
                'matches_won': player_previous_week.matches_won,
                'matches_lost': player_previous_week.matches_lost,
            })

        PlayerSeasonStats.objects.get_or_create(
            player=player,
            season=season,
            week=week,
            defaults=defaults
        )

    # Обновляем рейтинги
    players_ranked_by_pts = PlayerSeasonStats.objects.filter(
        season=season, 
        week=week
    ).order_by('-season_pts')

    geo_rank = {}
    
    for player in players_ranked_by_pts:
        if player.geo not in geo_rank:
            geo_rank[player.geo]={}
            geo_rank[player.geo]['absolute']=1
            if player.player.category not in geo_rank[player.geo]:
                geo_rank[player.geo][player.player.category]=1
            else:
                geo_rank[player.geo][player.player.category]+=1
        else:
            geo_rank[player.geo]['absolute']+=1
            if player.player.category not in geo_rank[player.geo]:
                geo_rank[player.geo][player.player.category] = 1
            else:
                geo_rank[player.geo][player.player.category] += 1
        
        # Обновляем рейтинги
        PlayerSeasonStats.objects.filter(
            id=player.id
        ).update(
            ranking_absolute=geo_rank[player.geo]['absolute'],
            ranking_category=geo_rank[player.geo][player.player.category]
        )

    update_current_stats_all(request)
    return redirect('rivals:all_rivals')

def player_detail(request, pk):
    player = get_object_or_404(Player.objects.select_related('user', 'attributes', 'current_stats'), pk=pk)
    current_season = date.today().year
    current_week = date.today().isocalendar()[1]

    recent_matches = PlayerMatch.objects.filter(player=player).select_related('match__tournament').prefetch_related('player').order_by('-match__date')
    latest_season_stat = player.season_stats.all().order_by('-season', '-week').first()
    current_season_points = latest_season_stat.season_pts if latest_season_stat else 0

    current_tournaments = PlayerTournament.objects.filter(
        player=player,
        tournament__status__in=['AC', 'CI']
    ).select_related('tournament')
    
    past_tournaments = PlayerTournament.objects.filter(
        player=player,
        tournament__status='FI'
    ).select_related('tournament').order_by('-tournament__start_date')


    stats = player.season_stats.all().order_by('season', 'week')  
    ntrp_labels = [f"{stat.season} W{stat.week}" for stat in stats]
    ntrp_data = [stat.season_final_NTRP for stat in stats]

    rating_labels = [f"{stat.season} W{stat.week}" for stat in stats]
    rating_data = [stat.season_pts for stat in stats]

    
    attributes, created = PlayerAttributes.objects.get_or_create(player=player)
    current_stats = player.current_stats
    season_stats = player.season_stats.all()

    upcoming_matches = PlayerMatch.objects.filter(
        player=player,
        match__status='PD'
    ).select_related('match')

    last_3_opponents = PlayerMatch.objects.filter(
        player=player,
        match__status='FI'
    ).select_related('match').order_by('-match__date')[:3]

    # Подготовка данных для Polar Area графика
    attribute_labels = [
        'Forehand',
        'Backhand',
        'Rallies',
        'Attack',
        'Serve',
        'Return',
        'Volleys',
        
    ]
    attribute_data = [
        attributes.forehand,
        attributes.backhand,
        attributes.rallies,
        attributes.attack,
        attributes.serve,
        attributes.serve_return,
        attributes.volleys,
        
    ]
    
    target_attribute_data = [
        5,
        5,
        5,
        5,
        5,
        5,
        5,
    ]
    
    # Получение последних 3 оценок от других игроков
    last_three_ratings = PlayerFeedbackAttributes.objects.filter(
        player=player
    ).order_by('-date')[:3]
    
    review_token = ReviewToken.objects.filter(to_player=player, from_player=request.user.player).first()

    context = {
        'player': player,
        'recent_matches': recent_matches,
        'current_tournaments': current_tournaments,
        'past_tournaments': past_tournaments,
        'rating_labels': rating_labels,
        'rating_data': rating_data,
        'ntrp_labels': ntrp_labels,
        'ntrp_data': ntrp_data,
        'attributes': attributes,
        'current_stats': current_stats,
        'season_stats': season_stats,
        'attribute_labels': attribute_labels,
        'attribute_data': attribute_data,
        'target_attribute_data': target_attribute_data,
        'last_three_ratings': last_three_ratings,
        'current_season_points': current_season_points,
        'current_season': current_season,
        'current_week': current_week,
        'upcoming_matches': upcoming_matches,
        'last_3_opponents': last_3_opponents,
        'review_token': review_token,
    }
    
    # Обработка формы вызова на поединок
    #if request.method == 'POST':
    #    form = ChallengeForm(request.POST)
    #    if form.is_valid():
    #        challenge = form.save(commit=False)
    #        challenge.challenger = request.user.player
    #        challenge.rival = player
    #        challenge.save()
    #        return redirect('rivals:player_detail', pk=player.pk)
    #else:
    #    form = ChallengeForm()
    
    #context['form'] = form
    
    return render(request, 'rivals/player_detail.html', context)

def is_tournament_participant_and_has_match_overdue_or_admin(view_func):
    @wraps(view_func)
    def _wrapped_view(request, pk, *args, **kwargs):
        tournament = get_object_or_404(Tournament, pk=pk)
        player = request.user.player

        # Проверка участия в турнире
        if player not in tournament.players.all():
            raise PermissionDenied("You don't have permission to prolong the stage of this tournament.")

        # Проверка наличия назначенного или просроченного матча
        has_unplayed_match = tournament.matches.filter(
            players__in=[player],  # Исправлено: используем players__in для ManyToMany
            status__in=['PD', 'OD']  # PD = Pending, OD = Overdue
        ).exists()

        if has_unplayed_match or request.user.is_staff:
            return view_func(request, pk, *args, **kwargs)
        else:
            raise PermissionDenied("You don't have any pending or overdue matches in this tournament.")
        
    return _wrapped_view

def has_valid_review_token(view_func):
    @wraps(view_func)
    def _wrapped_view(request, pk, *args, **kwargs):
        # Проверяем существование валидного токена
        has_token = ReviewToken.objects.filter(
            from_player=request.user.player,
            to_player_id=pk,
            is_used=False
        ).exists()
        
        if not has_token:
            raise PermissionDenied("You don't have permission to rate this player.")
        
        return view_func(request, pk, *args, **kwargs)
    return _wrapped_view

@login_required
@has_valid_review_token
def rate_opponent(request, pk):
    player_to_rate = get_object_or_404(Player, pk=pk)
    current_player = request.user.player
    
    if player_to_rate == current_player:
        messages.error(request, "You can't rate yourself.")
        return redirect('rivals:player_detail', pk=pk)

    if request.method == 'POST':
        form = RateOpponentForm(request.POST)
        if form.is_valid():
            # Извлечение данных из формы
            forehand = float(form.cleaned_data['forehand'])
            backhand = float(form.cleaned_data['backhand'])
            rallies = float(form.cleaned_data['rallies'])
            attack = float(form.cleaned_data['attack'])
            serve = float(form.cleaned_data['serve'])
            serve_return = float(form.cleaned_data['serve_return'])
            volleys = float(form.cleaned_data['volleys'])

            # Создание экземпляра PlayerFeedbackAttributes
            PlayerFeedbackAttributes.objects.create(
                player=player_to_rate,
                from_player=current_player,
                forehand=forehand,
                backhand=backhand,
                rallies=rallies,
                attack=attack,
                serve=serve,
                serve_return=serve_return,
                volleys=volleys
            )

            # Обновление или создание PlayerAttributes
            player_attr, created = PlayerAttributes.objects.get_or_create(player=player_to_rate)
            player_attr.update_attributes( )

            TimelineEvent.objects.create(
                player=player_to_rate,
                from_player=current_player,
                event_type='R',
                color='I',
                text='Received new skills rating' ,
                redirect_url=reverse('rivals:player_detail', kwargs={'pk': player_to_rate.pk}),
                action_text='View your profile'
            )

            TimelineEvent.objects.create(
                player=current_player,
                event_type='R',
                color='I',
                text="Rated opponent's skills" ,
                redirect_url=reverse('rivals:player_detail', kwargs={'pk': player_to_rate.pk}),
                action_text=player_to_rate
            )

            messages.success(request, "Your rating has been saved.")
            try:
                review_token = ReviewToken.objects.filter(to_player=player_to_rate, from_player=current_player).first()
                review_token.is_used = True
                review_token.save()
            except ReviewToken.DoesNotExist:
                pass
            return redirect('rivals:player_detail', pk=pk)
    else:
        form = RateOpponentForm()

    context = {
        'form': form,
        'player_to_rate': player_to_rate,
    }
    return render(request, 'rivals/rate_opponent.html', context)

@login_required
@user_passes_test(lambda u: u.is_staff)
def add_player_to_tournament(request, tournament_pk, player_pk):
    tournament = get_object_or_404(Tournament, pk=tournament_pk)
    player = get_object_or_404(Player, pk=player_pk)
    if tournament.status != 'CI':
        messages.error(request, "Registration for this tournament is closed.")
        return redirect('rivals:tournament_edit', pk=tournament_pk)
    if tournament.players_enrolled >= tournament.players_num:
        messages.error(request, "There are no available slots in this tournament.")
        return redirect('rivals:tournament_edit', pk=tournament_pk)
    if tournament and player:   
        entry, created = PlayerTournament.objects.get_or_create(player=player, tournament=tournament)
        if created:
            tournament.players_enrolled += 1
            tournament.save()
            messages.success(request, "You have successfully added a player to the tournament!")
        else:
            messages.info(request, "This player is already registered for this tournament.")
        TimelineEvent.objects.create(
            player=player,
            event_type='T',
            text=f"You are registered for the tournament {tournament.title}" ,
            redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': tournament.pk})
        )
    else:
        messages.warning(request, "Something went wrong. Please try again.")
    return redirect('rivals:tournament_edit', pk=tournament_pk)

@login_required 
@user_passes_test(lambda u: u.is_staff)
def remove_player_from_tournament(request, tournament_pk, player_pk):
    try:
        tournament = Tournament.objects.get(pk=tournament_pk)
    except Tournament.DoesNotExist:
        messages.error(request, "No such tournament.")
        return redirect('rivals:tournament_list')  # или другой нужный redirect

    try:
        player = Player.objects.get(pk=player_pk)
    except Player.DoesNotExist:
        messages.error(request, "No such player.")
        return redirect('rivals:tournament_list')
    
    if tournament.status != 'CI':
        messages.error(request, "Registration for this tournament is closed.")
        return redirect('rivals:tournament_edit', pk=tournament_pk)
    
    if tournament and player:
        try:
            entry = PlayerTournament.objects.get(player=player, tournament=tournament)
        except PlayerTournament.DoesNotExist:
            messages.info(request, "This player is not registered for this tournament.")
        else:
            entry.delete()
            tournament.players_enrolled -= 1
            tournament.save()
            TimelineEvent.objects.create(
                player=player,
                event_type='T',
                text=f"You are exluded from the tournament {tournament.title}" ,
                redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': tournament.pk})
            )
            messages.success(request, "Player has been removed from the tournament.")
        
    return redirect('rivals:tournament_edit', pk=tournament_pk)


@login_required 
@user_passes_test(lambda u: u.is_staff)
def tournament_activate(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if tournament.status != 'CI':
        messages.error(request, "Tournament is already active.")
        return redirect('rivals:tournament_detail', pk=pk)
    elif tournament.players_enrolled < tournament.players_num:
        messages.error(request, "Tournament is not full.")
        return redirect('rivals:tournament_detail', pk=pk)
    else:
        tournament.start()
        messages.success(request, "Tournament has been activated.")
        for player in tournament.players.all():
            TimelineEvent.objects.create(
                player=player,
                event_type='T',
                text=f"Tournament {tournament.title} has started" ,
                redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': tournament.pk}),
                color='2',
                action_text='View Tournament'
            )
        return redirect('rivals:tournament_detail', pk=pk)

@login_required 
@user_passes_test(lambda u: u.is_staff)    
def tournament_restart(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    tournament.matches.all().delete()
    tournament.status = 'CI'
    
    tournament.save()

    tournament.start()
    messages.success(request, "Tournament has been restarted.")
    for player in tournament.players.all():
            TimelineEvent.objects.create(
                player=player,
                event_type='T',
                text=f"Tournament {tournament.title} has started" ,
                redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': tournament.pk}),
                color='W',
                action_text='View Tournament'
            )
    return redirect('rivals:tournament_detail', pk=pk)

class TournamentDetailView(DetailView):
    model = Tournament
    template_name = 'rivals/tournament_detail.html'
    context_object_name = 'tournament'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        server_time = timezone.now()
        tournament = self.get_object()
        num_empty_slots = tournament.players_num - tournament.players.count()

        if self.request.user.is_authenticated:
            player = self.request.user.player
            is_eligible = (
                player.category == tournament.category and
                player.user.preferred_city == tournament.city and
                (player.gender == tournament.gender or tournament.gender == 'X') and
                num_empty_slots > 0
            )
            is_already_enrolled = PlayerTournament.objects.filter(player=player, tournament=tournament).exists()

        else:
            player = None
            is_eligible = False
            is_already_enrolled = False
        
        
        
        checkin_progress = int(tournament.players.count()/tournament.players_num * 100)

        matches = (tournament.matches.all()
                .prefetch_related(
                    Prefetch('player_matches',
                            queryset=PlayerMatch.objects.select_related('player','player__user', 'opponent','opponent__user').order_by('player__pk')
                    ),
                    'players','players__user','players__season_stats'
                ))
        matches_current_stage = matches.filter(tournament_stage=tournament.current_stage)
        matches_current_stage_total = matches_current_stage.count()
        matches_current_stage_completed = matches_current_stage.filter(status='FI').count() + matches_current_stage.filter(status='CA').count()
        if matches_current_stage_total > 0:
            stage_progress = int(matches_current_stage_completed/matches_current_stage_total * 100)
        else:
            stage_progress = 100
        

        stages = range(1, tournament.current_stage+1)

        players_tournament = PlayerTournament.objects.filter(tournament=tournament).order_by('flow').select_related('player')


        context.update({
            'player': player,
            'server_time': server_time,
            'empty_slots': range(num_empty_slots) if num_empty_slots > 0 else [],
            'is_eligible': is_eligible,
            'checkin_progress': checkin_progress,
            'is_already_enrolled': is_already_enrolled,
            'matches': matches,
            'stages': stages,
            'stage_days_left': (tournament.current_stage_end_date - server_time.date()).days if tournament.current_stage_end_date else 0,
            'stage_progress': stage_progress,
            'players_tournament': players_tournament,
        })
        return context

@login_required
def join_tournament(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    user = request.user

    # Check if the user is a player
    if not user.is_player:
        messages.error(request, "Only players can register for tournaments.")
        return redirect('rivals:tournament_detail', pk=pk)

    # Attempt to get the associated Player object
    try:
        player = Player.objects.get(user=user)
    except Player.DoesNotExist:
        messages.warning(request, "You do not have a player profile. Please update your profile.")
        return redirect('rivals:player_update')

    # Check for available slots
    if tournament.players_enrolled >= tournament.players_num:
        messages.error(request, "There are no available slots in this tournament.")
        return redirect('rivals:tournament_detail', pk=pk)

    # Check tournament status
    if tournament.status != 'CI':  # 'CI' stands for 'Check-in Open'
        messages.error(request, "Registration for this tournament is closed.")
        return redirect('rivals:tournament_detail', pk=pk)

    # Check if player's category matches tournament category
    if not player.category or player.category != tournament.category:
        player_category_display = get_display_value(player.category, TOURNAMENT_CATEGORY)
        tournament_category_display = get_display_value(tournament.category, TOURNAMENT_CATEGORY)
        messages.error(
            request,
            f"Your category ({player_category_display}) does not match the tournament category ({tournament_category_display})."
        )
        return redirect('rivals:tournament_detail', pk=pk)

    # Check if player's preferred city matches tournament city
    if player.user.preferred_city != tournament.city:
        user_city_display = get_display_value(player.user.preferred_city, TR_CITIES)
        tournament_city_display = get_display_value(tournament.city, TR_CITIES)
        messages.error(
            request,
            f"You are located in {user_city_display}, but the tournament is held in {tournament_city_display}."
        )
        return redirect('rivals:tournament_detail', pk=pk)

    # Check if user account is active
    if not user.is_active:
        messages.error(request, "Your account is inactive. Please contact support.")
        return redirect('rivals:tournament_detail', pk=pk)

    # Check if player's contact information is complete
    if not player.has_contact_info():
        messages.warning(request, "Your contact information is incomplete. Please update your profile.")
        return redirect('rivals:player_update')

    # Check if user is already registered for the tournament
    if PlayerTournament.objects.filter(player=player, tournament=tournament).exists():
        messages.info(request, "You are already registered for this tournament.")
        return redirect('rivals:tournament_detail', pk=pk)

    # Register the player for the tournament
    try:
        with transaction.atomic():
            PlayerTournament.objects.create(player=player, tournament=tournament)
            tournament.players_enrolled += 1
            tournament.save()
            TimelineEvent.objects.create(
                player=player,
                event_type='T',
                text=f"You registered for the tournament {tournament.title}" ,
                redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': tournament.pk}),
                action_text='View Tournament Details',
                color='2'
            )
            messages.success(request, "You have successfully registered for the tournament!")
    except Exception as e:
        messages.error(request, f"An error occurred while registering: {str(e)}")
        return redirect('rivals:tournament_detail', pk=pk)

    return redirect('rivals:tournament_detail', pk=pk)

def get_display_value(value, choices):
    """
    Helper function to get the display value from choices.
    """
    return dict(choices).get(value, "Unknown")

class TournamentCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Tournament
    form_class = TournamentForm
    template_name = 'rivals/tournament_form.html'
    

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Tournament has been successfully created!")
        return response

    def test_func(self):
        # Здесь можно добавить проверку, имеет ли пользователь право создавать турниры
        return self.request.user.is_staff  # Например, только администраторы могут создавать турниры
    
    def get_success_url(self):
        return reverse('rivals:tournament_detail', kwargs={'pk': self.object.pk})

class TournamentUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Tournament
    form_class = TournamentForm
    template_name = 'rivals/tournament_form.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tournament'] = self.object
        if self.object.gender == 'X':
            eligible_players = Player.objects.filter(
                category=self.object.category,
                user__preferred_city=self.object.city
            )
        else:
            eligible_players = Player.objects.filter(
                category=self.object.category,
                user__preferred_city=self.object.city,
                gender=self.object.gender
            )
        context['eligible_players'] = eligible_players
        
        if self.object.gender == 'X':
            condition = ~Q(user__preferred_city=self.object.city) | ~Q(category=self.object.category)
        else:
            condition = (
                ~Q(user__preferred_city=self.object.city) |
                ~Q(category=self.object.category) |
                ~Q(gender=self.object.gender)
            )
        other_players = Player.objects.filter(condition).select_related('user')
        context['other_players'] = other_players
        
        
        players_enrolled = PlayerTournament.objects.filter(tournament=self.object)
        context['players_enrolled'] = players_enrolled
        context['players_enrolled_count'] = players_enrolled.count()
        context['free_slots'] = range(self.object.players_num - players_enrolled.count())
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Tournament has been successfully updated!")
        return response

    def test_func(self):
        return self.request.user.is_staff
    
    def get_success_url(self):
        return reverse('rivals:tournament_detail', kwargs={'pk': self.object.pk})
    
def match_detail(request, pk):
    
    match = get_object_or_404(Match, pk=pk)
    

    if match:
        player = match.players.all().order_by('pk').first()
        opponent = match.players.all().order_by('pk').last()
    else:
        return redirect('rivals:tournaments')         
    try:
        player_pm = PlayerMatch.objects.get(match=match, player=player, opponent=opponent)
    except PlayerMatch.DoesNotExist:
        messages.warning(request, "No players checked in for this match.")
        return redirect('rivals:tournaments')
    
    try:
        opponent_pm = PlayerMatch.objects.get(match=match, player=opponent, opponent=player)
    except PlayerMatch.DoesNotExist:
        messages.warning(request, "No players checked in for this match.")
        return redirect('rivals:tournaments')
      
    # Передаем, кто выиграл матч 
    overall_winner = None
    if player_pm and opponent_pm:
        
        if player_pm.is_winner:
            overall_winner = 'player'
            
        elif opponent_pm.is_winner:
            overall_winner = 'opponent'
            
    
    # Обработка формы (если используется комбинированная форма) или другая логика...
    if request.method == 'POST':
        
        dual_form = DualPlayerMatchScoreForm(
            request.POST,
            player_instance=player_pm,
            opponent_instance=opponent_pm,
            match_instance=match,
            request=request
        )
        if dual_form.is_valid():
            
            dual_form.save()
            TimelineEvent.objects.create(
                player=player,
                event_type='M',
                color='1',
                text=f"{opponent if request.user.player != player else 'You'} entered the result for the match vs. {opponent if request.user.player == player else 'you'}" ,
                redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
                action_text='View Match Details'
            )
            TimelineEvent.objects.create(
                player=opponent,
                event_type='M',
                color='1',
                text=f"{player if request.user.player != opponent else 'You'} entered the result for the match vs. {player if request.user.player == opponent else 'you'}" ,
                redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
                action_text='View Match Details'
            )
            return redirect('rivals:match_detail', pk=match.pk)
        else:
            print(request, "Dual form errors:", dual_form.errors)
    else:
        dual_form = DualPlayerMatchScoreForm(
            player_instance=player_pm,
            opponent_instance=opponent_pm,
            match_instance=match,
            request=request
        )

    review_tokens = ReviewToken.objects.filter(after_match=match)

    context = {
        'match': match,
        'dual_form': dual_form,
        # Дополнительно можно передать отдельно для удобства в шаблоне
        'player': player,
        'opponent': opponent,
        'player_pm': player_pm,
        'opponent_pm': opponent_pm,
        'overall_winner': overall_winner,
        'review_tokens': review_tokens,
    }
    return render(request, 'rivals/match_detail.html', context)

@login_required
@user_passes_test(lambda u: u.is_staff)
def reopen_match_result(request, match_pk):
    if reopen_match_result_service(match_pk):
        messages.success(request, "Match result has been successfully reopened!")
    else:
        messages.warning(request, "Match result can not be reopened.")
    return redirect('rivals:match_detail', pk=match_pk) 
   
def approve_match_result(request, pk, forced=False, by_timeout=False):
    if approve_match_result_service(pk, forced, by_timeout):
        messages.success(request, "Match result has been successfully approved!")
    else:
        messages.warning(request, "Match result can not be approved.")
    return redirect('rivals:match_detail', pk=pk)

@login_required
@user_passes_test(lambda u: u.is_staff)
def implement_match_result_all(request):
    player_matches = PlayerMatch.objects.filter(is_implemented=False, is_approved_by_player=True, is_approved_by_opponent=True)
    counter = 0
    for player_match in player_matches:
        apply_match_result_service(player_match.pk)
        counter += 1
    messages.success(request, f"Successfully implemented {counter} match results.")
    return redirect('rivals:admin_test')

class TodayView(LoginRequiredMixin, TemplateView):
    template_name = 'rivals/today.html'
    
    def get_inspiration_quote(self):
        try:
            response = requests.get('https://zenquotes.io/api/random')
            if response.status_code == 200:
                quote_data = response.json()[0]
                return f'"{quote_data["q"]}" — {quote_data["a"]}'
        except:
            # Fallback quotes if API fails
            fallback_quotes = [
                "Every match is a new opportunity to grow stronger.",
                "Success in tennis is built one point at a time.",
                "The only competition that matters is the one with yourself.",
                "Today's practice is tomorrow's victory.",
                "Champions are made when no one is watching."
            ]
            return random.choice(fallback_quotes)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        player = self.request.user.player
        today = datetime.now().date()
        context['today'] = today
        context['player'] = player

        # Timeline Events
        context['timeline_events'] = player.timeline_events.all()
        
        # Current Tournaments
        current_tournaments = Tournament.objects.filter(
            player_tournaments__player=player,
            status__in=['AC', 'CI']  # In Action or Check-in Open
        ).prefetch_related('player_tournaments')
        
        if not current_tournaments.exists():
            # Recommended tournaments if no current ones
            recommended_tournaments = Tournament.objects.filter(
                status='CI',
                city=player.city,
                gender__in=[player.gender, 'X'],
                category=player.category
            )[:3]
            context['recommended_tournaments'] = recommended_tournaments
            context['current_tournaments'] = []
        else:
            context['current_tournaments'] = current_tournaments
            context['recommended_tournaments'] = []
        
        # Upcoming Matches
        context['upcoming_matches'] = Match.objects.filter(
            Q(players=player),
            status__in=['PD', 'OD']  # Pending or Opponent Done
        ).select_related('tournament')
        
        # Current Season Stats
        current_stats, created = PlayerCurrentStats.objects.get_or_create(player=player)
        if created:
            current_stats.save()
        context['current_stats'] = current_stats
        context['season_stats'] = player.season_stats.all()\
            .order_by('-season', '-week')\
            .distinct('season')\
            .order_by('-season')
        
        context['new_reviews_amount'] = player.feedback_attributes.filter(date__gte=today - timedelta(days=7)).count()
        
        # Review Tokens
        context['review_tokens'] = ReviewToken.objects.filter(
            from_player=player,
            is_used=False,
            created_at__gte=today - timedelta(days=14)
        )
        
        context['inspiration_quote'] = self.get_inspiration_quote()

        match_results_to_approve = PlayerMatch.objects.filter(
            player=player,
            is_approved_by_player=False,
            is_approved_by_opponent=True
        )
        context['match_results_to_approve'] = match_results_to_approve
        context['onboarding'] = PlayerOnboarding.objects.get(player=self.request.user.player)
        return context

@staff_member_required
def switch_user(request, user_id):
    """Позволяет админу быстро переключиться на другого пользователя"""
    if request.user.is_staff:
        try:
            # Сначала сохраняем ID админа
            admin_id = request.user.id
            
            # Получаем пользователя, на которого переключаемся
            user = User.objects.get(id=user_id)
            
            # Отладочные сообщения
            messages.info(request, f'Current session keys: {list(request.session.keys())}')
            messages.info(request, f'Trying to save admin_id: {admin_id}')
            
            # Выполняем вход под новым пользователем
            backend = 'django.contrib.auth.backends.ModelBackend'
            user.backend = backend
            login(request, user)
            
            # Сохраняем admin_id ПОСЛЕ логина
            request.session['admin_user_id'] = admin_id
            request.session.modified = True
            request.session.save()
            
            # Проверяем после сохранения
            messages.info(request, f'Session keys after login and save: {list(request.session.keys())}')
            messages.info(request, f'admin_user_id in session: {request.session.get("admin_user_id")}')
            
            messages.success(request, f'Switched to user: {user.get_full_name()}')
            
        except User.DoesNotExist:
            messages.error(request, 'User not found')
            
    return redirect('rivals:today')

@staff_member_required
def switch_back_to_admin(request):
    """Возвращает админа в его аккаунт"""
    admin_id = request.session.get('admin_user_id')
    
    if not admin_id:
        messages.error(request, 'No admin ID found in session')
        return redirect('rivals:today')
    
    try:
        admin_user = User.objects.get(id=admin_id)
        backend = 'django.contrib.auth.backends.ModelBackend'
        admin_user.backend = backend
        login(request, admin_user)
        
        # Безопасное удаление ключа из сессии
        if 'admin_user_id' in request.session:
            del request.session['admin_user_id']
            request.session.modified = True
            request.session.save()
        
        messages.success(request, f'Switched back to admin: {admin_user.get_full_name()}')
        
    except User.DoesNotExist:
        messages.error(request, f'Admin user with ID {admin_id} not found')
        # Очищаем невалидный admin_user_id
        if 'admin_user_id' in request.session:
            del request.session['admin_user_id']
            request.session.modified = True
            request.session.save()
    
    return redirect('rivals:today')

@login_required
@require_http_methods(["POST"])
def create_ticket(request):
    form = TicketForm(request.POST)
    if form.is_valid():
        ticket = form.save(commit=False)
        ticket.user = request.user
        ticket.save()
        messages.success(request, 'Your message has been sent successfully!')
    else:
        messages.error(request, 'Error sending message. Please try again.')
    return redirect(request.META.get('HTTP_REFERER', '/'))

@staff_member_required
def reopen_ticket(request, ticket_id):
    ticket = Ticket.objects.get(id=ticket_id)
    ticket.reopen_ticket()
    messages.success(request, 'Ticket has been reopened.')
    return redirect('rivals:admin_test')

@staff_member_required
def close_ticket(request, ticket_id):
    ticket = Ticket.objects.get(id=ticket_id)
    ticket.close_ticket()
    messages.success(request, 'Ticket has been closed.')
    return redirect('rivals:admin_test')

@staff_member_required
def check_overdue_matches(request):
    count = check_overdue_matches_service()
    if count:
        messages.success(request, f"Overdue matches checked. {count} matches found")
    else:
        messages.warning(request, "No overdue matches found")
    return redirect('rivals:admin_test')

@login_required
@is_tournament_participant_and_has_match_overdue_or_admin
def stage_prolongation_request(request, pk):
    tournament = get_object_or_404(Tournament, pk=pk)
    if request.method == 'POST':
        form = StageProlongationRequestForm(
            request.POST,
            player=request.user.player,
            tournament=tournament
        )
        if form.is_valid():
            form.save()
            messages.success(request, 'Stage prolongation request has been sent.')
            return redirect('rivals:tournament_detail', pk=pk)
    else:
        form = StageProlongationRequestForm(
            player=request.user.player,
            tournament=tournament
        )
    return render(request, 'rivals/stage_prolongation_request.html', {'form': form, 'tournament': tournament})

@staff_member_required
def approve_stage_prolongation_request(request, pk):
    stage_prolongation_request = StageProlongationRequest.objects.get(id=pk)
    stage_prolongation_request.approve()
    return redirect('rivals:admin_test')

@staff_member_required
def reject_stage_prolongation_request(request, pk):
    stage_prolongation_request = StageProlongationRequest.objects.get(id=pk)
    stage_prolongation_request.reject()
    return redirect('rivals:admin_test')

@staff_member_required
def reopen_stage_prolongation_request(request, pk):
    stage_prolongation_request = StageProlongationRequest.objects.get(id=pk)
    stage_prolongation_request.reopen()
    return redirect('rivals:admin_test')

def generate_verification_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

''' === СТАРЫЙ ВИЗАРД === 

class PlayerWizardView(LoginRequiredMixin, View):
    template_name = 'rivals/player_wizard.html'
    add_telegram_form_class = AddTelegramUsernameForm
    player_profile_form_class = PlayerWizardForm
    manual_code_form_class = TelegramVerificationForm

    def get_verification_context(self, user, verification):
        """Готовит контекст для отображения опций верификации (Шаг 2)."""
        context = {}
        bot_username = settings.TELEGRAM_BOT_USERNAME
        if bot_username and verification.verification_code:
            start_param = f"verify_{verification.verification_code}"
            deep_link = f"https://t.me/{bot_username}?start={start_param}"
            context['deep_link'] = deep_link

            try:
                qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
                qr.add_data(deep_link)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                qr_code_base64 = base64.b64encode(buffer.getvalue()).decode()
                context['qr_code_base64'] = qr_code_base64
            except Exception as e:
                 logger.error(f"Error generating QR code for user {user.email}: {e}")
                 # В случае ошибки, устанавливаем None, чтобы кнопка "Retry" появилась
                 context['qr_code_base64'] = None # <--- Убедитесь, что здесь None при ошибке
                 context['qr_code_error'] = True # Можно добавить флаг ошибки

            context['telegram_bot_name'] = bot_username
            # Используем имя из verification, если оно там есть, иначе из user.telegram
            context['telegram_username_current'] = verification.telegram_username or user.telegram
        else:
            logger.warning(f"Cannot generate verification links for user {user.email}. Bot username or code missing.")
            context['deep_link'] = None
            context['qr_code_base64'] = None
            context['telegram_bot_name'] = None
            context['telegram_username_current'] = verification.telegram_username or user.telegram # Все равно покажем имя
            context['qr_code_error'] = True # Добавляем флаг ошибки

        context['manual_code_form'] = self.manual_code_form_class()
        return context

    def get(self, request, *args, **kwargs):
        user = request.user
        context = {}
        player, player_created = Player.objects.get_or_create(user=user)
        # onboarding, onboarding_created = PlayerOnboarding.objects.get_or_create(player=player)

        # Определяем ЛОГИЧЕСКИЙ этап
        if user.is_telegram_verified:
            stage = 2
        else:
            stage = 1

        context['stage'] = stage

        # Определяем ВИЗУАЛЬНЫЙ шаг
        visual_step = 1
        if stage == 1:
            verification, verification_created = TelegramVerification.objects.get_or_create(
                user=user,
                defaults={'verification_code': generate_verification_code()} # Используем локальную функцию
            )
            if not verification.verification_code:
                 verification.verification_code = generate_verification_code() # Используем локальную функцию
                 verification.save()

            current_telegram_username = verification.telegram_username or user.telegram # Ищем имя в двух местах
            if current_telegram_username:
                visual_step = 2
                context.update(self.get_verification_context(user, verification))
            else:
                visual_step = 1

            context['add_telegram_form'] = self.add_telegram_form_class(
                 initial={'telegram_username': current_telegram_username}
            )

        elif stage == 2:
            visual_step = 3
            context['profile_form'] = self.player_profile_form_class(instance=player, user=user)

        context['visual_step'] = visual_step
        logger.info(f"User {user.email} GET request. Stage: {stage}, Visual Step: {visual_step}")

        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        user = request.user
        player = get_object_or_404(Player, user=user)
        # Получаем или создаем объект верификации СРАЗУ
        verification, _ = TelegramVerification.objects.get_or_create(
            user=user,
            defaults={'verification_code': generate_verification_code()}
        )
        context = {}

        action = None
        if 'submit_telegram_username' in request.POST:
            action = 'submit_username'
            current_visual_step = 1
        elif 'submit_verification_code' in request.POST:
            action = 'submit_code'
            current_visual_step = 2
        elif 'check_verification_status' in request.POST:
            action = 'check_status'
            current_visual_step = 2
        # === НОВАЯ ПРОВЕРКА ===
        elif 'retry_verification_generation' in request.POST:
            action = 'retry_generation'
            current_visual_step = 2
        # =====================
        elif 'submit_profile' in request.POST:
            action = 'submit_profile'
            current_visual_step = 5
        else:
             try:
                 current_visual_step = int(request.POST.get('visual_step', 1))
             except ValueError:
                 current_visual_step = 1
             action = 'unknown'

        logger.info(f"User {user.email} POST request. Action: {action}, Visual Step: {current_visual_step}")

        # --- Обработка Шага 1: Отправка ника ---
        if action == 'submit_username':
            # ... (ваш существующий код для submit_username, НО используйте verification из начала post) ...
            # Замените все verification = ... на просто использование verification
            # Пример изменения:
            context['visual_step'] = 1
            add_telegram_form = self.add_telegram_form_class(request.POST)
            if add_telegram_form.is_valid():
                tg_username = add_telegram_form.cleaned_data['telegram_username']
                # Проверка уникальности (оставляем как есть)
                if TelegramVerification.objects.filter(
                    telegram_username__iexact=tg_username
                    ).exclude(user=user).exists() or CustomUser.objects.filter(
                        telegram__iexact=tg_username # Проверяем и поле CustomUser.telegram
                        ).exclude(pk=user.pk).exists():
                     messages.error(request, f"Telegram username '@{tg_username}' is already associated with another account.")
                     context['add_telegram_form'] = add_telegram_form
                     context['stage'] = 1
                     return render(request, self.template_name, context)

                # Сохраняем ник и генерируем код
                user.telegram = tg_username # Сохраняем в CustomUser
                verification.telegram_username = tg_username # Сохраняем в TelegramVerification
                verification.is_verified = False
                user.is_telegram_verified = False
                verification.telegram_id = '' # Сбрасываем ID при смене ника
                # Генерируем НОВЫЙ код при смене ника
                verification.verification_code = generate_verification_code()
                user.save(update_fields=['telegram', 'is_telegram_verified'])
                verification.save()
                logger.info(f"Username @{tg_username} saved for {user.email}. New verification code generated.")

                # Отправляем код ботом (ваш код без изменений)
                if bot:
                    try:
                        chat_info = bot.get_chat(f"@{tg_username}")
                        if chat_info and chat_info.id:
                            message_text = f'Your Tennis Rivals verification code: *{verification.verification_code}*'
                            bot.send_message(chat_info.id, message_text, parse_mode='Markdown')
                            verification.telegram_id = str(chat_info.id) # Сохраняем ID
                            verification.save(update_fields=['telegram_id'])
                            messages.success(request, f"Verification code sent to @{tg_username}. Please check your Telegram or use other methods below.")
                        else:
                            logger.warning(f"Could not find Telegram user @{tg_username} to send the code.")
                            messages.warning(request, "Could not find Telegram user to send the code.")
                    except Exception as e:
                        logger.error(f"Error sending code via bot for {user.email}: {e}")
                        messages.warning(request, f"Could not send code to @{tg_username}. Please use QR/Link or manual code entry.")
                else:
                    messages.info(request, "Telegram bot not configured. Please use QR/Link or manual code entry.")

                return redirect('rivals:player_wizard')
            else:
                context['add_telegram_form'] = add_telegram_form
                context['stage'] = 1
                return render(request, self.template_name, context)

        # --- Обработка Шага 2: Отправка кода ---
        elif action == 'submit_code':
            # ... (ваш существующий код для submit_code, используйте verification из начала post) ...
            context['visual_step'] = 2
            context['stage'] = 1
            manual_code_form = self.manual_code_form_class(request.POST)
            if manual_code_form.is_valid():
                entered_code = manual_code_form.cleaned_data['verification_code']
                # Сверяем с кодом из объекта verification
                if verification.verification_code and verification.verification_code == entered_code:
                    verification.is_verified = True
                    verification.verified_at = now()
                    user.is_telegram_verified = True
                    verification.save()
                    user.save(update_fields=['is_telegram_verified'])
                    messages.success(request, "Telegram verified successfully!")
                    logger.info(f"Telegram manually verified for {user.email}")
                    return redirect('rivals:player_wizard')
                else:
                    messages.error(request, "Invalid verification code.")
                    logger.warning(f"Invalid manual code for user {user.email}")
            # Контекст для ререндера Шага 2
            context['manual_code_form'] = manual_code_form
            context['add_telegram_form'] = self.add_telegram_form_class(initial={'telegram_username': verification.telegram_username or user.telegram})
            context.update(self.get_verification_context(user, verification))
            return render(request, self.template_name, context)

        # --- Обработка Шага 2: Проверка статуса ---
        elif action == 'check_status':
             # ... (ваш существующий код для check_status, используйте user.is_telegram_verified) ...
            context['visual_step'] = 2
            context['stage'] = 1
            # Просто проверяем флаг на пользователе
            if user.is_telegram_verified:
                messages.success(request, "Verification confirmed! Proceed to the next step.")
                logger.info(f"Verification check successful for {user.email}")
                return redirect('rivals:player_wizard')
            else:
                messages.info(request, "Telegram verification is not completed yet. Please verify using the methods above or try checking again later.")
                logger.info(f"Verification check failed for {user.email}")
                # Контекст для ререндера Шага 2
                context['manual_code_form'] = self.manual_code_form_class()
                context['add_telegram_form'] = self.add_telegram_form_class(initial={'telegram_username': verification.telegram_username or user.telegram})
                context.update(self.get_verification_context(user, verification))
                return render(request, self.template_name, context)

        # === НОВЫЙ ОБРАБОТЧИК ===
        elif action == 'retry_generation':
            context['visual_step'] = 2 # Остаемся на шаге 2
            context['stage'] = 1      # Остаемся на этапе верификации
            logger.info(f"User {user.email} attempting retry verification generation.")

            # Убедимся, что код верификации существует
            if not verification.verification_code:
                verification.verification_code = generate_verification_code()
                verification.save()
                logger.info(f"Generated new verification code for {user.email} during retry.")

            # Здесь НЕ пытаемся снова отправить код ботом, т.к. проблема
            # скорее всего была в генерации QR/ссылки. GET-запрос сам
            # перегенерирует контекст с QR/ссылкой.

            messages.info(request, "Attempting to regenerate verification options...")
            # Просто перенаправляем на GET-обработчик этого же визарда.
            # GET-обработчик вызовет get_verification_context и отобразит
            # актуальные данные (включая QR/ссылку, если генерация теперь удастся).
            return redirect('rivals:player_wizard')
        # ========================

        # --- Обработка Шага 5: Отправка профиля ---
        elif action == 'submit_profile':
            # ... (ваш существующий код для submit_profile) ...
            context['visual_step'] = 5
            context['stage'] = 2
            if not user.is_telegram_verified:
                messages.error(request, "Telegram verification is required before submitting profile.")
                return redirect('rivals:player_wizard')

            profile_form = self.player_profile_form_class(
                request.POST, request.FILES, instance=player, user=user
            )
            if profile_form.is_valid():
                # Сохраняем профиль и устанавливаем is_new=False внутри формы
                saved_player = profile_form.save()
                messages.success(request, "Your profile has been completed successfully!")
                logger.info(f"Profile saved and is_new set to False for user {user.email}")
                # Возможно, стоит проверить PlayerOnboarding и завершить его
                try:
                    onboarding = PlayerOnboarding.objects.get(player=saved_player)
                    onboarding.is_completed = True
                    onboarding.completed_at = now()
                    onboarding.save()
                except PlayerOnboarding.DoesNotExist:
                    pass # Или создать его как завершенный

                return redirect('rivals:today')
            else:
                logger.warning(f"Profile form errors for user {user.email}: {profile_form.errors.as_json()}")
                context['profile_form'] = profile_form
                # Передаем stage и visual_step для корректного ререндера
                return render(request, self.template_name, context)

        # --- Неизвестное действие ---
        else:
            messages.error(request, "Invalid action submitted.")
            logger.warning(f"Unknown POST action for user {user.email}")
            return redirect('rivals:player_wizard')

'''

# === НОВЫЕ VIEWS ДЛЯ ВИЗАРДА ===

class PlayerWizardBaseView(LoginRequiredMixin, FormView):
    """Базовый класс для шагов визарда"""
    success_url = None # Будет определен в каждом шаге
    template_name = 'rivals/player_wizard_step.html' # Используем общий шаблон для начала

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['step'] = self.step # Добавляем номер шага в контекст
        # Добавим данные из сессии для отображения в шаблоне (если нужно)
        context['wizard_data'] = self.request.session.get('wizard_data', {})
        return context

    def form_valid(self, form):
        # Сохраняем данные шага в сессию
        wizard_data = self.request.session.get('wizard_data', {})
        wizard_data[f'step{self.step}_data'] = form.cleaned_data
        self.request.session['wizard_data'] = wizard_data
        self.request.session.modified = True # Помечаем сессию как измененную
        logger.info(f"Wizard step {self.step} data saved to session for user {self.request.user.email}")
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
         # Проверка доступа к шагу (базовая)
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        # Более сложная проверка доступа будет в каждом конкретном view
        return super().dispatch(request, *args, **kwargs)


class PlayerWizardStep1View(PlayerWizardBaseView):
    """Шаг 1: Ввод ника Telegram"""
    form_class = AddTelegramUsernameForm
    success_url = reverse_lazy('rivals:player_wizard_step2')
    step = 1

    def get_initial(self):
        """Заполняем форму из сессии или текущими данными пользователя"""
        initial = super().get_initial()
        wizard_data = self.request.session.get('wizard_data', {})
        step_data = wizard_data.get(f'step{self.step}_data', {})
        initial['telegram_username'] = step_data.get(
            'telegram_username',
            self.request.user.telegram or getattr(self.request.user, 'telegramverification', None) # Пробуем получить имя
        )
        return initial

    def form_valid(self, form):
        # Выполняем действия Шага 1 (сохранение ника, отправка кода) ПЕРЕД сохранением в сессию
        user = self.request.user
        verification, _ = TelegramVerification.objects.get_or_create(user=user)
        tg_username = form.cleaned_data['telegram_username']

        # Проверка уникальности (важно!)
        if TelegramVerification.objects.filter(
            telegram_username__iexact=tg_username
            ).exclude(user=user).exists() or CustomUser.objects.filter(
                telegram__iexact=tg_username # Проверяем и поле CustomUser.telegram
                ).exclude(pk=user.pk).exists():
             messages.error(self.request, f"Telegram username '@{tg_username}' is already associated with another account.")
             return self.form_invalid(form) # Возвращаем с ошибкой

        # Сохраняем ник и генерируем код
        user.telegram = tg_username # Сохраняем в CustomUser
        verification.telegram_username = tg_username # Сохраняем в TelegramVerification
        verification.is_verified = False
        user.is_telegram_verified = False
        verification.telegram_id = '' # Сбрасываем ID при смене ника
        # Генерируем НОВЫЙ код при смене ника
        verification.verification_code = generate_verification_code()
        user.save(update_fields=['telegram', 'is_telegram_verified'])
        verification.save()
        logger.info(f"Username @{tg_username} saved for {user.email}. New verification code generated.")

        # Отправляем код ботом (ваш код без изменений)
        if bot:
            try:
                chat_info = bot.get_chat(f"@{tg_username}")
                if chat_info and chat_info.id:
                    message_text = f'Your Tennis Rivals verification code: *{verification.verification_code}*'
                    bot.send_message(chat_info.id, message_text, parse_mode='Markdown')
                    verification.telegram_id = str(chat_info.id) # Сохраняем ID
                    verification.save(update_fields=['telegram_id'])
                    messages.success(self.request, f"Verification code sent to @{tg_username}. Please check your Telegram or use other methods below.")
                else:
                    logger.warning(f"Could not find Telegram user @{tg_username} to send the code.")
                    messages.warning(self.request, "Could not find Telegram user to send the code.")
            except Exception as e:
                logger.error(f"Error sending code via bot for {user.email}: {e}")
                messages.warning(self.request, f"Could not send code to @{tg_username}. Please use QR/Link or manual code entry.")
        else:
            messages.info(self.request, "Telegram bot not configured. Please use QR/Link or manual code entry.")

        # Теперь вызываем родительский form_valid для сохранения данных в сессию и редиректа
        return super().form_valid(form)

class PlayerWizardStep2View(PlayerWizardBaseView):
    """Шаг 2: Верификация Telegram (ручной ввод)"""
    # Пока сделаем только ручной ввод для простоты
    form_class = TelegramVerificationForm
    success_url = reverse_lazy('rivals:player_wizard_step3')
    step = 2
    template_name = 'rivals/player_wizard_step2.html' # Отдельный шаблон для Шага 2

    def dispatch(self, request, *args, **kwargs):
        # Проверка: нельзя попасть сюда, если не введен ник на шаге 1
        wizard_data = request.session.get('wizard_data', {})
        if 'step1_data' not in wizard_data or not wizard_data['step1_data'].get('telegram_username'):
            messages.warning(request, "Please enter your Telegram username first.")
            return redirect('rivals:player_wizard_step1')
        # Проверка: если уже верифицирован, пропускаем шаг
        if request.user.is_telegram_verified:
            logger.info(f"User {request.user.email} already verified, skipping step 2.")
            # Сохраним пустые данные для шага 2 в сессию, чтобы показать его как пройденный
            wizard_data['step2_data'] = {} # Пустой словарь как маркер прохождения
            request.session['wizard_data'] = wizard_data
            request.session.modified = True
            return redirect(self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """Добавляем контекст для QR/ссылки"""
        context = super().get_context_data(**kwargs)
        user = self.request.user
        verification = get_object_or_404(TelegramVerification, user=user)
        # Используем метод из старого view для генерации контекста QR/ссылки
        # Нужен экземпляр старого view или вынести get_verification_context
        context.update(PlayerWizardView().get_verification_context(user, verification)) # Немного хак, лучше вынести метод
        # Добавляем действия для формы
        context['form_actions'] = [
             {'name': 'submit_code', 'label': 'Verify Code & Continue', 'class': 'btn-primary'},
             {'name': 'retry_generation', 'label': 'Regenerate Options', 'class': 'btn-outline-secondary', 'formnovalidate': True},
             {'name': 'check_status', 'label': 'Check Status & Continue', 'class': 'btn-secondary'},
        ]
        return context

    def post(self, request, *args, **kwargs):
        """Обрабатываем разные кнопки: submit_code, retry_generation, check_status"""
        user = request.user
        verification = get_object_or_404(TelegramVerification, user=user)

        if 'submit_code' in request.POST:
            form = self.get_form()
            if form.is_valid():
                entered_code = form.cleaned_data['verification_code']
                if verification.verification_code and verification.verification_code == entered_code:
                    verification.is_verified = True
                    verification.verified_at = now()
                    user.is_telegram_verified = True
                    verification.save()
                    user.save(update_fields=['is_telegram_verified'])
                    messages.success(request, "Telegram verified successfully!")
                    logger.info(f"Telegram manually verified for {user.email}")
                    # Сохраняем данные формы (пустые) в сессию и редирект
                    return self.form_valid(form)
                else:
                    messages.error(request, "Invalid verification code.")
                    logger.warning(f"Invalid manual code for user {user.email}")
                    return self.form_invalid(form)
            else:
                return self.form_invalid(form) # Показать ошибки формы

        elif 'retry_generation' in request.POST:
            logger.info(f"User {user.email} attempting retry verification generation.")
            if not verification.verification_code:
                verification.verification_code = generate_verification_code()
                verification.save()
            messages.info(request, "Attempting to regenerate verification options...")
            # Просто редирект на GET этого же шага
            return redirect('rivals:player_wizard_step2')

        elif 'check_status' in request.POST:
            # Обновляем статус пользователя из БД
            user.refresh_from_db()
            if user.is_telegram_verified:
                messages.success(request, "Verification confirmed! Proceed to the next step.")
                logger.info(f"Verification check successful for {user.email}")
                 # Имитируем успешную отправку формы (пустой) для перехода
                form = self.get_form_class()(data={}) # Создаем пустую валидную форму
                form.is_valid() # Помечаем как валидную
                return self.form_valid(form) # Сохраняем в сессию и редирект
            else:
                messages.info(request, "Telegram verification is not completed yet. Please verify using the methods above or try checking again later.")
                logger.info(f"Verification check failed for {user.email}")
                # Просто рендерим страницу заново с сообщением
                return self.get(request, *args, **kwargs)
        else:
            messages.error(request, "Invalid action.")
            return self.get(request, *args, **kwargs)


class PlayerWizardStep3View(PlayerWizardBaseView):
    """Шаг 3: Личная информация"""
    form_class = PlayerWizardForm # Используем большую форму, но покажем часть полей
    success_url = reverse_lazy('rivals:player_wizard_step4')
    step = 3
    template_name = 'rivals/player_wizard_step3.html'

    def dispatch(self, request, *args, **kwargs):
        # Проверка: нельзя попасть сюда, если не пройден шаг 2 (т.е. нет step2_data в сессии)
        wizard_data = request.session.get('wizard_data', {})
        if 'step2_data' not in wizard_data:
             # Дополнительно проверим флаг на пользователе, если сессия потерялась
            if not request.user.is_telegram_verified:
                messages.warning(request, "Please verify your Telegram account first.")
                return redirect('rivals:player_wizard_step2')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        """Передаем user в форму"""
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        # Получаем instance игрока, если он есть
        try:
             kwargs['instance'] = self.request.user.player
        except Player.DoesNotExist:
             kwargs['instance'] = None
        return kwargs

    def get_initial(self):
        """Заполняем форму из сессии"""
        initial = super().get_initial()
        wizard_data = self.request.session.get('wizard_data', {})
        # Заполняем поля этого шага из данных сессии предыдущих шагов, если они там есть
        step3_data = wizard_data.get('step3_data', {})
        initial.update(step3_data) # Перезапишет initial из get_form_kwargs, если есть в сессии
        return initial

    # form_valid унаследован от базового класса, просто сохранит данные в сессию

class PlayerWizardStep4View(PlayerWizardBaseView):
    """Шаг 4: Теннисный профиль и навыки"""
    form_class = PlayerWizardForm # Используем большую форму, но покажем другую часть полей
    success_url = reverse_lazy('rivals:player_wizard_step5')
    step = 4
    template_name = 'rivals/player_wizard_step4.html'

    def dispatch(self, request, *args, **kwargs):
        # Проверка: нельзя попасть сюда, если не пройден шаг 3
        wizard_data = request.session.get('wizard_data', {})
        if 'step3_data' not in wizard_data:
            messages.warning(request, "Please complete the previous step first.")
            return redirect('rivals:player_wizard_step3')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        """Передаем user и instance в форму"""
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        try:
             kwargs['instance'] = self.request.user.player
        except Player.DoesNotExist:
             kwargs['instance'] = None
        return kwargs

    def get_initial(self):
        """Заполняем форму из сессии"""
        initial = super().get_initial()
        wizard_data = self.request.session.get('wizard_data', {})
        step4_data = wizard_data.get('step4_data', {})
        initial.update(step4_data)
        return initial

    # form_valid унаследован от базового класса

class PlayerWizardStep5View(LoginRequiredMixin, TemplateView):
    """Шаг 5: Обзор и отправка"""
    template_name = 'rivals/player_wizard_step5.html'
    step = 5

    def dispatch(self, request, *args, **kwargs):
         # Проверка: нельзя попасть сюда, если не пройден шаг 4
        wizard_data = request.session.get('wizard_data', {})
        if 'step4_data' not in wizard_data:
            messages.warning(request, "Please complete the previous step first.")
            return redirect('rivals:player_wizard_step4')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['step'] = self.step
        wizard_data = self.request.session.get('wizard_data', {})
        all_data = {}
        for key, value in wizard_data.items():
             if isinstance(value, dict):
                 all_data.update(value)
        context['wizard_data'] = all_data

        # Создаем форму аватара для рендеринга
        context['avatar_form'] = PlayerWizardAvatarForm() # <--- НОВАЯ ФОРМА

        # --- Расчет NTRP и категории (как было) ---
        try: player_instance = self.request.user.player
        except Player.DoesNotExist: player_instance = None
        review_form = PlayerWizardForm(data=all_data, instance=player_instance, user=self.request.user)
        if review_form.is_bound and review_form.is_valid():
            context['calculated_ntrp_int'] = review_form.calculate_ntrp()
            context['calculated_ntrp'] = context['calculated_ntrp_int'] / 1000.0 # Для отображения 3.5
            context['calculated_category'] = review_form.determine_category(context['calculated_ntrp_int'])
        else:
             logger.warning(f"Wizard data seems invalid at review step for user {self.request.user.email}. Errors: {review_form.errors.as_json()}")
             context['calculated_ntrp_int'] = 0
             context['calculated_ntrp'] = 0.0
             context['calculated_category'] = 'N/A'
             messages.error(self.request, "There was an issue with the data collected. Please review previous steps.")
        # --- Конец расчета ---

        # --- Отображаемые значения (как было) ---
        context['city_display'] = dict(TR_CITIES).get(all_data.get('city'))
        context['gender_display'] = dict(GENDER).get(all_data.get('gender'))
        context['experience_display'] = dict(PlayerWizardForm.EXPERIENCE_CHOICES).get(int(all_data.get('experience_level', 0)))
        context['frequency_display'] = dict(PlayerWizardForm.FREQUENCY_CHOICES).get(int(all_data.get('playing_frequency', 0)))
        context['technical_display'] = dict(PlayerWizardForm.LEVEL_CHOICES).get(int(all_data.get('technical_level', 0)))
        context['serve_display'] = dict(PlayerWizardForm.SERVE_CHOICES).get(int(all_data.get('serve_level', 0)))
        context['match_display'] = dict(PlayerWizardForm.MATCH_CHOICES).get(int(all_data.get('match_experience', 0)))
        # --- Конец отображаемых значений ---

        return context

    def post(self, request, *args, **kwargs):
        wizard_data = request.session.get('wizard_data', {})
        all_data = {}
        for key, value in wizard_data.items():
            if isinstance(value, dict):
                all_data.update(value)

        # Получаем или создаем игрока
        player, created = Player.objects.get_or_create(user=request.user)

        # Создаем и валидируем ОСНОВНУЮ форму с данными из сессии
        final_form = PlayerWizardForm(data=all_data, instance=player, user=request.user)
        # Создаем и валидируем ФОРМУ АВАТАРА с данными из POST и FILES
        avatar_form = PlayerWizardAvatarForm(request.POST, request.FILES) # <--- НОВАЯ ФОРМА

        # Проверяем обе формы
        if final_form.is_valid() and avatar_form.is_valid():
            try:
                # 1. Сохраняем основные данные (без аватара)
                # commit=True здесь сохранит User, Player, Stats
                saved_player = final_form.save(commit=True)

                # 2. Сохраняем аватар, если он был загружен
                avatar = avatar_form.cleaned_data.get('avatar')
                if avatar:
                    saved_player.avatar = avatar
                    saved_player.save(update_fields=['avatar']) # Обновляем только поле аватара
                    logger.info(f"Avatar updated for wizard user {request.user.email}")

                # 3. Завершаем онбординг (как было)
                onboarding, _ = PlayerOnboarding.objects.get_or_create(player=saved_player)
                onboarding.is_completed = True
                onboarding.completed_at = now()
                onboarding.save()

                messages.success(request, "Your profile has been completed successfully!")
                logger.info(f"Wizard completed and profile saved for user {request.user.email}")
                request.session.pop('wizard_data', None)
                return redirect('rivals:today')

            except Exception as e:
                 logger.exception(f"Error during final save for wizard user {request.user.email}: {e}")
                 messages.error(request, "An error occurred while saving your profile. Please try again.")
                 # Остаемся на странице обзора с ошибкой
                 context = self.get_context_data(**kwargs)
                 # Передаем ошибки обеих форм
                 context['form_errors'] = final_form.errors | avatar_form.errors
                 return self.render_to_response(context)
        else:
            # Если хотя бы одна форма невалидна
            logger.error(f"Wizard final form invalid for user {request.user.email}: ProfileForm={final_form.errors.as_json()} AvatarForm={avatar_form.errors.as_json()}")
            messages.error(request, "There were errors in your profile data. Please go back and correct them or re-upload the avatar if needed.")
             # Остаемся на странице обзора с ошибкой
            context = self.get_context_data(**kwargs)
            # Передаем ошибки обеих форм и сами формы для ререндера
            context['form_errors'] = final_form.errors | avatar_form.errors
            context['avatar_form'] = avatar_form # Передаем невалидную форму аватара обратно
            return self.render_to_response(context)

# === КОНЕЦ НОВЫХ VIEWS ===





def activate_onboarding(request):
    player = request.user.player
    onboarding, created = PlayerOnboarding.objects.get_or_create(player=player)
    return redirect('rivals:today')