from django.db import models
from persons.models import CustomUser
from .const import TOURNAMENT_GENDER, TOURNAMENT_FORMAT, TOURNAMENT_CATEGORY, TOURNAMENT_STATUS, TOURNAMENT_TYPE, GENDER, TOKEN_STATUS
from .const import DELETED_PAIR, MATCH_STATUS, TR_GEOS, CURRENT_SEASON, TR_CITIES
from .const import TIMELINE_EVENT_TYPE, TIMELINE_EVENT_COLOR

from datetime import date, datetime, timedelta
from django.utils.timezone import now
from django.utils.functional import cached_property
from django.contrib import messages
from django.shortcuts import redirect
from django.http import request
from django.core.cache import cache
from django.utils import timezone

import math, random
from django.db.models import Sum, F
from django.urls import reverse
from django.core.exceptions import ObjectDoesNotExist
from django.contrib.auth import get_user_model


def today():
    return date.today()
def tomorrow():
    return datetime.now().date() + timedelta(days=1)
def next_monday():
    next_week = tomorrow() + timedelta(days=7)
    days_until_monday = (7 - next_week.weekday()) % 7
    return next_week + timedelta(days=days_until_monday)
def current_week():
    return date.today().isocalendar()[1]


class Player(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='player')
    first_name = models.CharField(max_length=150,default='')
    last_name = models.CharField(max_length=150,default='')
    birthdate = models.DateField(null=True)
    gender = models.CharField(max_length=1, choices=GENDER, null=True)
    geo = models.CharField(max_length=2, default='GE')
    city = models.CharField(max_length=3, default='TBI')
    height = models.FloatField(null=True)
    weight = models.FloatField(null=True)
    tennis_exp_year = models.PositiveIntegerField(null=True)
    availability = models.TextField(null=True)

    player_since = models.DateField(auto_now_add=True)
    is_new = models.BooleanField(default=True)
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    is_fake = models.BooleanField(default=False)

   

    category = models.CharField(
        max_length=2,
        choices=TOURNAMENT_CATEGORY,
        null=True,
        default='C0'
    )

    def save(self, *args, **kwargs):
        if self.user:
            self.first_name = self.user.first_name
            self.last_name = self.user.last_name
        super().save(*args, **kwargs)
        # Инвалидируем кэш при изменении
        cache.delete(f'player_name_{self.pk}')


    @property
    def age(self):
        if self.birthdate:
            today = date.today()
            return today.year - self.birthdate.year - (
                (today.month, today.day) < (self.birthdate.month, self.birthdate.day)
            )
        return None

    @property
    def tennis_exp_years(self):
        if self.tennis_exp_year:
            return date.today().year - self.tennis_exp_year
        return 0

   
    
    
    def get_recent_matches(self, count=5):
        return self.matches_as_player.select_related('match__tournament').order_by('-match__date')[:count]

    def update_rating(self, points):
        self.rating_pts += points
        self.save()

    def has_contact_info(self):
        return bool(self.user.mobile and self.user.email)
    
    class Meta:
        ordering = ['user__last_name', 'user__first_name']

        
    def get_full_name(self):
        cache_key = f'player_name_{self.pk}'
        name = cache.get(cache_key)
        if name is None:
            name = f"{self.first_name} {self.last_name}"
            cache.set(cache_key, name, timeout=3600)
        return name

    def get_short_name(self):
        cache_key = f'player_short_name_{self.pk}'
        short_name = cache.get(cache_key)
        if short_name is None:
            short_name = f"{self.first_name[0]}. {self.last_name}"
            cache.set(cache_key, short_name, timeout=3600)
        return short_name
    
    def __str__(self):
        return self.get_full_name()

    def get_avatar_url(self):
        if self.avatar and hasattr(self.avatar, 'url'):
            return self.avatar.url
        return '/static/assets/img/Avatar Default Icon.svg' 

def get_deleted_player():
    from persons.models import CustomUser
    # Используем get_or_create для атомарного получения или создания пользователя-заглушки
    deleted_user, user_created = CustomUser.objects.get_or_create(
        username='deleted_player',
        defaults={
            'email': 'deleted@example.com',
            'first_name': 'Deleted',
            'last_name': 'Player',
            'preferred_city': 'DEL',
            # Добавьте здесь другие поля с их значениями по умолчанию,
            # если они нужны для создания пользователя.
            # ВАЖНО: Не устанавливайте 'password' здесь,
            # get_or_create не использует create_user.
            # Мы установим непригодный пароль ниже, если пользователь только что создан.
        }
    )

    # Если пользователь только что был создан, установим непригодный пароль
    if user_created:
        deleted_user.set_unusable_password()
        deleted_user.save(update_fields=['password'])

    # Используем get_or_create для атомарного получения или создания профиля игрока-заглушки
    deleted_player, player_created = Player.objects.get_or_create(
        user=deleted_user,
        defaults={
            'gender': 'X',
            'category': 'D0',
            
        }
    )

    return deleted_player

