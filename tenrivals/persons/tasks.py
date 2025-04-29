from celery import shared_task

@shared_task
def delete_outdated_password_reset_tokens():
    from persons.services import delete_outdated_password_reset_tokens_service
    return delete_outdated_password_reset_tokens_service()