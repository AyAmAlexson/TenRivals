from django.shortcuts import get_object_or_404
from .models import Match, PlayerMatch, TimelineEvent, PlayerSeasonStats, Tournament, PlayerTournament, Award
from django.urls import reverse
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

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
            
            logger.debug(f"PlayerSeasonStats for player {award.player.pk} in season {award.season} week {award.week} updated to {player_season_stats_week.season_pts} points")
        elif award.award_type == 'NT':
            logger.debug(f"Adding {award.amount} NTRP to player {award.player.pk} in season {award.season} week {award.week}")
            player_season_stats_week.season_final_NTRP += award.amount
            logger.debug(f"PlayerSeasonStats for player {award.player.pk} in season {award.season} week {award.week} updated to {player_season_stats_week.season_final_NTRP} NTRP")
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