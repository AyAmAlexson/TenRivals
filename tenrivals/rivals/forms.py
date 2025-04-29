from django import forms
from .models import Player, PlayerAttributes, Tournament, PlayerTournament, Match, PlayerMatch, ReviewToken, Ticket, StageProlongationRequest, PlayerSeasonStats
from persons.models import CustomUser
from .const import GENDER, TR_GEOS, TR_CITIES, TOURNAMENT_GENDER, TOURNAMENT_FORMAT, TOURNAMENT_CATEGORY, TOURNAMENT_STATUS, TOURNAMENT_TYPE
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Challenge, TimelineEvent, PlayerCurrentStats  # Предполагается, что у вас есть модель Challenge
import math
from django.contrib import messages
from datetime import date, datetime, timedelta
import re
from persons.models import CustomUser, TelegramVerification


CustomUser = get_user_model()

def update_elo_rating(player_elo: float, opponent_elo: float, game_diff: int, matches_played: int) -> int:
    """
    Рассчитывает изменение рейтинга (ELO) для участника матча с учетом калибровочного периода.
    
    Параметры:
      player_elo: Текущий рейтинг игрока, для которого рассчитывается изменение.
      opponent_elo: Рейтинг соперника.
      game_diff: Разница в выигранных и проигранных геймах.
                 Положительное значение – победа, отрицательное – поражение.
      matches_played: Количество сыгранных матчей игроком до данного матча.
      
    Возвращает:
      Изменение рейтинга (целое число), положительное для выигрыша, отрицательное для поражения.
    """
    # Порог калибровки: 5 матчей
    calibration_matches = 5
    
    # Стандартные параметры для подтверждённых игроков
    K0 = 50
    n0 = 50
    
    # Максимальные параметры для новых игроков
    K_max = 440
    n_max = 440
    
    # Определяем эффективный K и верхнюю границу изменения (n) по числу сыгранных матчей
    if matches_played < calibration_matches:
        effective_K = K_max - (K_max - K0) * (matches_played / calibration_matches)
        effective_n = n_max - (n_max - n0) * (matches_played / calibration_matches)
    else:
        effective_K = K0
        effective_n = n0

    # Ожидаемая вероятность выигрыша игрока
    E = 1 / (1 + 10 ** ((opponent_elo - player_elo) / 2000))
    
    # Определяем исход: если game_diff >= 0, считаем, что игрок выиграл; иначе – проиграл.
    outcome = 1 if game_diff >= 0 else 0
    
    # Рассчитываем функцию f(G), используя абсолютное значение разницы геймов,
    # чтобы избежать ошибок логарифма.
    abs_game_diff = abs(game_diff)
    
    # Если игрок с более низким рейтингом выигрывает (апсет), применяем одну формулу,
    # иначе – для фаворита или при поражении.
    if outcome == 1 and player_elo < opponent_elo:
        # Победа апсетом: f(G) = 1 + 0.202*(ln(|game_diff|+1) - ln2)
        f_G = 1 + 0.202 * (math.log(abs_game_diff + 1) - math.log(2))
        f_G = max(1, f_G)
    else:
        # Для фаворитов или при поражении.
        # Если разница рейтингов менее или равна 500, считаем f(G)=1.
        if abs(player_elo - opponent_elo) <= 500:
            f_G = 1
        else:
            # При значительной разнице рейтингов используем f(G)=ln(|game_diff|+1)
            f_G = math.log(abs_game_diff + 1)
            f_G = max(1, f_G)
    
    # Вычисляем изменение рейтинга в зависимости от исхода.
    if outcome == 1:
        # При победе: выигрыш = K_eff * (1 - E) * f(G)
        rating_change = effective_K * (1 - E) * f_G
    else:
        # При поражении: потеря = -K_eff * E * f(G)
        rating_change = - effective_K * E * f_G

    # Ограничиваем изменение по модулю effective_n
    rating_change = math.copysign(min(effective_n, abs(rating_change)), rating_change)
    
    # Возвращаем округленное целое значение
    return int(round(rating_change))

class PlayerUpdateForm(forms.ModelForm):
    """
    Form for updating Player and related User information.
    """
    first_name = forms.CharField(max_length=150, required=True, label='First Name')
    last_name = forms.CharField(max_length=150, required=True, label='Last Name')
    email = forms.EmailField(required=True, label='Email', disabled=True)
    mobile = forms.CharField(max_length=15, required=False, label='Mobile')
    birthdate = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label='Birthday')
    gender = forms.ChoiceField(choices=GENDER, widget=forms.RadioSelect(), required=True, label='Gender', disabled=True)
    tennis_exp_year = forms.IntegerField(required=False, label='Tennis Experience (years)')

    class Meta:
        model = Player
        fields = ['birthdate', 'gender', 'height', 'weight', 'tennis_exp_year', 'availability']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print(f'self.instance={type(self.instance)}')
        # Initialize first_name and last_name fields based on related user data
        if self.instance and self.instance.user:
            self.fields['first_name'].initial = self.instance.user.first_name
            self.fields['last_name'].initial = self.instance.user.last_name
            self.fields['email'].initial = self.instance.user.email
            self.fields['mobile'].initial = self.instance.user.mobile
            self.fields['gender'].initial = self.instance.gender
            

        # Apply 'form-control' class to all fields except 'gender'
        for field_name, field in self.fields.items():
            if field_name != 'gender':
                field.widget.attrs['class'] = 'form-control'
        
        # Add 'form-check-input' class to radio buttons for 'gender'
        self.fields['gender'].widget.attrs.update({'class': 'form-check-input'})
        


    def save(self, commit=True):
        player = super().save(commit=False)
        if commit:
            player.is_new = False
            player.save()

            player_attrs = PlayerAttributes.objects.get_or_create(player=player)
            # Обновление связанных полей User
            user = player.user
            user.first_name = self.cleaned_data.get('first_name', user.first_name)
            user.last_name = self.cleaned_data.get('last_name', user.last_name)
            user.email = self.cleaned_data['email']
            user.mobile = self.cleaned_data['mobile']
            user.save()
            player.save()
        return player