class Attributes(models.Model):
    forehand = models.FloatField(default=0)
    backhand = models.FloatField(default=0)
    rallies = models.FloatField(default=0)
    attack = models.FloatField(default=0)
    serve = models.FloatField(default=0)
    serve_return = models.FloatField(default=0)
    volleys = models.FloatField(default=0)
    summary = models.FloatField(default=0)

    def save(self, *args, **kwargs):
        self.summary = round(((self.forehand + self.backhand + self.rallies + self.attack + self.serve + self.serve_return + self.volleys) / 7), 1)
        super().save(*args, **kwargs)

    class Meta:
        abstract = True

class PlayerFeedbackAttributes(Attributes):
    player = models.ForeignKey('Player', on_delete=models.CASCADE, related_name='feedback_attributes')
    from_player = models.ForeignKey('Player', on_delete=models.CASCADE, related_name='feedback_attributes_from')
    date = models.DateTimeField(auto_now_add=True)

class PlayerAttributes(Attributes):
    player = models.OneToOneField('Player', on_delete=models.CASCADE, related_name='attributes')
    responses_amount = models.PositiveIntegerField(default=0)

    def update_attributes(self):
        all_responses = PlayerFeedbackAttributes.objects.filter(player=self.player)

        # Обновляем количество отзывов
        self.responses_amount = all_responses.count()

        # Суммируем все атрибуты из отзывов
        forehand_sum = all_responses.aggregate(Sum('forehand'))['forehand__sum'] or 0
        backhand_sum = all_responses.aggregate(Sum('backhand'))['backhand__sum'] or 0
        rallies_sum = all_responses.aggregate(Sum('rallies'))['rallies__sum'] or 0
        attack_sum = all_responses.aggregate(Sum('attack'))['attack__sum'] or 0
        serve_sum = all_responses.aggregate(Sum('serve'))['serve__sum'] or 0
        serve_return_sum = all_responses.aggregate(Sum('serve_return'))['serve_return__sum'] or 0
        volleys_sum = all_responses.aggregate(Sum('volleys'))['volleys__sum'] or 0

        # Обновляем средние значения атрибутов
        self.forehand = round((forehand_sum) / self.responses_amount, 1)
        self.backhand = round((backhand_sum) / self.responses_amount, 1)
        self.rallies = round((rallies_sum) / self.responses_amount, 1)
        self.attack = round((attack_sum) / self.responses_amount, 1)
        self.serve = round((serve_sum) / self.responses_amount, 1)
        self.serve_return = round((serve_return_sum) / self.responses_amount, 1)
        self.volleys = round((volleys_sum) / self.responses_amount, 1)

        # Сохраняем изменения
        self.save()

    def __str__(self):
        return f"Attributes of {self.player}"

class Match(models.Model):
    tournament = models.ForeignKey('Tournament', on_delete=models.CASCADE, related_name='matches')
    tournament_stage = models.SmallIntegerField(null=True, blank=True)
    match_type = models.CharField(max_length=1, choices=TOURNAMENT_FORMAT, default='S')
    
    players = models.ManyToManyField(
        'Player',
        through='PlayerMatch',
        through_fields=('match', 'player'),
        related_name='matches_as_player'
    )
    pairs = models.ManyToManyField(
        'Pair',
        through='PairMatch',
        through_fields=('match', 'pair'),
        related_name='matches_as_pair'
    )

    date = models.DateField(null=True, blank=True)
    court = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=2, choices=MATCH_STATUS, default='PD')

    def get_str_score(self):
        players = self.players.all().order_by('pk')
        if (self.status == 'FI' or self.status == 'CA'):
            try:
                player_match = self.player_matches.all().order_by('player__pk').first()
                opponent_match = self.player_matches.all().order_by('player__pk').last()
                total_score = 'Game, Set and Match '
                if player_match.is_winner:
                    total_score += str(player_match.player) + '. '
                elif opponent_match.is_winner:
                    total_score += str(opponent_match.player) + '. '
                else:
                    total_score = ''
                
                
                
                for i in range(len(player_match.score)):
                    player_set_score = player_match.score[i]
                    opponent_set_score = opponent_match.score[i]
                    if player_set_score is not None:
                        total_score += f'{player_set_score}'
                    
                    if opponent_set_score is not None:
                        total_score += f'-{opponent_set_score}; '
                    
                   
                return total_score[:-2]
            except Exception as e:
                return 'N/A'+str(e)
  
        else:
            return ['N/A']

