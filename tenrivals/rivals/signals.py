from django.db import models
from django.db.models.signals import pre_save, post_delete, post_save # Импортируем сигналы
from django.dispatch import receiver # Импортируем декоратор receiver
from persons.models import CustomUser
from .models import Player, PlayerOnboarding, PlayerTournament, Award
from allauth.account.models import EmailAddress
from allauth.account.signals import email_confirmed
from .const import ONBOARDING_ITEMS_COST
from .services import apply_award_service



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

@receiver(email_confirmed)
def on_email_confirmed(sender, request, email_address, **kwargs):
    """
    Действие при подтверждении email-адреса.
    """
    user = email_address.user
    player = Player.objects.filter(user=user).first()
    if not player:
        return
    try:
        player_onboarding = PlayerOnboarding.objects.get(player=player)
        player_onboarding.ob_verify_email = True
        player_onboarding.save()
    except PlayerOnboarding.DoesNotExist:
        pass
    
    
@receiver(post_save, sender=Player)
def on_player_save(sender, instance, **kwargs):
    """
    Действие при сохранении объекта Player.
    """
    player_onboarding = PlayerOnboarding.objects.get(player=instance)
    if instance.first_name not in [None, '']:
        player_onboarding.ob_first_name = True
    if instance.last_name not in [None, '']:
        player_onboarding.ob_last_name = True
    if instance.birthdate not in [None, '']:
        player_onboarding.ob_birthdate = True
    if instance.gender not in [None, '']:
        player_onboarding.ob_gender = True
    if instance.city not in [None, '']:
        player_onboarding.ob_city = True
    if instance.avatar:
        player_onboarding.ob_avatar = True
    if instance.weight:
        player_onboarding.ob_weight = True
    if instance.height:
        player_onboarding.ob_height = True
    if instance.tennis_exp_years not in [None,]:
        player_onboarding.ob_tennis_exp_years = True
        
    if instance.availability:
        player_onboarding.ob_availability = True

    
    player_onboarding.save()


@receiver(post_save, sender=CustomUser)
def on_user_save(sender, instance, **kwargs):
    """
    Действие при сохранении объекта CustomUser.
    """
    if instance.is_telegram_verified:
        player_onboarding = PlayerOnboarding.objects.get(player=instance.player)
        if not player_onboarding.is_completed and not player_onboarding.ob_verify_tg:
            player_onboarding.ob_verify_tg = True
            player_onboarding.save()
            new_award = Award.objects.create(
                player=instance.player,
                award_type='SP',
                received_via='OB',
                amount=ONBOARDING_ITEMS_COST['tg_verify'],
            )   
            apply_award_service(award=new_award)
    else:
        pass



@receiver(post_save, sender=PlayerTournament)
def on_playertournament_save(sender, instance, **kwargs):
    """
    Действие при сохранении объекта PlayerTournament.
    """

    player = instance.player
    player_onboarding = PlayerOnboarding.objects.get(player=player)
    if not player_onboarding.is_completed and not player_onboarding.ob_first_tournament_registration:
        player_onboarding.ob_first_tournament_registration = True
        player_onboarding.save()
        new_award = Award.objects.create(
                player=instance.player,
                award_type='SP',
                received_via='OB',
                amount=ONBOARDING_ITEMS_COST['first_tournament'],
            )   
        apply_award_service(award=new_award)
        
