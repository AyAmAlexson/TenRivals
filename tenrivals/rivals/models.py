from django.db import models
from persons.models import CustomUser
from .const import TOURNAMENT_GENDER, TOURNAMENT_FORMAT, TOURNAMENT_CATEGORY, TOURNAMENT_STATUS, TOURNAMENT_TYPE, GENDER
from .const import DELETED_PLAYER, DELETED_PAIR, MATCH_STATUS, RR_GEOS, CURRENT_SEASON
from datetime import date, datetime, timedelta

import math, random


def today():
    return date.today()
def tomorrow():
    return datetime.now().date() + timedelta(days=1)
def next_monday():
    next_week = tomorrow + timedelta(days=7)
    days_until_monday = (7 - next_week.weekday()) % 7
    return next_week + timedelta(days=days_until_monday)


class Player(models.Model):
    _user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='player')
    _birthdate = models.DateField(null=True)
    _gender = models.CharField(max_length=1, choices=GENDER, null=True)
    _geo = models.CharField(max_length=50, default='GE')
    _city = models.CharField(max_length=50, default='TB')
    _height = models.FloatField(null=True)
    _weight = models.FloatField(null=True)
    _tennis_exp_year = models.PositiveIntegerField(null=True)
    _availability = models.TextField(null=True)

    _player_since = models.DateField(auto_now_add=True)
    _avatar = models.ImageField(upload_to='avatars/', null=True)

    _level_current = models.FloatField(null=True)
    _level_log = models.JSONField(default=dict)
    _rating_pts = models.PositiveIntegerField(default=0)
    _attributes = models.JSONField(default=dict)

    def update_attributes(self, forehand, backhand, rallies, attack, serve, serve_return, volleys):
        self._attributes['forehand'] = (self._attributes['forehand'] * self._attributes['response_no'] + forehand) / (
                self._attributes['response_no'] + 1)
        self._attributes['backhand'] = (self._attributes['backhand'] * self._attributes['response_no'] + backhand) / (
                self._attributes['response_no'] + 1)
        self._attributes['rallies'] = (self._attributes['rallies'] * self._attributes['response_no'] + rallies) / (
                self._attributes['response_no'] + 1)
        self._attributes['attack'] = (self._attributes['attack'] * self._attributes['response_no'] + attack) / (
                self._attributes['response_no'] + 1)
        self._attributes['serve'] = (self._attributes['serve'] * self._attributes['response_no'] + serve) / (
                self._attributes['response_no'] + 1)
        self._attributes['serve_return'] = (self._attributes['serve_return'] * self._attributes[
            'response_no'] + serve_return) / (
                                                   self._attributes['response_no'] + 1)
        self._attributes['volleys'] = (self._attributes['volleys'] * self._attributes['response_no'] + volleys) / (
                self._attributes['response_no'] + 1)

        self._attributes['summary'] = (self._attributes['forehand'] + self._attributes['backhand'] + self._attributes[
            'rallies'] + self._attributes['attack'] + self._attributes['serve'] + self._attributes['serve_return'] +
                                       self._attributes['volleys']) / 7
        self._oppo_score = self._attributes['summary']

        self._attributes['response_no'] += 1
        self.save()

    @property
    def _age(self):
        return today.year - self._birthdate.year - (
                (today.month, today.day) < (self._birthdate.month, self._birthdate.day))

    @property
    def _tennis_exp(self):
        return today.year - self._tennis_exp_year

    def str(self):
        return f'{self._user.first_name} {self._user.last_name}'


class Match(models.Model):
    _tournament = models.ForeignKey('Tournament', on_delete=models.CASCADE)
    _tournament_stage = models.SmallIntegerField(null=True)

    _players = models.ManyToManyField('Player', through='PlayerMatch', through_fields=('_match', '_player'),
                                      related_name='match_as_player')
    _pairs = models.ManyToManyField('Pair', through='PairMatch', through_fields=('_match', '_pair'),
                                    related_name='match_as_pair')

    _date = models.DateField(null=True)
    _court = models.CharField(max_length=50, null=True)

    _status = models.CharField(max_length=2, choices=MATCH_STATUS, default='PD')