class Tournament(models.Model):
    title = models.CharField(max_length=150, null=True)
    type = models.CharField(max_length=2, choices=TOURNAMENT_TYPE, default='RM')
    category = models.CharField(max_length=2, choices=TOURNAMENT_CATEGORY)
    gender = models.CharField(max_length=1, choices=TOURNAMENT_GENDER)
    format = models.CharField(max_length=2, choices=TOURNAMENT_FORMAT)
    geo = models.CharField(max_length=2, choices=TR_GEOS, default='GE')
    city = models.CharField(max_length=3, choices=TR_CITIES, default='TBI')
    status = models.CharField(max_length=2, choices=TOURNAMENT_STATUS, default='CI')

    description = models.TextField(null=True)

    image = models.ImageField(upload_to='tournament_images/', null=True)

    start_date = models.DateField(null=True)
    date_created = models.DateField(auto_now_add=True)

    current_stage = models.SmallIntegerField(default=0)
    current_stage_start_date = models.DateField(null=True)
    current_stage_end_date = models.DateField(null=True)

    players = models.ManyToManyField('Player', through='PlayerTournament')
    pairs = models.ManyToManyField('Pair', through='PairTournament')

    players_num = models.PositiveSmallIntegerField()
    players_enrolled = models.PositiveSmallIntegerField(default=0)


    def __str__(self):
        return f'{self.title}: {self.city} {self.gender} {self.category} {self.format} {self.type}'

    @property
    def ref(self):
        return self.geo + '-' + '0'*(5-len(str(self.pk)))+str(self.pk)


    @property
    def players_left(self):
        return self.players_num-self.players_enrolled


    @property
    def stages_max(self):
        if self.type == 'RM':
            return math.ceil(
                math.log(self.players_num, 2))  # округляем вверх, если участников больше, чем ближайшая степень двойки
        elif self.type == 'GM':
            return self.players_num - 1
        elif self.type == 'OS':
            return math.ceil(math.log(self.players_num,
                                      2)) + 2  # для формата n групп по 4, из каждой выходит 2 и дальше играют навылет

    @property
    def stage_progress(self):
        if self.status == 'AC':
            return round((self.current_stage / (self.stages_max + 1)) * 100)
        elif self.status == 'FI':
            return 100
        else:
            return 0
        
    @property
    def checkin_progress(self):
        if self.status == 'CI':
            return round((self.players_enrolled / self.players_num) * 100)
        else:
            return 100
            

    def player_singles_chekin(self, player):
        if self.format == 'S':
            if self.status == 'CI' and self.players_enrolled < self.players_num:
                if self.gender == 'X' or self.gender == player.gender:
                    self.players.add(player)
                    self.players_enrolled += 1
                    self.save()
                    if self.players_enrolled == self.players_num:
                        self.start()

    def player_doubles_checkin_as_pair(self, pair):  #TODO
        pass

    def player_doubles_checkin_as_single(self, single):  # TODO
        pass

    def init_matches_singles(self):
        if self.type == 'RM':
            players = []
            if self.current_stage == 1:

                players = list(self.players.all())
                if players:
                    random.shuffle(players)
                else:
                    messages.warning(request, 'No players added. The current stage should be the 1st')

            elif self.current_stage > 1:
                players_unsorted = {}
                for player in self.players:
                    player_tournament = PlayerTournament.objects.get(player = player, tournament = self.pk)
                    players_unsorted[player] = int(player_tournament.flow)
                if players_unsorted:
                    players_sorted = dict(sorted(players_unsorted.items(), key=lambda item: item[1], reverse=True))
                    players = list(players_sorted.keys())
                else:
                    messages.warning(request, 'No players added. The current stage should be later than the 1st')
            else:
                messages.warning(request, 'Error with stage - singles - current_stage < 1')

            if players:
                self.players_add_in_RM(players)
            else:
                messages.warning(request, 'No players added. Error with stage - singles')

        if self.type == 'GM':

            players_sorted = list(self.players.all().order_by('pk'))
            self.players_add_in_GM(players_sorted, self.current_stage)

        elif self.type == 'OS':
            pass

        else:
            pass

    def players_add_in_RM(self, players):
        for i in range(0, len(players), 2):
            match = Match.objects.create(tournament=self, tournament_stage=self.current_stage)
            PlayerMatch.objects.get_or_create(player = players[i], opponent = players[i + 1], match = match)
            PlayerMatch.objects.get_or_create(player = players[i + 1], opponent = players[i], match = match)

            # match_player_one = PlayerMatch.objects.create(_player = players[i], _opponent = players[i+1], _match = match) # на всякий случай это оставлю тут, тк я не уверен, что плейерматч создается автоматом при добавлении плейера в матч
            # match_players_two = PlayerMatch.objects.create(_player = players[i+1], _opponent = players[i], _match = match) # по идее должно быть так, но если нет, тогда надо будет создавать вручную

    def players_add_in_GM(self, players, stage):
        # Предварительная проверка: убедимся, что список содержит хотя бы два элемента
        if len(players) < 2:
            messages.warning(request, "Not enough players to create a GM match.")
            return
        
        if stage >= len(players):
            messages.warning(request, "Stage is out of range.")
            return
        
        match = Match.objects.create(tournament=self, tournament_stage=stage)
        PlayerMatch.objects.get_or_create(player=players[-1], opponent=players[stage-1], match=match)
        PlayerMatch.objects.get_or_create(player=players[stage-1], opponent=players[-1], match=match)

        for i in range(1, len(players) // 2):
            a = (stage + i - 1) % (len(players) - 1) 
            b = (stage - i - 1 + len(players) - 1) % (len(players) - 1) 
            match = Match.objects.create(tournament=self, tournament_stage=stage)
            PlayerMatch.objects.get_or_create(player=players[a], opponent=players[b], match=match)
            PlayerMatch.objects.get_or_create(player=players[b], opponent=players[a], match=match)
                
                



    def init_matches_doubles(self):
        if self.current_stage == 1:
            pass
        elif self.current_stage > 1:
            pass
        else:
            messages.warning(request, 'Error with stage - doubles - current_stage < 1')

    def start(self):
        if self.players_enrolled == self.players_num:
            self.status = 'AC'
            self.current_stage = 1

            self.start_date = tomorrow()
            self.current_stage_start_date = tomorrow()
            self.current_stage_end_date = next_monday()

            if self.format == 'S':
                self.init_matches_singles()
            elif self.format == 'D':
                self.init_matches_doubles()

            self.save()

    def finish(self):
        pass

    def next_stage(self):
        self.current_stage += 1
        if self.current_stage > self.stages_max:
            self.save()
            self.finish()
        else:
            self._current_stage_start_date = tomorrow
            self._current_stage_end_date = next_monday

            if self.format == 'S':
                self.init_matches_singles()
            elif self.format == 'D':
                self.init_matches_doubles()

            self.save()

    def current_stage_extend(self, days_delta):
        if self._current_stage_end_date:
            self._current_stage_end_date += timedelta(days=days_delta)
            self.save()


class Pair(models.Model):
    player1 = models.ForeignKey(Player, on_delete=models.SET(get_deleted_player), null=True, related_name='pair_p1_set')
    player2 = models.ForeignKey(Player, on_delete=models.SET(get_deleted_player), null=True, related_name='pair_p2_set')
    gender = models.CharField(max_length=1, choices=TOURNAMENT_GENDER, null=True)
    pair_since = models.DateField(auto_now_add=True)

    is_complete = models.BooleanField(default=False)

    def add_player(self, player: Player):
        if not self.is_complete:
            if not self.player1:
                self.player1.add(player)
            elif not self.player2:
                self.player2.add(player)
                self.is_complete = True
                if self.player1.gender != self.player2.gender:
                    self.gender = 'X'
                else:
                    self.gender = self.player1.gender
                self.save()
            else:
                print(f'Impossible to add to this pair. Pair ID: {self.id}')
        else:
            print(f'Impossible to add to this pair. Pair ID: {self.id}')

class PlayerTournament(models.Model):
    player = models.ForeignKey(Player, on_delete=models.SET(get_deleted_player), related_name='player_tournaments')
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='player_tournaments')
    result = models.PositiveIntegerField(null=True)
    flow = models.CharField(max_length=8, default='')
    is_withdrawn = models.BooleanField(default=False)
    games_played = models.PositiveIntegerField(default=0)
    games_won = models.PositiveIntegerField(default=0)
    games_lost = models.PositiveIntegerField(default=0)
    games_rted = models.PositiveIntegerField(default=0)