class CityUpdateForm(forms.Form):
    """
    Separate form for selecting city.
    """
    city = forms.ChoiceField(
        choices=[], 
        widget=forms.Select(attrs={'class': 'form-select'}), 
        label='City'
    )

    def __init__(self, *args, **kwargs):
        self.geo_list = kwargs.pop('geo_list', [])
        self.cities_list = kwargs.pop('cities_list', [])
        super().__init__(*args, **kwargs)
        
        # Organize choices by geo
        choices = []
        for geo in self.geo_list:
            geo_code, geo_name = geo
            cities = [
                (city_code, city_name[:-4])
                for city_code, city_name in self.cities_list
                if city_name[-2:] == geo_code
            ]
            if cities:
                choices.append((geo_name, cities))
        self.fields['city'].choices = choices

    def save(self, player, user):
        """
        Saves the selected city and corresponding geo_code in the Player model.
        """
        selected_city = self.cleaned_data['city']
        player.city = selected_city
        player.user.preferred_city = selected_city
        print(f'THE USER={player.user.first_name} {player.user.last_name}')
        # Find geo_code for the selected city
        geo_code = None
        for city_code, city_name in self.cities_list:
            if city_code == selected_city:
                geo_code = city_name[-2:]
                break
        
        if geo_code:
            print(f'geo_code={geo_code}')
            player.geo = geo_code
            player.user.preferred_geo = geo_code
            player.save()
            player.user.save()
        else:
            raise ValueError('Geo code not found for the selected city.')

class AvatarUpdateForm(forms.ModelForm):
    """
    Separate form for updating user avatar.
    """
    class Meta:
        model = Player
        fields = ['avatar']
        widgets = {
            'avatar': forms.ClearableFileInput(attrs={'class': 'form-control-file'}),
        }
        labels = {
            'avatar': 'Update Avatar',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Apply 'form-control' class to all fields
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control' 

class ChallengeForm(forms.ModelForm):
    class Meta:
        model = Challenge
        fields = ['message']  # Пример поля
        widgets = {
            'message': forms.Textarea(attrs={'rows': 3}),
        } 

class RateOpponentForm(forms.Form):
    ATTRIBUTE_CHOICES = [(i, str(i)) for i in range(1, 6)]  # Оценки от 1 до 5

    forehand = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Forehand',
        required=True
    )
    backhand = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Backhand',
        required=True
    )
    rallies = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Rallies',
        required=True
    )
    attack = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Attack',
        required=True
    )
    serve = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Serve',
        required=True
    )
    serve_return = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Serve Return',
        required=True
    )
    volleys = forms.ChoiceField(
        choices=ATTRIBUTE_CHOICES,
        widget=forms.RadioSelect,
        label='Volleys',
        required=True
    ) 

class TournamentForm(forms.ModelForm):
    title = forms.CharField(max_length=255, required=True, widget=forms.TextInput(attrs={'class': 'form-control'}))
    description = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4}), required=False)
    city = forms.ChoiceField(choices=TR_CITIES, widget=forms.Select(attrs={'class': 'form-control'}), required=True)
    category = forms.ChoiceField(choices=TOURNAMENT_CATEGORY, widget=forms.Select(attrs={'class': 'form-control'}), required=True)
    format = forms.ChoiceField(choices=TOURNAMENT_FORMAT, widget=forms.Select(attrs={'class': 'form-control'}), required=True)
    players_num = forms.IntegerField(widget=forms.NumberInput(attrs={'class': 'form-control'}), required=True)
    status = forms.ChoiceField(choices=TOURNAMENT_STATUS, widget=forms.Select(attrs={'class': 'form-control'}), required=True)
    image = forms.ImageField(widget=forms.ClearableFileInput(attrs={'class': 'form-control-file'}), required=False)
    gender = forms.ChoiceField(choices=TOURNAMENT_GENDER, widget=forms.Select(attrs={'class': 'form-control'}), required=True)
    type = forms.ChoiceField(choices=TOURNAMENT_TYPE, widget=forms.Select(attrs={'class': 'form-control'}), required=True)

    class Meta:
        model = Tournament
        fields = ['title', 'description', 'city', 'category', 'format', 'players_num', 'status', 'image', 'gender', 'type']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'city': forms.Select(choices=TR_CITIES, attrs={'class': 'form-control'}),
            'category': forms.Select(choices=TOURNAMENT_CATEGORY, attrs={'class': 'form-control'}),
            'format': forms.Select(choices=TOURNAMENT_FORMAT, attrs={'class': 'form-control'}),
            'players_num': forms.NumberInput(attrs={'class': 'form-control'}),
            'status': forms.Select(choices=TOURNAMENT_STATUS, attrs={'class': 'form-control'}),
            'image': forms.ClearableFileInput( attrs={'class': 'form-control-file'}),
            'gender': forms.Select(choices=TOURNAMENT_GENDER, attrs={'class': 'form-control'}),
            'type': forms.Select(choices=TOURNAMENT_TYPE, attrs={'class': 'form-control'}),
        }

    def save(self, commit=True):
        tournament = super().save(commit=False)
        if commit:
            tournament.save()
            all_tournament_players = PlayerTournament.objects.filter(tournament=tournament)
            tournament.players_enrolled = all_tournament_players.count()
            tournament.save()
        return tournament

