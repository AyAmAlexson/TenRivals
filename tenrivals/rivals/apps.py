from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)

class RivalsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rivals'

    def ready(self):
        try:
            import rivals.tasks  
            logger.info("Rivals tasks imported successfully.")
        except ImportError:
            logger.warning("Could not import rivals.tasks.")
        
        
        try:
            import rivals.signals 
            logger.info("Rivals signals imported successfully.")
        except ImportError:
            logger.warning("Could not import rivals.signals.")
