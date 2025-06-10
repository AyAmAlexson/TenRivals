from django.shortcuts import get_object_or_404
from .models import Match, PlayerMatch, TimelineEvent, PlayerSeasonStats, Tournament, PlayerTournament, Award, PlayerCurrentStats, PlayerSeasonStats, Player
from django.urls import reverse
from datetime import datetime, date, timedelta
import logging
from django.db.models import Sum, Min, Max, OuterRef, Subquery
from django.db import transaction
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)
from .const import DEFAULT_NTRP, DEFAULT_RANKING

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
def current_season():
    return date.today().year




def apply_award_service(award:Award):
    """
        Применяет очки из Award к статистике игрока за сезон/неделю.
    """
    if award.is_implemented:
        logger.warning(f"Attempted to apply already implemented award {award.pk}.")
        return False

    try:
        player_season_stats_week = PlayerSeasonStats.objects.get(season=award.season, week=award.week, player=award.player)
        logger.debug(f"Applying award {award.pk} to player {award.player.pk} in season {award.season} week {award.week}")


        if award.award_type == 'SP':
            logger.debug(f"Adding {award.amount} points to player {award.player.pk} in season {award.season} week {award.week}")
            player_season_stats_week.season_pts += award.amount
            award.is_implemented = True
            award.save()
            logger.debug(f"PlayerSeasonStats for player {award.player.pk} in season {award.season} week {award.week} updated to {player_season_stats_week.season_pts} points")
        
            event = TimelineEvent.objects.create(
                player=award.player,
                event_type='B',
                color='M',
                text=f"Awarded {award.amount} {award.award_type} for {award.received_via} in season {award.season} week {award.week}"
            )

            event.save()

        elif award.award_type == 'NT':
            logger.debug(f"Adding {award.amount} NTRP to player {award.player.pk} in season {award.season} week {award.week}")
            player_season_stats_week.season_final_NTRP += award.amount
            award.is_implemented = True
            award.save()
            logger.debug(f"PlayerSeasonStats for player {award.player.pk} in season {award.season} week {award.week} updated to {player_season_stats_week.season_final_NTRP} NTRP")
            event = TimelineEvent.objects.create(
                player=award.player,
                event_type='B',
                color='M',
                text=f"Awarded {award.amount} {award.award_type} for {award.received_via} in season {award.season} week {award.week}"
            )

            event.save()
        else:
            logger.error(f"Unknown award type {award.award_type} for award {award.pk}")
            return False
        
        player_season_stats_week.save()
        logger.debug(f"PlayerSeasonStats saved for player {award.player.pk} in season {award.season} week {award.week}")
        return True
    
    except PlayerSeasonStats.DoesNotExist:
        logger.error(f"Could not find PlayerSeasonStats for award {award.pk}")
        return False
    except Exception as e:
        logger.error(f"Error applying award {award.pk}: {e}", exc_info=True)
        return False


def rewoke_award_service(award:Award):
    """
        Снимает очки из Award с статистики игрока за сезон/неделю.
    """
    if not award.is_implemented:
        logger.warning(f"Attempted to rewoke already rewoke award {award.pk}.")
        return False
    
    try:
        if award.award_type == 'SP':
            player_season_stats_week = PlayerSeasonStats.objects.get(season=award.season, week=award.week, player=award.player)
            
    except PlayerSeasonStats.DoesNotExist:
        logger.error(f"Could not find PlayerSeasonStats for award {award.pk}")
        return False
    except Exception as e:
        logger.error(f"Error rewoving award {award.pk}: {e}", exc_info=True)
        return False
    
    



def implement_match_result_for_player_service(pss:PlayerSeasonStats, winner:bool, rt:bool, opp_rt:bool, pts_gained:int, NTRP_change:int):
    if winner:
        if not opp_rt:
            pss.matches_won += 1
            pss.matches_played += 1
    elif rt:
        pss.matches_rted += 1
        pss.matches_lost += 1
        pss.matches_played += 1
    else:
        pss.matches_lost += 1
        pss.matches_played += 1
    
    pss.season_pts += pts_gained
    pss.season_final_NTRP += NTRP_change
    pss.max_season_NTRP = max(pss.max_season_NTRP, pss.season_final_NTRP)
    pss.min_season_NTRP = min(pss.min_season_NTRP, pss.season_final_NTRP)

    pss.save()