class PlayerMatchScoreForm(forms.ModelForm):
    class Meta:
        model = PlayerMatch
        fields = [
            'set1_score', 'set1_tie_score', 'set1_won',
            'set2_score', 'set2_tie_score', 'set2_won',
            'set3_score', 'set3_tie_score', 'set3_won',
            'set3_is_tiebreak',
            'is_withdrawn',
        ]
        widgets = {
            'set1_score': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '-',
                'maxlength': '6',
                'style': 'width:6ch; max-width:6ch; padding-left: 1ch; padding-right: 1ch; padding-top: 0.8ch; padding-bottom: 0.8ch; line-height: 1.8;',
            }),
            'set1_tie_score': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '-',
                'maxlength': '6',
                'style': 'width:6ch; max-width:6ch; padding-left: 1ch; padding-right: 1ch; padding-top: 0.8ch; padding-bottom: 0.8ch; line-height: 1.8;',
            }),
            'set1_won': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'set2_score': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '-',
                'maxlength': '6',
                'style': 'width:6ch; max-width:6ch; padding-left: 1ch; padding-right: 1ch; padding-top: 0.8ch; padding-bottom: 0.8ch; line-height: 1.8;',
            }),
            'set2_tie_score': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '-',
                'maxlength': '6',
                'style': 'width:6ch; max-width:6ch; padding-left: 1ch; padding-right: 1ch; padding-top: 0.8ch; padding-bottom: 0.8ch; line-height: 1.8;',
            }),
            'set2_won': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'set3_score': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '-',
                'maxlength': '6',
                'style': 'width:6ch; max-width:6ch; padding-left: 1ch; padding-right: 1ch; padding-top: 0.8ch; padding-bottom: 0.8ch; line-height: 1.8;',
            }),
            'set3_tie_score': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '-',
                'maxlength': '6',
                'style': 'width:6ch; max-width:6ch; padding-left: 1ch; padding-right: 1ch; padding-top: 0.8ch; padding-bottom: 0.8ch; line-height: 1.8;',
            }),
            'set3_won': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'set3_is_tiebreak': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'is_withdrawn': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
                'id': 'id_player_withdrawn',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Устанавливаем необязательные поля
        optional_fields = [
            'set1_score', 'set1_tie_score', 'set1_won',
            'set2_score', 'set2_tie_score', 'set2_won',
            'set3_score', 'set3_tie_score', 'set3_won',
            'set3_is_tiebreak', 'is_withdrawn'
        ]
        for field in optional_fields:
            if field in self.fields:
                self.fields[field].required = False

        # Устанавливаем идентификаторы для синхронизации с RT-чекбоксами
        if self.prefix == 'player':
            self.fields['is_withdrawn'].widget.attrs.update({'id': 'id_player_withdrawn'})
        elif self.prefix == 'opponent':
            self.fields['is_withdrawn'].widget.attrs.update({'id': 'id_opponent_withdrawn'})

    def clean(self):
        cleaned_data = super().clean()
        # Если установлена галочка RT, то отсутствующие обязательные поля (например, set1_score)
        # подставляются автоматически в значение 0.
        if cleaned_data.get('is_withdrawn'):
            if not cleaned_data.get('set1_score'):
                cleaned_data['set1_score'] = 0
        return cleaned_data

