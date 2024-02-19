import datetime

TOURNAMENT_TYPE = [
    ('RM', 'Ranked Match'),
    ('GM', 'Group Match'),
    ('OS', 'Olympic System'),
]
TOURNAMENT_CATEGORY = [
    ('C0','Novice'),
    ('C1','Challenger'),
    ('C2','Tour'),
    ('C3','Maters'),
    ('PR','Professionals'),
]

TOURNAMENT_GENDER = [
    ('M', "Men's Tournaments"),
    ('F', "Women's Tournaments"),
    ('X', "Mixed Tournaments")
]

TOURNAMENT_FORMAT = [
    ('S', 'Singles'),
    ('D', 'Doubles'),
]

TOURNAMENT_STATUS = [
    ('CI', 'Check-In'),
    ('AC', 'In Action'),
    ('FI', 'Finished'),
]

GENDER = [
    ('M', "Men's Tournaments"),
    ('F', "Women's Tournaments"),
]

DELETED_PLAYER = '0'
DELETED_PAIR = '0'

MATCH_STATUS = [
    ('PD', 'Pending'),
    ('FI', 'Finished'),
]

RR_GEOS = [
    ('MT', 'Malta'),
    ('IT', 'Italy'),
    ('RS', 'Serbia'),
    ('ME', 'Montenegro'),
    ('GE', 'Georgia'),
]

CURRENT_SEASON = datetime.datetime.now().year
