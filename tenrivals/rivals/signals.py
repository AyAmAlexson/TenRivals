from django.db import models
from django.db.models.signals import pre_save, post_delete # Импортируем сигналы
from django.dispatch import receiver # Импортируем декоратор receiver
from persons.models import CustomUser
from .models import Player


@receiver(pre_save, sender=Player)
def delete_old_avatar_on_update(sender, instance, **kwargs):
    """
    Удаляет старый файл аватара из S3 при обновлении, если он изменился.
    """
    if not instance.pk: # Объект только создается, старого аватара нет
        return False

    try:
        # Получаем старый объект из базы данных
        old_instance = sender.objects.get(pk=instance.pk)
        old_avatar = old_instance.avatar
    except sender.DoesNotExist:
        return False # Не должно случиться при обновлении, но на всякий случай

    # Проверяем, есть ли новый аватар и отличается ли он от старого
    new_avatar = instance.avatar
    if not old_avatar: # Старого аватара не было
        return False

    if old_avatar != new_avatar:
        # Проверяем, что старый файл действительно существует в хранилище
        if old_avatar and hasattr(old_avatar, 'storage') and old_avatar.storage.exists(old_avatar.name):
             # Удаляем старый файл. save=False ВАЖНО, чтобы не вызвать pre_save рекурсивно!
             old_avatar.delete(save=False)

@receiver(post_delete, sender=Player)
def delete_avatar_on_delete(sender, instance, **kwargs):
    """
    Удаляет файл аватара из S3 после удаления объекта Player.
    """
    # Проверяем, был ли у объекта аватар
    if instance.avatar:
        # Проверяем, что файл существует в хранилище перед удалением
        if hasattr(instance.avatar, 'storage') and instance.avatar.storage.exists(instance.avatar.name):
            # Удаляем файл из хранилища
            instance.avatar.delete(save=False) # save=False здесь не так критично, как в pre_save, но для единообразия

# --- КОНЕЦ ДОБАВЛЕНИЯ СИГНАЛОВ ---

# ... остальной код models.py ...
