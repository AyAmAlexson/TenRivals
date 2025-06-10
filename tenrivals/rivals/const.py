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
    ('CA', 'Cancelled'),
    ('OD', 'Overdue'),
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
    ('BEL', 'Belgrade, RS'),

]

CURRENT_SEASON = datetime.datetime.now().year

FEMALE_PLAYERS_AVATARS = [
    'avatars/photo___Caroline.png',
    'avatars/photo___Karina.png',
    'avatars/photo___Kate.png',
    'avatars/photo___Milena.png',
    'avatars/photo___Olga.png',
    'avatars/photo___Ruzanna.png',
]

MALE_PLAYERS_AVATARS = [
    'avatars/photo___Alex_2.png',
    'avatars/photo___Bogdan.png',
    'avatars/photo___Kamza.png',
    'avatars/photo___Mitya.png',
    'avatars/photo___Rufin.png',
    'avatars/photo___Sanzhu.png',

]

TOKEN_STATUS = [
    ('P', 'Pending'),
    ('U', 'Used'),
    ('E', 'Expired'),
]

TIMELINE_EVENT_TYPE = [
    ('M', 'Match'),
    ('R', 'Review'),
    ('C', 'Challenge'),
    ('N', 'New Rating'),
    ('T', 'Tournament'),
    ('P', 'Pair'),
    ('S', 'Success'),
    ('F', 'Failure'),
    ('O', 'Other'),
    ('E', 'Event'),
    ('I', 'Info'),
    ('W', 'Warning'),
    ('D', 'Danger'),
    ('L', 'Light'),
    ('H', 'High'),
    ('B', 'Onboarding'),
]

TIMELINE_EVENT_COLOR = [
    ('1', 'primary'),
    ('2', 'secondary'),
    ('I', 'info'),
    ('W', 'warning'),
    ('D', 'danger'),
    ('S', 'success'),
    ('G', 'green'),
    ('R', 'red'),
    ('B', 'blue'),
    ('Y', 'yellow'),
    ('P', 'purple'),
    ('O', 'orange'),
    ('L', 'light'),
    ('V', 'violet'),
    ('G', 'gray'),
    ('B', 'black'),
    ('T', 'turquoise'),
    ('M', 'magenta'),
    ('C', 'cyan'),
    ('A', 'aqua'),
]

ONBOARDING_ITEMS_COST = {
        'tg_verify': 5,
        'main_info': 3,
        'additional_info': 2,
        'avatar': 3,
        'wizard': 3,
        'email_verify': 1,
        'first_tournament': 3,
        'first_match': 3,
        'first_opponent_review': 2,

    }

AWARD_TYPE = [
    ('SP', 'Season Points'),
    ('NT', 'NTRP Points'),

]

AWARD_RECEIVED_VIA = [
    ('OB', 'Onboarding'),
    ('MW', 'Match Win'),
    ('ML', 'Match Loss'),
    ('MR', 'Match RTed'),
    ('SP', 'Stage Prolongation'),
    ('OR', 'Opponent Review'),
    ('WC', 'Wizard Completion'),
    ('TR', 'Tournament Result'),

    
]

DEFAULT_NTRP = 1000
DEFAULT_RANKING = 0