def apply_match_result_service(player_pm_pk):
    player_pm = get_object_or_404(PlayerMatch, pk=player_pm_pk)
    match = player_pm.match
    player = player_pm.player
    if not player_pm.is_implemented:
        player_season_stats_week,created = PlayerSeasonStats.objects.get_or_create(season=match.date.year, week=match.date.isocalendar()[1], player=player)
        implement_match_result_for_player_service(pss=player_season_stats_week, winner=player_pm.is_winner, rt=player_pm.is_withdrawn, opp_rt=player_pm.opponent_is_withdrawn, pts_gained=player_pm.season_pts_gained, NTRP_change=player_pm.NTRP_change)

        player_season_stats_later_weeks = PlayerSeasonStats.objects.filter(season=match.date.year, week__gt=match.date.isocalendar()[1], player=player)
        for player_season_stats_week in player_season_stats_later_weeks:
            implement_match_result_for_player_service(pss=player_season_stats_week, winner=player_pm.is_winner, rt=player_pm.is_withdrawn, opp_rt=player_pm.opponent_is_withdrawn, pts_gained=player_pm.season_pts_gained, NTRP_change=player_pm.NTRP_change)
        player_pm.is_implemented = True
        player_pm.save()

        player_tournament, created = PlayerTournament.objects.get_or_create(player=player, tournament=match.tournament)
        if player_tournament.flow:
            if player_pm.is_winner:
                player_tournament.flow += 'W'
            else:
                player_tournament.flow += 'L'
        else:
            if player_pm.is_winner:
                player_tournament.flow = 'W'
            else:
                player_tournament.flow = 'L'
        player_tournament.save()
        
        return True
    else:
        return False



def exclude_match_result_for_player_service(pss:PlayerSeasonStats, winner:bool, rt:bool, opp_rt:bool, pts_gained:int, NTRP_change:int):
    if winner:
        if not opp_rt:
            pss.matches_won = max(0, pss.matches_won - 1)
            pss.matches_played = max(0, pss.matches_played - 1)
    elif rt:
        pss.matches_rted = max(0, pss.matches_rted - 1)
        pss.matches_lost = max(0, pss.matches_lost - 1)
        pss.matches_played = max(0, pss.matches_played - 1)
    else:
        pss.matches_lost = max(0, pss.matches_lost - 1)
        pss.matches_played = max(0, pss.matches_played - 1)
    
    if pss.max_season_NTRP == pss.season_final_NTRP:
        pss.max_season_NTRP = max(0, pss.max_season_NTRP - NTRP_change)
    if pss.min_season_NTRP == pss.season_final_NTRP:
        pss.min_season_NTRP = max(0, pss.season_final_NTRP - NTRP_change)

    pss.season_pts = max(0, pss.season_pts - pts_gained)
    pss.season_final_NTRP = max(0, pss.season_final_NTRP - NTRP_change)
    
    pss.save()


def delete_match_result_service(player_pm_pk):
    player_pm = get_object_or_404(PlayerMatch, pk=player_pm_pk)
    match = player_pm.match
    player = player_pm.player
    
    player_season_stats_week = PlayerSeasonStats.objects.get(season=match.date.year, week=match.date.isocalendar()[1], player=player)
    player_season_stats_week.exclude_match_result(winner=player_pm.is_winner, rt=player_pm.is_withdrawn, opp_rt=player_pm.opponent_is_withdrawn, pts_gained=player_pm.season_pts_gained, NTRP_change=player_pm.NTRP_change)
    
    
    player_season_stats_later_weeks = PlayerSeasonStats.objects.filter(season=match.date.year, week__gt=match.date.isocalendar()[1], player=player)
    for player_season_stats_week in player_season_stats_later_weeks:
        player_season_stats_week.exclude_match_result(winner=player_pm.is_winner, rt=player_pm.is_withdrawn, opp_rt=player_pm.opponent_is_withdrawn, pts_gained=player_pm.season_pts_gained, NTRP_change=player_pm.NTRP_change)
    player_pm.is_implemented = False

    player_tournament = PlayerTournament.objects.get(player=player, tournament=match.tournament)
    if player_tournament.flow:
        player_tournament.flow = player_tournament.flow[:-1]
    player_tournament.save()
    player_pm.save()