class PairTournament(models.Model):
    pair = models.ForeignKey(Pair, on_delete=models.SET(DELETED_PAIR))
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE)
    result = models.PositiveIntegerField(null=True)
    flow = models.CharField(max_length=8, default='')

class PlayerMatch(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="player_matches")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="player_matches_as_player")
    opponent = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="player_matches_as_opponent")
    
    is_winner = models.BooleanField(null=True)
    is_withdrawn = models.BooleanField(default=False)
    opponent_is_withdrawn = models.BooleanField(default=False)

    NTRP_before_match = models.PositiveIntegerField(null=True)
    NTRP_change = models.IntegerField(null=True)
    season_pts_gained = models.SmallIntegerField(null=True)
    is_approved_by_player = models.BooleanField(default=False)
    is_approved_by_opponent = models.BooleanField(default=False)

    is_implemented = models.BooleanField(default=False)

    last_updated = models.DateTimeField(auto_now=True)

    set1_score = models.PositiveSmallIntegerField(default=0)
    set1_has_tiebreak = models.BooleanField(default=False)
    set1_tie_score = models.PositiveSmallIntegerField(null=True)
    set1_won = models.BooleanField(null=True)

    set2_score = models.PositiveSmallIntegerField(null=True)
    set2_has_tiebreak = models.BooleanField(default=False)
    set2_tie_score = models.PositiveSmallIntegerField(null=True)
    set2_won = models.BooleanField(null=True)

    set3_score = models.PositiveSmallIntegerField(null=True)
    set3_has_tiebreak = models.BooleanField(default=False)
    set3_is_tiebreak = models.BooleanField(default=False)
    set3_tie_score = models.PositiveSmallIntegerField(null=True)
    set3_won = models.BooleanField(null=True)

    

    def __str__(self):
        return f'{self.player} vs {self.opponent} in {self.match}'
    
    def save(self, *args, **kwargs):
        if not self.pk:  # Проверяем, новый ли это объект
            self.NTRP_before_match = self.player.current_stats.current_NTRP or 0
        self.geo = self.player.geo
        return super().save(*args, **kwargs)

    @property
    def submission_closed(self):
        return self.is_approved_by_opponent and self.is_approved_by_player

    @property
    def submitted(self):
        
        return self.is_approved_by_opponent or self.is_approved_by_player
    
    @property
    def score(self):
        result = []
        if self.is_withdrawn:
            result.append('-RT-')
            result.append('')
            result.append('')
        elif self.opponent_is_withdrawn:
            result.append('Tech Win')

        else:
            result.append(self.set1_score)
            if self.set1_has_tiebreak:
                result[0]= str(result[0])+f'({self.set1_tie_score})'
            result.append(self.set2_score)
            if self.set2_has_tiebreak:
                result[1]= str(result[1])+f'({self.set2_tie_score})'
            result.append(self.set3_score)
            if self.set3_has_tiebreak:
                result[2]= str(result[2])+f'({self.set3_tie_score})'
        
        return result
    
