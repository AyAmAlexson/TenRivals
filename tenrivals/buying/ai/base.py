"""AI provider abstraction.

Buying business logic never talks to a concrete LLM vendor. Every provider must
return data passing the same schema validation (buying.ai.schemas); free-form
LLM output is never used as a source of business data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class AIProviderError(Exception):
    """Provider-level failure: network, auth, invalid/unparseable response."""


class AIProviderNotConfigured(AIProviderError):
    """Provider cannot run (e.g. missing API key)."""


@dataclass
class AIResult:
    data: dict
    model_version: str
    prompt_version: str
    raw_response: dict = field(default_factory=dict)


class AIProvider:
    """Interface implemented by concrete providers (OpenAI today; others later)."""

    name: str = 'base'

    def normalize(self, text: str) -> AIResult:
        """Free-form client request -> validated normalization dict (schemas.py)."""
        raise NotImplementedError

    def match(self, normalized: dict, candidate: dict) -> AIResult:
        """Semantic product matching. Implemented in Phase 4."""
        raise NotImplementedError

    def generate_search_queries(self, normalized: dict) -> AIResult:
        """Alternative spellings / search strings. Implemented in Phase 4."""
        raise NotImplementedError