class DualPlayerMatchScoreForm(forms.Form):
    """
    Форма для одновременного ввода результатов матча для двух экземпляров модели PlayerMatch:
    для игрока и соперника.
    Дополнительные поля:
      - court: обязательное, если матч не отменён;
      - game_date: обязательное, если матч не отменён.
    """
    def __init__(self, *args, player_instance=None, opponent_instance=None, match_instance=None, request=None, **kwargs):
        self.request = request  # Сохраняем request
        super().__init__(*args, **kwargs)
        self.match_instance = match_instance
        self.player_form = PlayerMatchScoreForm(
            *args,
            prefix='player',
            instance=player_instance
        )
        self.opponent_form = PlayerMatchScoreForm(
            *args,
            prefix='opponent',
            instance=opponent_instance
        )
        
        # Добавляем дополнительные поля.
        # Важно: устанавливаем required=False, чтобы встроенная валидация не срабатывала,
        # а обязательность будем проверять в методе clean().
        self.fields['court'] = forms.CharField(
            label="Court",
            required=False,
            widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter the court'})
        )
        self.fields['game_date'] = forms.DateField(
            label="Game date",
            required=False,
            widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
        )
    
        # Если передан match_instance, задаём значения по умолчанию.
        if self.match_instance:
            self.fields['court'].initial = self.match_instance.court
            self.fields['game_date'].initial = self.match_instance.date

    def validate_set_scores(self):
        """Проверяет соответствие счета и отметок о выигрыше сетов"""
        errors = []
        
        # Проверяем валидность форм перед проверкой результата
        if not (self.player_form.is_valid() and self.opponent_form.is_valid()):
            return []
        
        for set_num in [1, 2, 3]:
            player_score = self.player_form.cleaned_data.get(f'set{set_num}_score') or 0
            opponent_score = self.opponent_form.cleaned_data.get(f'set{set_num}_score') or 0
            player_tie = self.player_form.cleaned_data.get(f'set{set_num}_tie_score') or 0
            opponent_tie = self.opponent_form.cleaned_data.get(f'set{set_num}_tie_score') or 0
            player_won = self.player_form.cleaned_data.get(f'set{set_num}_won')
            opponent_won = self.opponent_form.cleaned_data.get(f'set{set_num}_won')

            # Если есть счет, должна быть отметка о победителе
            if (player_score or opponent_score) and not (player_won or opponent_won):
                errors.append(f"Set {set_num}: Winner must be selected")
                continue

            # Проверка соответствия счета и победителя
            if player_score > opponent_score:
                if not player_won or opponent_won:
                    errors.append(f"Set {set_num}: Winner mark doesn't match the score")
            elif opponent_score > player_score:
                if player_won or not opponent_won:
                    errors.append(f"Set {set_num}: Winner mark doesn't match the score")
            elif player_score == opponent_score and (player_score or opponent_score):
                # При равном счете проверяем тай-брейк
                if player_tie > opponent_tie:
                    if not player_won or opponent_won:
                        errors.append(f"Set {set_num}: Tiebreak winner mark doesn't match the score")
                elif opponent_tie > player_tie:
                    if player_won or not opponent_won:
                        errors.append(f"Set {set_num}: Tiebreak winner mark doesn't match the score")
                else:
                    errors.append(f"Set {set_num}: Equal scores, tiebreak must determine the winner")

        return errors

    def validate_match_result(self):
        """Проверяет общий результат матча"""
        # Проверяем валидность форм перед проверкой результата
        if not (self.player_form.is_valid() and self.opponent_form.is_valid()):
            return []
        
        # Получаем данные из cleaned_data вместо instance
        player_sets = sum([
            bool(self.player_form.cleaned_data.get('set1_won')),
            bool(self.player_form.cleaned_data.get('set2_won')),
            bool(self.player_form.cleaned_data.get('set3_won'))
        ])
        
        opponent_sets = sum([
            bool(self.opponent_form.cleaned_data.get('set1_won')),
            bool(self.opponent_form.cleaned_data.get('set2_won')),
            bool(self.opponent_form.cleaned_data.get('set3_won'))
        ])
        
        # Проверяем, есть ли вообще какие-то результаты
        if player_sets == 0 and opponent_sets == 0:
            # Если нет результатов, возможно это новый матч или RT
            if (self.player_form.cleaned_data.get('is_withdrawn') or 
                self.opponent_form.cleaned_data.get('is_withdrawn')):
                return []  # Для RT это нормальная ситуация
            return ["Please enter match results"]
        
        if player_sets == opponent_sets:
            return [f"Match cannot end in a tie. You entered the score {player_sets} - {opponent_sets} by sets. Please check the scores and set winners."]
        
        return []

    def handle_withdrawal(self, rt_player, rt_opponent):
        """Обрабатывает ситуацию с RT (withdrawal)"""
        if rt_player:
            # Обнуляем результаты игрока
            for set_num in [1, 2, 3]:
                setattr(self.player_form.instance, f'set{set_num}_score', 0)
                setattr(self.player_form.instance, f'set{set_num}_tie_score', 0)
                setattr(self.player_form.instance, f'set{set_num}_won', False)
            self.player_form.instance.is_withdrawn = True

            # Устанавливаем победные результаты сопернику
            for set_num in [1, 2, 3]:
                setattr(self.opponent_form.instance, f'set{set_num}_score', 6)
                setattr(self.opponent_form.instance, f'set{set_num}_tie_score', 0)
                setattr(self.opponent_form.instance, f'set{set_num}_won', True)
            self.opponent_form.instance.is_withdrawn = False

        elif rt_opponent:
            # Аналогично для соперника
            for set_num in [1, 2, 3]:
                setattr(self.opponent_form.instance, f'set{set_num}_score', 0)
                setattr(self.opponent_form.instance, f'set{set_num}_tie_score', 0)
                setattr(self.opponent_form.instance, f'set{set_num}_won', False)
            self.opponent_form.instance.is_withdrawn = True

            # Устанавливаем победные результаты игроку
            for set_num in [1, 2, 3]:
                setattr(self.player_form.instance, f'set{set_num}_score', 6)
                setattr(self.player_form.instance, f'set{set_num}_tie_score', 0)
                setattr(self.player_form.instance, f'set{set_num}_won', True)
            self.player_form.instance.is_withdrawn = False

    def update_match_status(self):
        """Обновляет статус матча и связанные поля"""
        if self.match_instance:
            if self.player_form.instance.is_withdrawn or self.opponent_form.instance.is_withdrawn:
                self.match_instance.status = "CA"
                self.match_instance.court = 'N/A'
                self.match_instance.date = datetime.now().date()
            else:
                self.match_instance.status = "FI"
                self.match_instance.court = self.cleaned_data.get('court')
                self.match_instance.date = self.cleaned_data.get('game_date')

    def create_review_tokens(self, player_instance, opponent_instance):
        """Создает токены для взаимного оценивания игроков"""
        for (from_player, to_player) in [(player_instance, opponent_instance),
                                       (opponent_instance, player_instance)]:
            newtoken, created = ReviewToken.objects.get_or_create(
                from_player=from_player.player,
                to_player=to_player.player,
                after_match=self.match_instance
            )
            if created:
                TimelineEvent.objects.create(
                    player=from_player.player,
                    event_type='R',
                    color='I',
                    text=f"Give skills rating for {to_player.player} after the match",
                    redirect_url=reverse('rivals:rate_opponent', kwargs={'pk': to_player.player.pk})
                )

    def update_player_stats(self, player_instance, opponent_instance):
        """Обновляет статистику игроков"""
        # Расчет разницы в сетах
        sets_difference = (player_instance.set1_score +
                          (player_instance.set2_score or 0) +
                          (player_instance.set3_score or 0) -
                          opponent_instance.set1_score -
                          (opponent_instance.set2_score or 0) -
                          (opponent_instance.set3_score or 0))

        # Получение текущей статистики
        player_stats, _ = PlayerCurrentStats.objects.get_or_create(player=player_instance.player)
        opponent_stats, _ = PlayerCurrentStats.objects.get_or_create(player=opponent_instance.player)

        # Обновление рейтинга NTRP
        player_instance.NTRP_change = update_elo_rating(
            player_stats.current_NTRP,
            opponent_stats.current_NTRP,
            sets_difference,
            player_stats.matches_played
        )
        opponent_instance.NTRP_change = update_elo_rating(
            opponent_stats.current_NTRP,
            player_stats.current_NTRP,
            -sets_difference,
            opponent_stats.matches_played
        )

    def award_season_pts(self, player_instance, opponent_instance):
        """Создает события в timeline для обоих игроков"""
        for instance in [player_instance, opponent_instance]:
            other_instance = opponent_instance if instance == player_instance else player_instance
            
            if instance.is_winner:
                instance.season_pts_gained = 5
            elif instance.is_withdrawn:
                instance.season_pts_gained = -3
            else:
                instance.season_pts_gained = 1

            event, created = TimelineEvent.objects.get_or_create(
                player=instance.player,
                event_type='P',
                check_field=f'{self.match_instance.pk}',
                redirect_url=reverse('rivals:match_detail', kwargs={'pk': self.match_instance.pk}),
                color='W'
            )
            
            event.text = (
                f'Gained {instance.season_pts_gained} points this season after the match with {other_instance.player}'
                if created else
                f'Updated number of points gained after the match with {other_instance.player}: {instance.season_pts_gained}'
            )
            event.save()
            instance.save()

    def determine_match_winner(self, player_instance, opponent_instance):
        """Определяет победителя матча и устанавливает флаг is_winner"""
        if player_instance.is_withdrawn:
            opponent_instance.is_winner = True
            player_instance.is_winner = False
            return
        
        if opponent_instance.is_withdrawn:
            player_instance.is_winner = True
            opponent_instance.is_winner = False
            return

        # Подсчет выигранных сетов
        player_sets = sum([
            bool(player_instance.set1_won),
            bool(player_instance.set2_won),
            bool(player_instance.set3_won)
        ])
        
        opponent_sets = sum([
            bool(opponent_instance.set1_won),
            bool(opponent_instance.set2_won),
            bool(opponent_instance.set3_won)
        ])
        
        # Установка флага победителя
        player_instance.is_winner = player_sets > opponent_sets
        opponent_instance.is_winner = opponent_sets > player_sets

    def approval_by_sender(self, player_instance, opponent_instance):
        if self.request.user.player == player_instance.player:
            player_instance.is_approved_by_player = True
            player_instance.is_approved_by_opponent = False
            opponent_instance.is_approved_by_player = False
            opponent_instance.is_approved_by_opponent = True
        elif self.request.user.player == opponent_instance.player:
            opponent_instance.is_approved_by_player = True
            opponent_instance.is_approved_by_opponent = False
            player_instance.is_approved_by_player = False
            player_instance.is_approved_by_opponent = True
      
        else:
            player_instance.is_approved_by_player = False
            player_instance.is_approved_by_opponent = False
            opponent_instance.is_approved_by_player = False
            opponent_instance.is_approved_by_opponent = False

    def determine_tiebreaks(self, player_instance, opponent_instance):
        """Определяет наличие тай-брейков"""
        if (player_instance.set1_tie_score is not None or opponent_instance.set1_tie_score is not None):
            player_instance.set1_has_tiebreak = True
            opponent_instance.set1_has_tiebreak = True
        else:
            player_instance.set1_has_tiebreak = False
            opponent_instance.set1_has_tiebreak = False

        if (player_instance.set2_tie_score is not None or opponent_instance.set2_tie_score is not None):
            player_instance.set2_has_tiebreak = True
            opponent_instance.set2_has_tiebreak = True
        else:
            player_instance.set2_has_tiebreak = False
            opponent_instance.set2_has_tiebreak = False

        if (player_instance.set3_tie_score is not None or opponent_instance.set3_tie_score is not None):
            player_instance.set3_has_tiebreak = True
            opponent_instance.set3_has_tiebreak = True
        else:
            player_instance.set3_has_tiebreak = False
            opponent_instance.set3_has_tiebreak = False


    def save(self, commit=True):
        """Основной метод сохранения"""
        # Проверка корректности результатов
        score_errors = self.validate_set_scores()
        match_errors = self.validate_match_result()
        
        if score_errors or match_errors:
            for error in score_errors + match_errors:
                messages.warning(self.request, error)
            return None, None

        # Получаем флаги RT
        rt_player = self.player_form.cleaned_data.get('is_withdrawn', False)
        rt_opponent = self.opponent_form.cleaned_data.get('is_withdrawn', False)

        # Обработка RT и основных результатов
        self.handle_withdrawal(rt_player, rt_opponent)

        # Сохранение основных данных
        player_instance = self.player_form.save(commit=commit)
        opponent_instance = self.opponent_form.save(commit=commit)
       
        # Определение наличия тай-брейков
        self.determine_tiebreaks(player_instance, opponent_instance)
        # Определение победителя
        self.determine_match_winner(player_instance, opponent_instance)

        if commit:
            # Обновление дополнительных полей
            self.update_match_status()
            if self.match_instance:
                self.match_instance.save()
                
                # Создание токенов для отзывов
                self.create_review_tokens(player_instance, opponent_instance)
                
                # Обновление статистики
                self.update_player_stats(player_instance, opponent_instance)
                
                # Создание событий в timeline
                self.award_season_pts(player_instance, opponent_instance)

                self.approval_by_sender(player_instance, opponent_instance)

                # Финальное сохранение
                player_instance.save()
                opponent_instance.save()

        return player_instance, opponent_instance

