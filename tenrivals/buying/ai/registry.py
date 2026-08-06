"""Provider registry: business logic asks for get_ai_provider() and never
imports a concrete vendor implementation.

Provider selection is driven only by settings.BUYING_AI_PROVIDER (Config Var).
"""

from django.conf import settings

from .base import AIProvider, AIProviderNotConfigured

# Map Config Var values → importable provider classes. Adding a vendor requires
# a one-time code registration here; switching models does not.
_PROVIDERS = {
    'openai': 'buying.ai.providers.openai_provider.OpenAIProvider',
}


def get_ai_provider() -> AIProvider:
    name = (getattr(settings, 'BUYING_AI_PROVIDER', '') or '').strip().lower()
    if not name:
        raise AIProviderNotConfigured('BUYING_AI_PROVIDER is not set')
    dotted = _PROVIDERS.get(name)
    if not dotted:
        raise AIProviderNotConfigured(
            f'Unknown AI provider "{name}". Supported: {", ".join(sorted(_PROVIDERS))}'
        )
    module_path, class_name = dotted.rsplit('.', 1)
    import importlib

    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()