class PairMatch(models.Model):
    pair = models.ForeignKey('Pair', on_delete=models.SET(DELETED_PAIR), related_name='the_pair')
    opponent = models.ForeignKey('Pair', on_delete=models.SET(DELETED_PAIR), related_name='the_opponent_pair')
    match = models.ForeignKey('Match', on_delete=models.CASCADE, related_name='pair_matches')
    is_winner = models.BooleanField(null=True)
    is_withdrawn = models.BooleanField(default=False)

    season_pts_gained = models.PositiveIntegerField(null=True)

    set1_score = models.PositiveSmallIntegerField(default=0)
    set1_tie_score = models.PositiveSmallIntegerField(null=True)
    set1_won = models.BooleanField(null=True)

    set2_score = models.PositiveSmallIntegerField(null=True)
    set2_tie_score = models.PositiveSmallIntegerField(null=True)
    set2_won = models.BooleanField(null=True)

    set3_score = models.PositiveSmallIntegerField(null=True)
    set3_tie_score = models.PositiveSmallIntegerField(null=True)
    set3_won = models.BooleanField(null=True)

    _set3_is_tiebreak = models.BooleanField(null=True)

class PlayerSeasonStats(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='season_stats')
    season = models.PositiveIntegerField(default=date.today().year)
    week = models.PositiveIntegerField(default=current_week())
    geo = models.CharField(max_length=2, choices=TR_GEOS)
    season_pts = models.PositiveIntegerField(default=0)
    season_final_NTRP = models.PositiveIntegerField(default=1000)
    season_start_NTRP = models.PositiveIntegerField(default=1000)
    max_season_NTRP = models.PositiveIntegerField(default=1000)
    min_season_NTRP= models.PositiveIntegerField(default=1000)
    matches_played = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)
    matches_lost = models.PositiveIntegerField(default=0)
    matches_rted = models.PositiveIntegerField(default=0)
    
    ranking_absolute = models.PositiveIntegerField(default=0)
    ranking_category = models.PositiveIntegerField(default=0)

    last_updated = models.DateTimeField(auto_now=True)
    

    @property
    def season_NTRP_progress(self):
        return self.season_final_NTRP - self.season_start_NTRP
    
    @property
    def win_rate(self):
        return f'{int(self.matches_won / self.matches_played * 100)}%'
        
    def implement_match_result(self, winner:bool, rt:bool, opp_rt:bool, pts_gained:int, NTRP_change:int):
        if winner:
            if not opp_rt:
                self.matches_won += 1
                self.matches_played += 1
        elif rt:
            self.matches_rted += 1
            self.matches_lost += 1
            self.matches_played += 1
        else:
            self.matches_lost += 1
            self.matches_played += 1
        
        self.season_pts += pts_gained
        self.season_final_NTRP += NTRP_change
        self.max_season_NTRP = max(self.max_season_NTRP, self.season_final_NTRP)
        self.min_season_NTRP = min(self.min_season_NTRP, self.season_final_NTRP)

        self.save()

    def exclude_match_result(self, winner:bool, rt:bool, opp_rt:bool, pts_gained:int, NTRP_change:int):
        if winner:
            if not opp_rt:
                self.matches_won = max(0, self.matches_won - 1)
                self.matches_played = max(0, self.matches_played - 1)
        elif rt:
            self.matches_rted = max(0, self.matches_rted - 1)
            self.matches_lost = max(0, self.matches_lost - 1)
            self.matches_played = max(0, self.matches_played - 1)
        else:
            self.matches_lost = max(0, self.matches_lost - 1)
            self.matches_played = max(0, self.matches_played - 1)
        
        if self.max_season_NTRP == self.season_final_NTRP:
            self.max_season_NTRP = max(0, self.max_season_NTRP - NTRP_change)
        if self.min_season_NTRP == self.season_final_NTRP:
            self.min_season_NTRP = max(0, self.season_final_NTRP - NTRP_change)

        self.season_pts = max(0, self.season_pts - pts_gained)
        self.season_final_NTRP = max(0, self.season_final_NTRP - NTRP_change)
        
        self.save()

    def save(self, *args, **kwargs):
        self.geo = self.player.geo
        return super().save(*args, **kwargs)