class TicketForm(forms.ModelForm):
    message = forms.CharField(
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 4,
            'placeholder': 'Enter your message here...'
        })
    )

    class Meta:
        model = Ticket
        fields = ['message']

class StageProlongationRequestForm(forms.ModelForm):
    class Meta:
        model = StageProlongationRequest
        fields = ['to_date']

        #default +1 week
        widgets = {
            'to_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control', 'value': datetime.now() + timedelta(days=7)})
        }

    def __init__(self, *args, player=None, tournament=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.player = player
        self.tournament = tournament

    def clean(self):
        cleaned_data = super().clean()
        to_date = cleaned_data.get('to_date')
        if to_date < datetime.now().date():
            raise forms.ValidationError("To date cannot be in the past.")
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.from_player = self.player
        instance.tournament = self.tournament
        instance.stage = self.tournament.current_stage
        if commit:
            instance.save()
        return instance

class AddTelegramUsernameForm(forms.Form):
    """
    Форма для ввода Telegram username на первом этапе визарда.
    """
    telegram_username = forms.CharField(
        label="Telegram Username",
        max_length=32,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Your Telegram username (without @)',
            'autofocus': True,
        }),
        help_text="Enter your Telegram username (starting with a letter, 5-32 chars, a-z, 0-9, _)"
    )

    def clean_telegram_username(self):
        """
        Валидация ника Telegram: удаляем '@', проверяем длину и символы.
        """
        tg_username = self.cleaned_data.get('telegram_username')
        if not tg_username:
            # Поле required=True, но на всякий случай
            raise forms.ValidationError("Telegram username is required.")

        if tg_username.startswith('@'):
            tg_username = tg_username[1:]

        if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', tg_username):
             raise forms.ValidationError(
                "Invalid Telegram username format. Must start with a letter, "
                "contain 5-32 alphanumeric characters or underscores."
            )

        
        if CustomUser.objects.filter(telegram=tg_username).exists() or TelegramVerification.objects.filter(telegram_username=tg_username).exists():
            raise forms.ValidationError("This Telegram username is already associated with another account.")

        return tg_username