class Tournament(models.Model):
    _title = models.CharField(max_length=150, null=True)

    _type = models.CharField(max_length=2, choices=TOURNAMENT_TYPE, default='RM')
    _category = models.CharField(max_length=2, choices=TOURNAMENT_CATEGORY)
    _gender = models.CharField(max_length=1, choices=TOURNAMENT_GENDER)
    _format = models.CharField(max_length=2, choices=TOURNAMENT_FORMAT)
    _geo = models.CharField(max_length=2, choices=RR_GEOS, default='GE')
    _city = models.CharField(max_length=50, null=True)
    _status = models.CharField(max_length=2, choices=TOURNAMENT_STATUS, default='CI')

    _start_date = models.DateField(null=True)

    _current_stage = models.SmallIntegerField(default=0)
    _current_stage_start_date = models.DateField(null=True)
    _current_stage_end_date = models.DateField(null=True)

    _players = models.ManyToManyField('Player', through='PlayerTournament')
    _pairs = models.ManyToManyField('Pair', through='PairTournament')

    _players_num = models.PositiveSmallIntegerField()
    _players_enrolled = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f'{self._title}: {self._gender} {self._category} {self._format} {self._type}'

    @property
    def _stages_max(self):
        if self._type == 'RM':
            return math.ceil(
                math.log(self._players_num, 2))  # округляем вверх, если участников больше, чем ближайшая степень двойки
        elif self._type == 'GM':
            return self._players_num - 1
        elif self._type == 'OS':
            return math.ceil(math.log(self._players_num,
                                      2)) + 2  # для формата n групп по 4, из каждой выходит 2 и дальше играют навылет

    def player_singles_chekin(self, player):
        if self._format == 'S':
            if self._status == 'CI' and self._players_enrolled < self._players_num:
                if self._gender == 'X' or self._gender == player._gender:
                    self._players.add(player)
                    self._players_enrolled += 1
                    self.save()
                    if self._players_enrolled == self._players_num:
                        self.start()

    def player_doubles_checkin_as_pair(self, pair):  # TODO
        pass

    def player_doubles_checkin_as_single(self, single):  # TODO
        pass

    def init_matches_singles(self):
        if self._type == 'RM':
            players = []
            if self._current_stage == 1:

                for player in self._players:
                    players.add(player)
                if players:
                    random.shuffle(players)
                else:
                    print ('No players added. The current stage should be the 1st')

            elif self._current_stage > 1:
                players_unsorted = {}
                for player in self._players:
                    player_tournament = PlayerTournament.objects.get(_player = player, _tournament = self.id)
                    players_unsorted[player] = int(player_tournament._flow)
                if players_unsorted:
                    players_sorted = dict(sorted(players_unsorted.items(), key=lambda item: item[1], reverse=True))
                    players = list(players_sorted.keys())
                else:
                    print ('No players added. The current stage should be later than the 1st')
            else:
                print ('Error with stage - singles - _current_stage < 1')

            if players:
                self.players_add_in_RM(players)
            else:
                print('No players added. Error')

        elif self._type == 'GM':
            players = []
            if self._current_stage == 1:
                for player in self._players:
                    players.add(player)
                self.players_add_in_GM(players)

        elif self._type == 'OS':
            pass

        else:
            pass

    def players_add_in_RM(self, players):
        for i in range(0, len(players), 2):
            match = Match.objects.create(_tournament=self.id, _tournament_stage=self._current_stage)
            match._players.add(players[i])
            match._players.add(players[i + 1])
            match_player_one = PlayerMatch.objects.get(_player=players[i], _match=match)
            match_player_two = PlayerMatch.objects.get(_player=players[i + 1], _match=match)

            match_player_one._opponent = players[i + 1]
            match_player_one.save()
            match_player_two._opponent = players[i]
            match_player_two.save()
            # match_player_one = PlayerMatch.objects.create(_player = players[i], _opponent = players[i+1], _match = match) # на всякий случай это оставлю тут, тк я не уверен, что плейерматч создается автоматом при добавлении плейера в матч
            # match_players_two = PlayerMatch.objects.create(_player = players[i+1], _opponent = players[i], _match = match) # по идее должно быть так, но если нет, тогда надо будет создавать вручную

    # def players_add_in_GM(self, players):
    #     for i in range(len(players)):
    #         for j in range (i):



    def init_matches_doubles(self):
        if self._current_stage == 1:
            pass
        elif self._current_stage > 1:
            pass
        else:
            print('Error with stage - doubles - _current_stage < 1')

    def start(self):
        if self._players_enrolled == self._players_num:
            self._status = 'AC'
            self._current_stage = 1

            self._start_date = tomorrow
            self._current_stage_start_date = tomorrow
            self._current_stage_end_date = next_monday

            if self._format == 'S':
                self.init_matches_singles()
            elif self._format == 'D':
                self.init_matches_doubles()

            self.save()

    def finish(self):
        pass

    def next_stage(self):
        self._current_stage += 1
        if self._current_stage > self._stages_max:
            self.save()
            self.finish()
        else:
            self._current_stage_start_date = tomorrow
            self._current_stage_end_date = next_monday

            if self._format == 'S':
                self.init_matches_singles()
            elif self._format == 'D':
                self.init_matches_doubles()

            self.save()

    def current_stage_extend(self, days_delta):
        if self._current_stage_end_date:
            self._current_stage_end_date += timedelta(days=days_delta)
            self.save()