class PlayerCurrentStats(models.Model):
    player = models.OneToOneField(Player, on_delete=models.CASCADE, related_name='current_stats')
    
    matches_played = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)
    matches_rted = models.PositiveIntegerField(default=0)
    matches_lost = models.PositiveIntegerField(default=0)
    
    matches_played_this_season = models.PositiveIntegerField(default=0)
    matches_won_this_season = models.PositiveIntegerField(default=0)
    matches_rted_this_season = models.PositiveIntegerField(default=0)
    matches_lost_this_season = models.PositiveIntegerField(default=0)

    season_pts = models.PositiveIntegerField(default=0)
    
    current_NTRP = models.PositiveIntegerField(default=1000)
    season_start_NTRP = models.PositiveIntegerField(default=1000)
    max_NTRP = models.PositiveIntegerField(default=1000)
    min_NTRP = models.PositiveIntegerField(default=1000)

    max_NTRP_this_season = models.PositiveIntegerField(default=1000)
    min_NTRP_this_season = models.PositiveIntegerField(default=1000)
    
    ranking_absolute = models.PositiveIntegerField(default=0)
    ranking_category = models.PositiveIntegerField(default=0)

    last_updated = models.DateTimeField(auto_now=True)

    @property
    def season_NTRP_progress(self):
        return self.current_NTRP - self.season_start_NTRP

    @property
    def win_rate(self):
        if self.matches_played > 0:
            return f'{int(self.matches_won / self.matches_played * 100)}%'
        else:
            return '-%'
    
    @property
    def win_rate_this_season(self):
        if self.matches_played_this_season > 0:
            return f'{int(self.matches_won_this_season / self.matches_played_this_season * 100)}%'  
        else:
            return '-%'
        
    def update_current_stats(self):
        self.matches_played = Match.objects.filter(player=self).count()
        self.matches_won = PlayerMatch.objects.filter(player=self, is_winner=True, opponent_is_withdrawn=False).count()
        self.matches_rted = PlayerMatch.objects.filter(player=self, is_withdrawn=True).count()
        self.matches_lost = PlayerMatch.objects.filter(player=self, is_winner=False).count()

        self.matches_played_this_season = PlayerMatch.objects.filter(player=self, match__season=current_season()).count()
        self.matches_won_this_season = PlayerMatch.objects.filter(player=self, is_winner=True, opponent_is_withdrawn=False, match__season=current_season()).count()
        self.save()

