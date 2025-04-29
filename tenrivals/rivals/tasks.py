from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from .models import TimelineEvent

@shared_task
def test_celery():
    message = f"Test task executed at {timezone.now()}"
    print(message)  # Это появится в логах worker
    return message

@shared_task
def cleanup_old_events():
    # Вычисляем дату месяц назад
    month_ago = timezone.now() - timedelta(days=30)
    
    # Удаляем старые записи
    deleted_count = TimelineEvent.objects.filter(
        event_date__lt=month_ago
    ).delete()[0]
    
    return f'Deleted {deleted_count} old events' 

@shared_task
def check_overdue_matches():
 
    from .services import check_overdue_matches_service
    count = check_overdue_matches_service()
    if count > 0:
        return f'Overdue matches checked. {count} matches found' 
    else:
        return f'Overdue matches checked. No matches found' 


@shared_task
def approve_match_results_by_timeout():
    from .models import Match, PlayerMatch
    from .services import approve_match_result_service
    
    day_ago = timezone.now() - timedelta(days=1)
    month_ago = timezone.now() - timedelta(days=30)
    print(f"Starting approving matches by timeout")
    matches = Match.objects.filter(date__gte=month_ago)
    result = 0
    
    for match in matches:
        print(f"Match FOUND! {match.id}")
        try:
            player = match.players.all().order_by('pk').first()
            opponent = match.players.all().order_by('pk').last()
            player_pm = PlayerMatch.objects.get(match=match, player=player, opponent=opponent)
            opponent_pm = PlayerMatch.objects.get(match=match, player=opponent, opponent=player)
            
            print(f"Player match: {player_pm.last_updated>day_ago}, Opponent match: {opponent_pm.last_updated>day_ago}")
            if player_pm.last_updated > day_ago and opponent_pm.last_updated > day_ago:
                print(f"GOING to approve match {match.id}")
                if approve_match_result_service(match.id, by_timeout=True, forced=True):
                    result += 1
                    print(f"Approved match {match.id}")
        except Exception as e:
            print(f"Error approving match {match.id}: {e}")
            
    return f'Forced approval done. Approved {result} match results by timeout'