class Pair(models.Model):
    _player1 = models.ForeignKey(Player, on_delete=models.SET(DELETED_PLAYER), null=True, related_name='pair_p1_set')
    _player2 = models.ForeignKey(Player, on_delete=models.SET(DELETED_PLAYER), null=True, related_name='pair_p2_set')
    _gender = models.CharField(max_length=1, choices=TOURNAMENT_GENDER, null=True)
    _pair_since = models.DateField(auto_now_add=True)

    _is_complete = models.BooleanField(default=False)

    def add_player(self, player: Player):
        if not self._is_complete:
            if not self._player1:
                self._player1.add(player)
            elif not self._player2:
                self._player2.add(player)
                self._is_complete = True
                if self._player1._gender != self._player2._gender:
                    self._gender = 'X'
                else:
                    self._gender = self._player1._gender
                self.save()
            else:
                print(f'Impossible to add to this pair. Pair ID: {self.id}')
        else:
            print(f'Impossible to add to this pair. Pair ID: {self.id}')


class PlayerTournament(models.Model):
    _player = models.ForeignKey(Player, on_delete=models.SET(DELETED_PLAYER))
    _tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE)
    _result = models.PositiveIntegerField(null=True)
    _flow = models.CharField(max_length=8, default='')


class PairTournament(models.Model):
    _pair = models.ForeignKey(Pair, on_delete=models.SET(DELETED_PAIR))
    _tournament = models.ForeignKey(Tournament, on_delete=models.CASCADE)
    _result = models.PositiveIntegerField(null=True)
    _flow = models.CharField(max_length=5, default='')


class PlayerMatch(models.Model):
    _player = models.ForeignKey('Player', on_delete=models.SET(DELETED_PLAYER), related_name='the_player')
    _opponent = models.ForeignKey('Player', on_delete=models.SET(DELETED_PLAYER), related_name='the_opponent')
    _match = models.ForeignKey('Match', on_delete=models.CASCADE, related_name='player_matches')
    _is_winner = models.BooleanField(null=True)
    _is_withdrawn = models.BooleanField(default=False)

    _set1_score = models.PositiveSmallIntegerField(default=0)
    _set1_tie_score = models.PositiveSmallIntegerField(null=True)
    _set1_won = models.BooleanField(null=True)

    _set2_score = models.PositiveSmallIntegerField(null=True)
    _set2_tie_score = models.PositiveSmallIntegerField(null=True)
    _set2_won = models.BooleanField(null=True)

    _set3_score = models.PositiveSmallIntegerField(null=True)
    _set3_tie_score = models.PositiveSmallIntegerField(null=True)
    _set3_won = models.BooleanField(null=True)

    _set3_is_tiebreak = models.BooleanField(null=True)