class PairSeasonStats(models.Model):
    pair = models.ForeignKey(Pair, on_delete=models.CASCADE, related_name='season_stats')
    season = models.PositiveIntegerField(default=date.today().year)
    week = models.PositiveIntegerField(default=current_week())
    geo = models.CharField(max_length=2, choices=TR_GEOS, default='GE')

    season_pts = models.PositiveIntegerField(default=0)
    season_final_NTRP = models.PositiveIntegerField(default=1000)
    season_start_NTRP = models.PositiveIntegerField(default=1000)
    max_season_NTRP = models.PositiveIntegerField(default=1000)
    min_season_NTRP = models.PositiveIntegerField(default=1000)
    matches_played = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)
    matches_rted = models.PositiveIntegerField(default=0)
    matches_lost = models.PositiveIntegerField(default=0)

    ranking_absolute = models.PositiveIntegerField(default=0)
    ranking_category = models.PositiveIntegerField(default=0)

    last_updated = models.DateTimeField(auto_now=True)

    @property
    def season_NTRP_progress(self):
        return self.season_final_NTRP - self.season_start_NTRP

class PairTotalStats(models.Model):
    pair = models.OneToOneField(Pair, on_delete=models.CASCADE, related_name='total_stats')
    matches_played = models.PositiveIntegerField(default=0)
    matches_won = models.PositiveIntegerField(default=0)
    matches_lost = models.PositiveIntegerField(default=0)

class Challenge(models.Model):
    challenger = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='challenges_sent')
    rival = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='challenges_received')
    message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_accepted = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Challenge from {self.challenger} to {self.rival}"
     
class ReviewToken(models.Model):
    from_player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='review_tokens')
    to_player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='reviewed_tokens')
    after_match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name='review_tokens')
    is_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    @property
    def expires_at(self):
        return self.created_at + timedelta(days=14)
    
    def __str__(self):
        return f'Rate your Opponent {self.to_player} after {self.after_match}'
    
    class Meta:
        ordering = ['-created_at']

class TimelineEvent(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='timeline_events')
    from_player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='timeline_events_from', null=True, blank=True)
    event_type = models.CharField(max_length=2, choices=TIMELINE_EVENT_TYPE)
    color = models.CharField(max_length=2, choices=TIMELINE_EVENT_COLOR)
    event_date = models.DateTimeField(auto_now_add=True)
    text = models.TextField(blank=True, null=True)
    action_text = models.CharField(max_length=255, blank=True, null=True)
    redirect_url = models.CharField(max_length=255, blank=True, null=True)
    check_field = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['-event_date']

    def __str__(self):
        return f'{self.player}: {self.event_type} - {self.text} - {self.event_date}'
        
class Ticket(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='tickets')
    message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    is_closed = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['-created_at']

    @property
    def is_open(self):
        return not self.is_closed
    
    @property
    def resolution_time(self):
        if self.closed_at:
            return self.closed_at - self.created_at
        return None
    
    def close_ticket(self):
        self.closed_at = timezone.now()
        self.is_closed = True
        self.save()

    def reopen_ticket(self):
        self.closed_at = None
        self.is_closed = False
        self.save()

    def __str__(self):
        return f'Ticket from {self.user} - {self.created_at}'
    

