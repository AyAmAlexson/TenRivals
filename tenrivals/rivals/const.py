import datetime

TOURNAMENT_TYPE = [
    ('RM', 'Ranked Match'),
    ('GM', 'Group Match'),
    ('OS', 'Olympic System'),
]
TOURNAMENT_CATEGORY = [
    ('C0','Novice'),
    ('C1','Challenger'),
    # ('C2','Tour'),
    ('C3','Masters'),
    ('PR','Profi'),
]

TOURNAMENT_GENDER = [
    ('M', "Men"),
    ('X', "Mixed"),
    ('F', "Women"),
]

TOURNAMENT_FORMAT = [
    ('S', 'Singles'),
    ('D', 'Doubles'),
]

TOURNAMENT_STATUS = [
    ('CI', 'Check-in Open'),
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

TR_GEOS = [
    ('MT', 'Malta'),
    ('IT', 'Italy'),
    ('RS', 'Serbia'),
    ('GE', 'Georgia'),
    ('RU', 'Russia')
]

TR_CITIES = [
    ('MTA', 'Malta All, MT'),
    ('TBI', 'Tbilisi, GE'),
    ('BAT', 'Batumi, GE'),
    ('MSC', 'Moscow, RU'),
    ('SPB', 'St. Petersburg, RU'),
    ('MIL', 'Milano, IT'),
    ('ROM', 'Roma, IT'),
    ('BEL', 'Belgrad, RS'),

]

CURRENT_SEASON = datetime.datetime.now().year