class PairMatch(models.Model):
    _pair = models.ForeignKey('Pair', on_delete=models.SET(DELETED_PAIR), related_name='the_pair')
    _opponent = models.ForeignKey('Pair', on_delete=models.SET(DELETED_PAIR), related_name='the_opponent_pair')
    _match = models.ForeignKey('Match', on_delete=models.CASCADE, related_name='pair_matches')
    _is_winner = models.BooleanField(null=True)
    _is_withdrawn = models.BooleanField(default=False)

    _set1_score = models.PositiveSmallIntegerField(default=0)
    _set1_tie_score = models.PositiveSmallIntegerField(null=True)
    _set1_won = models.BooleanField(null=True)

    _set2_score = models.PositiveSmallIntegerField(null=True)
    _set2_tie_score = models.PositiveSmallIntegerField(null=True)
    _set2_won = models.BooleanField(null=True)

    _set3_score = models.PositiveSmallIntegerField(null=True)
    _set3_tie_score = models.PositiveSmallIntegerField(null=True)
    _set3_won = models.BooleanField(null=True)

    _set3_is_tiebreak = models.BooleanField(null=True)


class PlayerSeasonStats(models.Model):
    _player = models.ForeignKey(Player, on_delete=models.CASCADE)
    _season = models.PositiveIntegerField(default=CURRENT_SEASON)
    _pts = models.PositiveIntegerField(default=0)
    _final_rating_pts = models.PositiveIntegerField(default=1000)
    _start_rating_pts = models.PositiveIntegerField(default=1000)
    _max_rating_pts = models.PositiveIntegerField(default=1000)
    _min_rating_pts = models.PositiveIntegerField(default=1000)
    _matches_played = models.PositiveIntegerField(default=0)
    _matches_won = models.PositiveIntegerField(default=0)
    _matches_lost = models.PositiveIntegerField(default=0)


class PlayerTotalStats(models.Model):
    _player = models.ForeignKey(Player, on_delete=models.CASCADE)
    _current_rating_pts = models.PositiveIntegerField(default=1000)
    _max_rating_pts = models.PositiveIntegerField(default=1000)
    _min_rating_pts = models.PositiveIntegerField(default=1000)
    _matches_played = models.PositiveIntegerField(default=0)
    _matches_won = models.PositiveIntegerField(default=0)
    _matches_lost = models.PositiveIntegerField(default=0)


class PairSeasonStats(models.Model):
    _pair = models.ForeignKey('Pair', on_delete=models.CASCADE)
    _season = models.PositiveIntegerField(default=CURRENT_SEASON)
    _pts = models.PositiveIntegerField(default=0)
    _final_rating_pts = models.PositiveIntegerField(default=1000)
    _start_rating_pts = models.PositiveIntegerField(default=1000)
    _max_rating_pts = models.PositiveIntegerField(default=1000)
    _min_rating_pts = models.PositiveIntegerField(default=1000)
    _matches_played = models.PositiveIntegerField(default=0)
    _matches_won = models.PositiveIntegerField(default=0)
    _matches_lost = models.PositiveIntegerField(default=0)


class PairTotalStats(models.Model):
    _pair = models.ForeignKey('Pair', on_delete=models.CASCADE)
    _current_rating_pts = models.PositiveIntegerField(default=1000)
    _max_rating_pts = models.PositiveIntegerField(default=1000)
    _min_rating_pts = models.PositiveIntegerField(default=1000)
    _matches_played = models.PositiveIntegerField(default=0)
    _matches_won = models.PositiveIntegerField(default=0)
    _matches_lost = models.PositiveIntegerField(default=0)
