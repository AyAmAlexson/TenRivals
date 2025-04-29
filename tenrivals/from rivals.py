from rivals.models import *
from rivals.views import *
import random
for pl in Player.objects.all():
    initial_ntrp = random.randint(600,3500)
    max=initial_ntrp
    min=initial_ntrp
    pts=0
    games=0
    wins=0
    for week in range(1,3):
        plss=PlayerSeasonStats.objects.get(player=pl, season=2025, week=week)
        plss.season=2025
        plss.week=week
        plss.geo=pl.geo
        pts+=random.randint(0,50)
        plss.season_pts=pts
        ntrp=initial_ntrp+random.randint(-200,+200)
        plss.season_start_NTRP=initial_ntrp
        plss.season_final_NTRP=ntrp
        if ntrp>max:
            max=ntrp
        plss.max_season_NTRP=max
        if ntrp<min:
            min=ntrp
        plss.min_season_NTRP=min
        games+=random.randint(2,5)
        wins=random.randint(wins,games)
        plss.matches_won=wins
        plss.matches_played=games
        plss.matches_lost=games-wins
        plss.save()




update_season_ranking(2025,3)


for week in plss:
     player_fields = vars(week)
     for field, value in player_fields.items():
         print(f"{field}: {value}")