def approve_match_result_service(pk, forced=False, by_timeout=False):
    match = get_object_or_404(Match, pk=pk)
    player = match.players.all().order_by('pk').first()
    opponent = match.players.all().order_by('pk').last()
    player_pm = PlayerMatch.objects.get(match=match, player=player, opponent=opponent)
    opponent_pm = PlayerMatch.objects.get(match=match, player=opponent, opponent=player)
    
    if player_pm.is_approved_by_player:
        player_pm.is_approved_by_player = True
        player_pm.is_approved_by_opponent = True
        opponent_pm.is_approved_by_player = True
        opponent_pm.is_approved_by_opponent = True
        TimelineEvent.objects.create(
            player=player,
            event_type='M',
            color='1',
            text=f"Match result approved by {opponent}" ,
            redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
            action_text='View Match Details'
        )

        if by_timeout:
            TimelineEvent.objects.create(
                player=opponent,
                event_type='M',
                color='1',
                text=f"Match result with {player} approved by timeout" ,
            )

    elif opponent_pm.is_approved_by_player:
        player_pm.is_approved_by_player = True
        player_pm.is_approved_by_opponent = True
        opponent_pm.is_approved_by_player = True
        opponent_pm.is_approved_by_opponent = True
        TimelineEvent.objects.create(
            player=opponent,
            event_type='M',
            color='1',
            text=f"Match result approved by {player}" ,
            redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
            action_text='View Match Details'
        )

        if by_timeout:
            TimelineEvent.objects.create(
                player=player,
                event_type='M',
                color='1',
                text=f"Match result with {opponent} approved by timeout" ,
                redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
                action_text='View Match Details'
            )


    #Forced approval call from admin
    elif forced:
        player_pm.is_approved_by_player = True
        player_pm.is_approved_by_opponent = True
        opponent_pm.is_approved_by_player = True
        opponent_pm.is_approved_by_opponent = True
        TimelineEvent.objects.create(
            player=player,
            event_type='M',
            color='1',
            text=f"Match result with {opponent} approved by admin" ,
            redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
            action_text='View Match Details'
        )
        TimelineEvent.objects.create(
            player=opponent,
            event_type='M',
            color='1',
            text=f"Match result with {player} approved by admin" ,
            redirect_url=reverse('rivals:match_detail', kwargs={'pk': match.pk}),
            action_text='View Match Details'
        )
    else:
        return False


    player_pm.save()
    opponent_pm.save()
    
    try:
        return apply_match_result_service(player_pm.pk) and apply_match_result_service(opponent_pm.pk)
    except Exception as e:
        print(f"Error applying match result: {e}")
        return False 
    


def reopen_match_result_service(match_pk):
    match = get_object_or_404(Match, pk=match_pk)
    player = match.players.all().order_by('pk').first()
    opponent = match.players.all().order_by('pk').last()
    player_pm = PlayerMatch.objects.get(match=match, player=player, opponent=opponent)
    opponent_pm = PlayerMatch.objects.get(match=match, player=opponent, opponent=player)

    try:
        delete_match_result_service(player_pm.pk)
        delete_match_result_service(opponent_pm.pk)
    except Exception as e:
        print(f"Error deleting match result: {e}")
        return False

    player_pm.is_implemented = False
    opponent_pm.is_implemented = False

    player_pm.is_approved_by_player = False
    player_pm.is_approved_by_opponent = False
    opponent_pm.is_approved_by_player = False
    opponent_pm.is_approved_by_opponent = False

    player_pm.save()
    opponent_pm.save()
    
    return True

