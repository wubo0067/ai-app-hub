import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
src_pkg = types.ModuleType("src")
src_pkg.__path__ = [str(root / "src")]
sys.modules.setdefault("src", src_pkg)
utils_pkg = types.ModuleType("src.utils")
utils_pkg.__path__ = [str(root / "src" / "utils")]
sys.modules.setdefault("src.utils", utils_pkg)

from src.utils.settings import get_settings

_REQUIRED = {
    "REASONING_API_KEY": "test-key",
    "REASONING_BASE_URL": "https://Example.COM/compatible-mode/v1",
    "REASONING_MODEL": "DeepSeek-V4.1-Flash",
}
_OPTIONAL_KEYS = (
    "REASONING_THINKING",
    "REASONING_EFFORT",
    "REASONING_TEMPERATURE",
    "REASONING_CONTEXT_WINDOW",
    "REASONING_MAX_OUTPUT_TOKENS",
    "REASONING_MIN_THINKING_OUTPUT_TOKENS",
    "REASONING_HISTORY_KEEP",
    "LANGSMITH_TRACING",
)


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        get_settings.cache_clear()
        self._patch = patch.dict(os.environ, {}, clear=False)
        self._patch.start()
        for key in (*_REQUIRED, *_OPTIONAL_KEYS):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._patch.stop()
        get_settings.cache_clear()

    def test_loads_required_values_without_lowercasing(self) -> None:
        with patch.dict(os.environ, _REQUIRED):
            settings = get_settings()

        self.assertEqual(settings.reasoning_api_key, "test-key")
        self.assertEqual(
            settings.reasoning_base_url, "https://Example.COM/compatible-mode/v1"
        )
        self.assertEqual(settings.reasoning_model, "DeepSeek-V4.1-Flash")

    def test_optional_values_use_defaults(self) -> None:
        with patch.dict(os.environ, _REQUIRED):
            settings = get_settings()

        self.assertEqual(settings.reasoning_thinking, "disabled")
        self.assertEqual(settings.reasoning_effort, "max")
        self.assertAlmostEqual(settings.reasoning_temperature, 0.1)
        self.assertEqual(settings.reasoning_context_window, 524_288)
        self.assertEqual(settings.reasoning_max_output_tokens, 48_000)
        self.assertEqual(settings.reasoning_min_thinking_output_tokens, 40_960)
        self.assertEqual(settings.reasoning_history_keep, 2)
        self.assertFalse(settings.langsmith_tracing)

    def test_missing_api_key_raises(self) -> None:
        with patch.dict(
            os.environ,
            {
                "REASONING_BASE_URL": "https://example.com",
                "REASONING_MODEL": "m",
            },
        ):
            with self.assertRaises(RuntimeError) as ctx:
                get_settings()

        self.assertIn("REASONING_API_KEY", str(ctx.exception))

    def test_invalid_boolean_raises(self) -> None:
        with patch.dict(os.environ, {**_REQUIRED, "LANGSMITH_TRACING": "maybe"}):
            with self.assertRaises(RuntimeError) as ctx:
                get_settings()

        self.assertIn("LANGSMITH_TRACING", str(ctx.exception))

    def test_invalid_temperature_raises(self) -> None:
        with patch.dict(os.environ, {**_REQUIRED, "REASONING_TEMPERATURE": "hot"}):
            with self.assertRaises(RuntimeError) as ctx:
                get_settings()

        self.assertIn("REASONING_TEMPERATURE", str(ctx.exception))

    def test_invalid_thinking_mode_raises(self) -> None:
        with patch.dict(os.environ, {**_REQUIRED, "REASONING_THINKING": "sometimes"}):
            with self.assertRaises(RuntimeError) as ctx:
                get_settings()

        self.assertIn("REASONING_THINKING", str(ctx.exception))

    def test_invalid_context_window_raises(self) -> None:
        with patch.dict(os.environ, {**_REQUIRED, "REASONING_CONTEXT_WINDOW": "big"}):
            with self.assertRaises(RuntimeError) as ctx:
                get_settings()

        self.assertIn("REASONING_CONTEXT_WINDOW", str(ctx.exception))

    def test_masked_summary_never_leaks_api_key(self) -> None:
        with patch.dict(os.environ, _REQUIRED):
            settings = get_settings()

        summary = settings.masked_summary()
        dumped = str(summary)
        self.assertNotIn("test-key", dumped)
        self.assertNotIn("reasoning_api_key", summary)
        self.assertEqual(summary["reasoning_model"], "DeepSeek-V4.1-Flash")


if __name__ == "__main__":
    unittest.main()