class StageProlongationRequest(models.Model):
    tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE, related_name='stage_prolongation_requests')
    stage = models.PositiveIntegerField()
    from_player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='stage_prolongation_requests_from')
    to_date = models.DateField(default=next_monday)
    created_at = models.DateTimeField(auto_now_add=True)
    is_approved = models.BooleanField(default=False)
    is_closed = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        new_event, created = TimelineEvent.objects.get_or_create(
            player=self.from_player,
            event_type='T',
            color='2',
            redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': self.tournament.pk}),
            check_field=f'stage_prolongation_request_{self.pk}',
        )
        if created:
            new_event.text=f'You requested to prolong the current stage of {self.tournament.title} till {self.to_date}'
            new_event.save()
        else:
            new_event.text=f'Your request to prolong the current stage of {self.tournament.title} till {self.to_date} was updated'
            new_event.save()
        return super().save(*args, **kwargs)
    
    def __str__(self):
        return f'{self.tournament.title}: Stage {self.stage} prolongation request from {self.from_player} till {self.to_date}'
    
    def approve(self):
        self.is_approved = True
        self.is_closed = True
        self.tournament.current_stage_end_date = self.to_date
        self.tournament.save()
        for player in self.tournament.players.all():
            if not player== self.from_player:
                new_event = TimelineEvent.objects.create(
                    player=player,
                    event_type='T',
                    color='2',
                    redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': self.tournament.pk}),
                    text=f'The current stage of {self.tournament.title} has been prolonged till {self.to_date}',
                    check_field=f'stage_prolongation_request_{self.pk}',
                )
            else:
                new_event = TimelineEvent.objects.create(
                    player=player,
                    event_type='T',
                    color='2',
                    text=f'Your request to prolong the current stage of {self.tournament.title} till {self.to_date} has been approved',
                    redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': self.tournament.pk}),
                )
        overdue_matches = self.tournament.matches.filter(status='OD')
        for match in overdue_matches:
            match.status = 'PD'
            match.save()

        
        self.save()

    def reject(self):
        self.is_approved = False
        self.is_closed = True
        new_event = TimelineEvent.objects.create(
            player=self.from_player,
            event_type='T',
            color='2',
            text=f'Your request to prolong the current stage of {self.tournament.title} till {self.to_date} has been rejected',
            redirect_url=reverse('rivals:tournament_detail', kwargs={'pk': self.tournament.pk}),
        )
        self.save()

    def reopen(self):
        self.is_closed = False
        self.is_approved = False
        self.save()

class PlayerOnboarding(models.Model):
    player = models.OneToOneField(Player, on_delete=models.CASCADE, related_name='onboarding')
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)
    
    ob_first_name = models.BooleanField(default=False)
    ob_last_name = models.BooleanField(default=False)
    ob_birthdate = models.BooleanField(default=False)
    ob_gender = models.BooleanField(default=False)
    ob_city = models.BooleanField(default=False)
    
    
    ob_main_info = models.BooleanField(default=False)

    ob_verify_email = models.BooleanField(default=False)
    ob_verify_tg = models.BooleanField(default=False)

    ob_avatar = models.BooleanField(default=False)
    
    ob_height = models.BooleanField(default=False)
    ob_weight = models.BooleanField(default=False)
    ob_tennis_exp_years = models.BooleanField(default=False)
    ob_availability = models.BooleanField(default=False)

    ob_additional_info = models.BooleanField(default=False)

    ob_first_tournament_registration = models.BooleanField(default=False)

    ob_first_match_played = models.BooleanField(default=False)

    ob_first_opponent_review = models.BooleanField(default=False)

    ob_wizard_completed = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.ob_first_name and self.ob_last_name and self.ob_birthdate and self.ob_gender and self.ob_city:
            self.ob_main_info = True
            self.save()
        if self.ob_height and self.ob_weight and self.ob_tennis_exp_years and self.ob_availability:
            self.ob_additional_info = True
            self.save()
        
        if self.ob_main_info and self.ob_additional_info and self.ob_avatar and self.ob_first_tournament_registration and self.ob_first_match_played and self.ob_first_opponent_review and self.ob_wizard_completed:
            self.is_completed = True
            self.save()
        super().save(*args, **kwargs)
    
    def ob_list(self):
        result = []
        
        
        result.append(('Verify your Telegram', reverse('rivals:player_update'),5,self.ob_verify_tg))
        result.append(('Update your profile info: name, birthdate, gender, city', reverse('rivals:player_update'),3,self.ob_main_info))
        result.append(('Update your additional info: height, weight, tennis experience, availability', reverse('rivals:player_update'),2,self.ob_additional_info))
        result.append(('Upload your avatar', reverse('rivals:player_update'),3,self.ob_avatar))
        result.append(('Complete the skill evaluation', reverse('rivals:player_wizard_step1'),3,self.ob_wizard_completed))
        result.append(('Verify your email', reverse('rivals:player_update'),1,self.ob_verify_email))
        result.append(('Register in your first tournament', reverse('rivals:tournaments'),3,self.ob_first_tournament_registration))
        result.append(('Play your first match', "",3,self.ob_first_match_played))
        result.append(('Rate your first opponent after the match', "",2,self.ob_first_opponent_review))
        
        return result


   