def check_overdue_matches_service():
    today = datetime.now().date()
    active_tournaments = Tournament.objects.filter(status='AC', current_stage_end_date__isnull=False, current_stage_end_date__lt=today)
    count = 0
    for tournament in active_tournaments:
        if tournament.current_stage_end_date is not None:
            matches_to_check = Match.objects.filter(tournament=tournament, status='PD')
            
            for match in matches_to_check:
                count += 1
                match.status = 'OD'
                match.save()
                print(f"Match {match.id} is overdue")
    
    if count > 0:
        return count
    else:
        return False 
    
def update_current_player_stats_service(player: Player) -> bool:
    """
    Обновляет текущую статистику игрока на основе его сезонной статистики.
    
    Args:
        player (Player): объект игрока
        
    Returns:
        bool: True если обновление прошло успешно, False в случае ошибки
        
    Raises:
        ValidationError: если player невалидный
    """
    if not player:
        raise ValidationError("Player object is required")
        
    try:
        with transaction.atomic():
            pcs, _ = PlayerCurrentStats.objects.get_or_create(player=player)
            
            # Получаем последнюю статистику игрока
            pss_last_week = PlayerSeasonStats.objects.filter(
                player=player
            ).order_by('-week', '-season').first()
            
            if not pss_last_week:
                logger.warning(f"No PlayerSeasonStats found for player {player.pk}")
                _set_default_stats(pcs)
                return True
                
            # Устанавливаем текущий NTRP
            pcs.current_NTRP = pss_last_week.season_final_NTRP
            
            # Подзапрос для получения последних недель каждого сезона
            last_weeks = PlayerSeasonStats.objects.filter(
                player=player,
                season=OuterRef('season')
            ).order_by('-week').values('week')[:1]
            
            # Получаем агрегированную статистику по всем сезонам
            pss_all_last_weeks = PlayerSeasonStats.objects.filter(
                player=player,
                week=Subquery(last_weeks)
            ).aggregate(
                total_matches_played=Sum('matches_played'),
                total_matches_won=Sum('matches_won'),
                total_matches_lost=Sum('matches_lost'),
                total_matches_rted=Sum('matches_rted'),
                min_ntrp=Min('min_season_NTRP'),
                max_ntrp=Max('max_season_NTRP')
            )
            
            # Обновляем общую статистику с проверкой на None
            pcs.matches_played = pss_all_last_weeks['total_matches_played'] or 0
            pcs.matches_won = pss_all_last_weeks['total_matches_won'] or 0
            pcs.matches_lost = pss_all_last_weeks['total_matches_lost'] or 0
            pcs.matches_rted = pss_all_last_weeks['total_matches_rted'] or 0
            pcs.min_NTRP = pss_all_last_weeks['min_ntrp'] or DEFAULT_NTRP
            pcs.max_NTRP = pss_all_last_weeks['max_ntrp'] or DEFAULT_NTRP
            
            # Обновляем статистику текущего сезона
            if pss_last_week.season == current_season():
                _update_current_season_stats(pcs, pss_last_week)
            else:
                _reset_current_season_stats(pcs, pss_last_week)
            
            pcs.save()
            return True
            
    except Exception as e:
        logger.error(f"Error updating stats for player {player.pk}: {str(e)}", exc_info=True)
        return False

def _set_default_stats(pcs: PlayerCurrentStats) -> None:
    """Устанавливает значения по умолчанию для статистики"""
    pcs.current_NTRP = DEFAULT_NTRP
    pcs.matches_played = 0
    pcs.matches_won = 0
    pcs.matches_rted = 0
    pcs.matches_lost = 0
    pcs.min_NTRP = DEFAULT_NTRP
    pcs.max_NTRP = DEFAULT_NTRP
    pcs.matches_played_this_season = 0
    pcs.matches_won_this_season = 0
    pcs.matches_rted_this_season = 0
    pcs.matches_lost_this_season = 0
    pcs.season_pts = 0
    pcs.season_start_NTRP = DEFAULT_NTRP
    pcs.max_NTRP_this_season = DEFAULT_NTRP
    pcs.min_NTRP_this_season = DEFAULT_NTRP
    pcs.ranking_absolute = DEFAULT_RANKING
    pcs.ranking_category = DEFAULT_RANKING
    pcs.save()

