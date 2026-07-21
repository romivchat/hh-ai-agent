import asyncio
import unittest
from pathlib import Path
from unittest import mock

import ai_analyzer


class AiAnalyzerSafetyTest(unittest.TestCase):
    def test_source_author_identity_is_not_hardcoded(self) -> None:
        source = Path(ai_analyzer.__file__).read_text(encoding="utf-8")

        self.assertNotIn("fikstt2", source)
        self.assertNotIn("VisionForge", source)
        self.assertNotIn("Евгений", source)

    def test_suitability_propagates_ollama_outage(self) -> None:
        error = ai_analyzer.OllamaUnavailableError("offline")
        with mock.patch("ai_analyzer._ask_ollama", side_effect=error):
            with self.assertRaises(ai_analyzer.OllamaUnavailableError):
                asyncio.run(ai_analyzer.is_vacancy_suitable("Python", "Backend"))

    def test_cover_letter_never_uses_generic_fallback(self) -> None:
        error = ai_analyzer.OllamaUnavailableError("offline")
        with mock.patch("ai_analyzer._ask_ollama", side_effect=error):
            with self.assertRaises(ai_analyzer.OllamaUnavailableError):
                asyncio.run(ai_analyzer.generate_cover_letter("Python", "Backend"))

    def test_suitability_requires_exact_yes_or_no(self) -> None:
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value="probably yes"),
        ):
            with self.assertRaises(ai_analyzer.OllamaUnavailableError):
                asyncio.run(ai_analyzer.is_vacancy_suitable("Python", "Backend"))


if __name__ == "__main__":
    unittest.main()