class PlayerWizardForm(forms.ModelForm):
    """
    Форма-визард для заполнения профиля игрока, определения его уровня
    и создания начальной статистики. (ЭТАП 2)
    """
    # Поля для CustomUser
    first_name = forms.CharField(max_length=150, required=True, label='First Name')
    last_name = forms.CharField(max_length=150, required=True, label='Last Name')
    mobile = forms.CharField(max_length=15, required=False, label='Mobile Phone')
    # email больше не нужен здесь, т.к. он основной для входа и вряд ли меняется в визарде
    # email = forms.EmailField(required=False, label='Email')
    # telegram = forms.CharField(max_length=32, required=True, label='Telegram')
    
    # Поля для Player
    birthdate = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}), 
        required=True, 
        label='Date of Birth'
    )
    gender = forms.ChoiceField(
        choices=GENDER, 
        widget=forms.RadioSelect, 
        required=True, 
        label='Gender'
    )
    height = forms.FloatField(
        required=True, 
        label='Height (cm)',
        min_value=120,
        max_value=220,
        help_text='Enter your height in centimeters'
    )
    weight = forms.FloatField(
        required=True, 
        label='Weight (kg)',
        min_value=40,
        max_value=150,
        help_text='Enter your weight in kilograms'
    )
    tennis_exp_year = forms.IntegerField(
        required=True, 
        label='When did you start playing tennis?',
        min_value=1980,
        max_value=datetime.now().year,
        help_text='Enter the year when you started playing tennis'
    )
    availability = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Your availability',
        help_text='Let others know when you are available to play'
    )
    
    # Город и регион
    city = forms.ChoiceField(
        choices=TR_CITIES,
        widget=forms.Select,
        required=True,
        label='City'
    )
    
    # Вопросы для определения уровня игрока (NTRP)
    EXPERIENCE_CHOICES = [
        (0, 'Never played before'),
        (100, 'Less than 1 year'),
        (200, 'Between 1-3 years'),
        (300, 'Between 3-5 years'),
        (400, 'More than 5 years')
    ]
    
    FREQUENCY_CHOICES = [
        (0, 'Rarely or just starting'),
        (100, 'Once a month'),
        (200, 'Once a week'),
        (300, 'Several times a week'),
        (400, 'Almost every day')
    ]
    
    LEVEL_CHOICES = [
        (0, 'Beginner - just learning the basics'),
        (100, 'Novice - can rally but inconsistent'),
        (200, 'Intermediate - consistent rally, some spin'),
        (300, 'Advanced - good technique, consistent spin'),
        (400, 'Expert - very good technique and match experience')
    ]
    
    SERVE_CHOICES = [
        (0, 'Learning how to serve'),
        (100, 'Can get the ball in but inconsistent'),
        (200, 'Consistent serve with some direction'),
        (300, 'Different types of serves with spin'),
        (400, 'Strong and accurate serves')
    ]
    
    MATCH_CHOICES = [
        (0, 'Never played a match'),
        (100, 'Played a few friendly matches'),
        (200, 'Played in friendly competitions'),
        (300, 'Played in local tournaments'),
        (400, 'Played in regional/national tournaments')
    ]
    
    experience_level = forms.ChoiceField(
        choices=EXPERIENCE_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label='How long have you been playing tennis?'
    )
    
    playing_frequency = forms.ChoiceField(
        choices=FREQUENCY_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label='How often do you play tennis?'
    )
    
    technical_level = forms.ChoiceField(
        choices=LEVEL_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label='How would you describe your technical level?'
    )
    
    serve_level = forms.ChoiceField(
        choices=SERVE_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label='How would you describe your serve?'
    )
    
    match_experience = forms.ChoiceField(
        choices=MATCH_CHOICES,
        widget=forms.RadioSelect,
        required=True,
        label='What is your match experience?'
    )
    
    avatar = forms.ImageField(
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': 'image/*',
        }),
        help_text='Upload your profile picture'
    )
    
    class Meta:
        model = Player
        fields = [
            'first_name', 'last_name', 'mobile', # 'email', 'telegram',
            'birthdate', 'gender', 'height', 'weight', 
            'tennis_exp_year', 'availability', 'city'
        ]
    
    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        
        # Инициализация полей из существующих данных пользователя
        if self.user:
            self.fields['first_name'].initial = self.user.first_name
            self.fields['last_name'].initial = self.user.last_name
            # self.fields['email'].initial = self.user.email
            self.fields['mobile'].initial = self.user.mobile
            # self.fields['telegram'].initial = self.user.telegram
            self.fields['city'].initial = self.user.preferred_city
            
            # Если у пользователя уже есть Player профиль
            try:
                player = self.user.player
                if player:
                    self.fields['birthdate'].initial = player.birthdate
                    self.fields['gender'].initial = player.gender
                    self.fields['height'].initial = player.height
                    self.fields['weight'].initial = player.weight
                    self.fields['tennis_exp_year'].initial = player.tennis_exp_year
                    self.fields['availability'].initial = player.availability
                    self.fields['city'].initial = player.city
            except (Player.DoesNotExist, AttributeError):
                pass
        
        # Применяем классы стилей для полей формы
        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.EmailInput, forms.DateInput, forms.Select)):
                field.widget.attrs.update({'class': 'form-control'})
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs.update({'class': 'form-control'})
            elif isinstance(field.widget, forms.RadioSelect):
                field.widget.attrs.update({'class': 'form-check-input'})
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Базовая валидация
        if not cleaned_data.get('first_name'):
            self.add_error('first_name', 'First name is required')
        
        if not cleaned_data.get('last_name'):
            self.add_error('last_name', 'Last name is required')
        
        # Проверка даты рождения (не моложе 5 лет и не старше 100 лет)
        birthdate = cleaned_data.get('birthdate')
        if birthdate:
            today = datetime.now().date()
            age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
            if age < 5:
                self.add_error('birthdate', 'You must be at least 5 years old')
            elif age > 100:
                self.add_error('birthdate', 'Age cannot exceed 100 years')
        
        return cleaned_data
    
    def calculate_ntrp(self):
        """
        Рассчитывает NTRP игрока на основе ответов в анкете.
        NTRP (National Tennis Rating Program) - от 1.0 до 7.0
        """
        # Получаем значения из формы
        experience = int(self.cleaned_data.get('experience_level', 0))
        frequency = int(self.cleaned_data.get('playing_frequency', 0))
        technique = int(self.cleaned_data.get('technical_level', 0))
        serve = int(self.cleaned_data.get('serve_level', 0))
        matches = int(self.cleaned_data.get('match_experience', 0))
        
        # Вычисляем общую сумму баллов (максимум 2000)
        total_points = experience + frequency + technique + serve + matches
        
        # Пересчитываем в шкалу NTRP (от 1.0 до 7.0)
        # Формула: 1.0 + (total_points / 2000) * 6.0
        ntrp_raw = 1.0 + (total_points / 2000) * 6.0
        
        # Округляем до 1 десятичного знака
        ntrp = round(ntrp_raw * 10) / 10
        
        # Конвертируем в целое число для хранения (умножаем на 10)
        return int(ntrp * 1000)
    
    def determine_category(self, ntrp):
        """
        Определяет категорию игрока на основе NTRP.
        
        Категории:
        C0: NTRP < 2.0 (начинающие)
        C1: 2.0 <= NTRP < 3.5 (любители)
        C2: 3.5 <= NTRP < 4.5 (полупрофессионалы)
        C3: 4.5 <= NTRP < 7.0 (профессионалы)
        C5: NTRP >= 7.0 (продвинутые профессионалы)
        """
        ntrp_float = ntrp / 10000  # Преобразуем в формат с плавающей точкой (например, 350 -> 3.5)
        
        if ntrp_float < 2.0:
            return 'C0'
        elif ntrp_float < 3.5:
            return 'C1'
        #elif ntrp_float < 4.0:
        #    return 'C2'
        elif ntrp_float < 4.5:
            return 'C3'
        elif ntrp_float < 7.0:
            return 'C4'
        else:
            return 'C5'
    
    def save(self, commit=True):
        """
        Сохраняет данные формы, обновляя или создавая модели CustomUser, Player и PlayerSeasonStats.
        """
        # Получаем или создаем профиль игрока
        if self.instance and self.instance.pk:
            player = super().save(commit=False)
        else:
            player = super().save(commit=False)
            player.user = self.user
        
        # Обновляем данные пользователя
        user = self.user
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        
        if self.cleaned_data.get('mobile'):
            user.mobile = self.cleaned_data['mobile']
        
        # Устанавливаем город и определяем регион
        city_code = self.cleaned_data['city']
        user.preferred_city = city_code
        
        # Находим geo_code для выбранного города
        geo_code = None
        for city_code_check, city_name in TR_CITIES:
            if city_code_check == city_code:
                geo_code = city_name[-2:]
                break
        
        if geo_code:
            user.preferred_geo = geo_code
            player.geo = geo_code
        
        # Обновляем данные игрока
        player.birthdate = self.cleaned_data['birthdate']
        player.gender = self.cleaned_data['gender']
        player.height = self.cleaned_data['height']
        player.weight = self.cleaned_data['weight']
        player.tennis_exp_year = self.cleaned_data['tennis_exp_year']
        player.availability = self.cleaned_data.get('availability', '')
        player.city = city_code
        
        # Рассчитываем NTRP и определяем категорию
        ntrp_value = self.calculate_ntrp()
        player.category = self.determine_category(ntrp_value)
        
        # Устанавливаем флаг is_new в False, т.к. профиль заполнен
        player.is_new = False
        
        # Сохраняем аватар, если он был загружен
        if self.cleaned_data.get('avatar'):
            player.avatar = self.cleaned_data['avatar']
        
        if commit:
            user.save()
            player.save()
            
            # Создаем или обновляем сезонную статистику
            current_year = date.today().year
            stats, created = PlayerSeasonStats.objects.get_or_create(
                player=player,
                season=current_year,
                defaults={
                    'geo': player.geo,
                    'season_start_NTRP': ntrp_value,
                    'season_final_NTRP': ntrp_value,
                    'max_season_NTRP': ntrp_value,
                    'min_season_NTRP': ntrp_value
                }
            )
            
            # Если запись уже существовала, обновляем её
            if not created:
                stats.season_start_NTRP = ntrp_value
                stats.season_final_NTRP = ntrp_value
                stats.max_season_NTRP = ntrp_value
                stats.min_season_NTRP = ntrp_value
                stats.save()
            
            # Создаем запись в PlayerCurrentStats, если её еще нет
            PlayerCurrentStats.objects.get_or_create(
                player=player,
                defaults={
                    'current_NTRP': ntrp_value
                }
            )
            
            # Добавляем событие в таймлайн
            TimelineEvent.objects.create(
                player=player,
                event_type='P',
                color='S',
                text=f"Profile completed with initial NTRP rating: {ntrp_value}",
                redirect_url=reverse('rivals:player_detail', kwargs={'pk': player.pk})
            )
            
            # Дополнительно создаем PlayerAttributes, если нужно
            PlayerAttributes.objects.get_or_create(player=player)
        
        return player
    

    