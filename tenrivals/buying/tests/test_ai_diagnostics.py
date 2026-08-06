"""AI config / diagnostics / schema tests (no live OpenAI calls)."""

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from buying.ai.diagnostics import build_diagnostics, record_ai_error
from buying.ai.providers.openai_provider import OpenAIProvider
from buying.ai.schemas import SchemaValidationError, validate_match
from buying.tests.utils import make_superuser


class MatchSchemaTests(SimpleTestCase):
    def test_valid_match(self):
        data = validate_match({
            'match_status': 'exact',
            'match_score': 0.92,
            'reasons': ['Brand and model match'],
        })
        self.assertEqual(data['match_status'], 'exact')

    def test_invalid_status(self):
        with self.assertRaises(SchemaValidationError):
            validate_match({'match_status': 'maybe', 'match_score': 0.5, 'reasons': []})


@override_settings(
    SECURE_SSL_REDIRECT=False,
    BUYING_AI_PROVIDER='openai',
    BUYING_NORMALIZATION_MODEL='gpt-test-norm',
    BUYING_MATCH_MODEL='gpt-test-match',
    BUYING_AI_TEMPERATURE=0,
    BUYING_AI_TIMEOUT=30,
    BUYING_AI_MAX_RETRIES=1,
    BUYING_OPENAI_API_KEY='',
)
class DiagnosticsTests(TestCase):
    def test_diagnostics_without_key(self):
        record_ai_error('unit-test error', operation='normalize')
        diag = build_diagnostics(probe=False)
        self.assertEqual(diag.provider, 'openai')
        self.assertEqual(diag.normalization_model, 'gpt-test-norm')
        self.assertEqual(diag.matching_model, 'gpt-test-match')
        self.assertEqual(diag.temperature, 0)
        self.assertFalse(diag.api_key_configured)
        self.assertEqual(diag.api_status, 'not_configured')
        self.assertIn('unit-test error', diag.last_error)

    def test_provider_reads_config_models(self):
        provider = OpenAIProvider()
        self.assertEqual(provider.normalization_model, 'gpt-test-norm')
        self.assertEqual(provider.match_model, 'gpt-test-match')
        self.assertEqual(provider.temperature, 0)
        self.assertEqual(provider.max_retries, 1)

    def test_diagnostics_page_loads(self):
        user = make_superuser()
        self.client.force_login(user)
        # Superuser may still need buying.view — grant via is_superuser in auth decorator
        response = self.client.get(reverse('administration:buying_ai_diagnostics'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI diagnostics')
        self.assertContains(response, 'gpt-test-norm')
