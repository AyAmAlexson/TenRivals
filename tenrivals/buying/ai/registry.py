"""Provider registry: business logic asks for get_ai_provider() and never
imports a concrete vendor implementation."""

from django.conf import settings

from .base import AIProvider, AIProviderNotConfigured


def get_ai_provider() -> AIProvider:
    name = getattr(settings, 'BUYING_AI_PROVIDER', 'openai')
    if name == 'openai':
        from .providers.openai_provider import OpenAIProvider

        return OpenAIProvider()
    raise AIProviderNotConfigured(f'Unknown AI provider "{name}"')
