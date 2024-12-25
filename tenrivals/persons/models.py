from django.db import models
from django.contrib.auth.models import AbstractUser
from rivals.const import TR_GEOS, TR_CITIES


class CustomUser(AbstractUser):
    mobile = models.CharField(max_length=15)
    is_player = models.BooleanField(default=True)
    is_author = models.BooleanField(default=False)
    preferred_city = models.CharField(max_length=3, choices=TR_CITIES, default='TBI')
    preferred_geo = models.CharField(max_length=2, choices=TR_GEOS, default='GE')
    def __str__(self):
        return self.username


class Author(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='author')
    bio = models.TextField()
    articles_written = models.PositiveIntegerField()