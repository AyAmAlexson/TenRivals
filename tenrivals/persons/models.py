from django.db import models
from django.contrib.auth.models import AbstractUser


class CustomUser(AbstractUser):
    mobile = models.CharField(max_length=15)
    is_player = models.BooleanField(default=True)
    is_author = models.BooleanField(default=False)

    def __str__(self):
        return self.username


class Author(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='author')
    bio = models.TextField()
    articles_written = models.PositiveIntegerField()