def _update_current_season_stats(pcs: PlayerCurrentStats, pss_last_week: PlayerSeasonStats) -> None:
    """Обновляет статистику текущего сезона"""
    pcs.matches_played_this_season = pss_last_week.matches_played
    pcs.matches_won_this_season = pss_last_week.matches_won
    pcs.matches_rted_this_season = pss_last_week.matches_rted
    pcs.matches_lost_this_season = pss_last_week.matches_lost
    pcs.season_pts = pss_last_week.season_pts
    pcs.season_start_NTRP = pss_last_week.season_start_NTRP
    pcs.max_NTRP_this_season = pss_last_week.max_season_NTRP
    pcs.min_NTRP_this_season = pss_last_week.min_season_NTRP
    pcs.ranking_absolute = pss_last_week.ranking_absolute
    pcs.ranking_category = pss_last_week.ranking_category

def _reset_current_season_stats(pcs: PlayerCurrentStats, pss_last_week: PlayerSeasonStats) -> None:
    """Сбрасывает статистику для нового сезона"""
    pcs.matches_played_this_season = 0
    pcs.matches_won_this_season = 0
    pcs.matches_rted_this_season = 0
    pcs.matches_lost_this_season = 0
    pcs.season_pts = 0
    pcs.season_start_NTRP = pss_last_week.season_final_NTRP
    pcs.max_NTRP_this_season = pss_last_week.season_final_NTRP
    pcs.min_NTRP_this_season = pss_last_week.season_final_NTRP
    pcs.ranking_absolute = DEFAULT_RANKING
    pcs.ranking_category = DEFAULT_RANKING


def pss_special_get_or_create_service(player: Player) -> tuple[PlayerSeasonStats, bool]:
    """
    Returns:
        tuple[PlayerSeasonStats, bool]: PlayerSeasonStats object and boolean indicating if the player is new
        PlayerSeasonStats: PlayerSeasonStats object
        bool: True if the player is new, False otherwise
    """
    try:

        pss, created = PlayerSeasonStats.objects.get_or_create(player=player, season=current_season(), week=current_week())
        logger.debug(f"NICEEEE! PlayerSeasonStats created: {created}")
    except Exception as e:
        logger.error(f"Error getting or creating PlayerSeasonStats: {e}")

    if created:
        previous_pss = PlayerSeasonStats.objects.filter(player=player, season=current_season(), week__lt=current_week()).order_by('-week').first()
        if previous_pss:
            is_new_player = False
            if previous_pss.season == current_season():
                pss.season_start_NTRP = previous_pss.season_final_NTRP
                pss.season_final_NTRP = previous_pss.season_final_NTRP
                pss.season_pts = previous_pss.season_pts
                pss.matches_played = previous_pss.matches_played
                pss.matches_won = previous_pss.matches_won
                pss.matches_lost = previous_pss.matches_lost
                pss.matches_rted = previous_pss.matches_rted
                pss.max_season_NTRP = previous_pss.max_season_NTRP
                pss.min_season_NTRP = previous_pss.min_season_NTRP
                pss.save()
            else:
                is_new_player = True
                pss.season_start_NTRP = previous_pss.season_final_NTRP
                pss.season_final_NTRP = previous_pss.season_final_NTRP
                pss.season_pts = 0
                pss.matches_played = previous_pss.matches_played
                pss.matches_won = previous_pss.matches_won
                pss.matches_lost = previous_pss.matches_lost
                pss.matches_rted = previous_pss.matches_rted
                pss.max_season_NTRP = previous_pss.season_final_NTRP
                pss.min_season_NTRP = previous_pss.season_final_NTRP
                pss.save()
    else:
        is_new_player = False

    
    return pss, is_new_player