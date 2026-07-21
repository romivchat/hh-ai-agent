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

    def test_suitability_rejects_unstructured_answer(self) -> None:
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value="probably yes"),
        ):
            with self.assertRaises(ai_analyzer.OllamaUnavailableError):
                asyncio.run(ai_analyzer.is_vacancy_suitable("Python", "Backend"))

    def test_suitability_uses_boolean_schema(self) -> None:
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value='{"suitable": false}'),
        ) as ask:
            self.assertFalse(
                asyncio.run(ai_analyzer.is_vacancy_suitable("Project", "Build"))
            )

        response_format = ask.await_args.kwargs["response_format"]
        self.assertEqual(
            response_format["properties"]["suitable"], {"type": "boolean"}
        )

    def test_cover_letter_rejects_contacts_and_wrong_signature(self) -> None:
        with mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"):
            issues = ai_analyzer._cover_letter_issues(
                "Роман Иванов\n+79991234567\nroman@example.com\nС уважением, Роман"
            )

        self.assertIn("нельзя добавлять email", issues)
        self.assertIn("нельзя добавлять телефон", issues)
        self.assertIn("запрещена формула «С уважением»", issues)

    def test_cover_letter_rejects_unsolicited_github_claims(self) -> None:
        with (
            mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"),
            mock.patch.object(
                ai_analyzer, "GITHUB_URL", "https://github.com/romivchat"
            ),
        ):
            issues = ai_analyzer._cover_letter_issues(
                "Здравствуйте!\nНа GitHub есть мои коммерческие AI-проекты.\n"
                "Роман Иванов",
                "Ищем Product Owner AI-платформы",
            )

        self.assertIn(
            "GitHub можно упоминать только по прямому запросу вакансии", issues
        )

    def test_cover_letter_rejects_commercial_llm_claims(self) -> None:
        with mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"):
            issues = ai_analyzer._cover_letter_issues(
                "Здравствуйте!\nРазвивал LLM-функции в мобильном банке.\n"
                "Роман Иванов"
            )

        self.assertTrue(any("про LLM можно написать" in issue for issue in issues))

    def test_cover_letter_retries_after_invalid_draft(self) -> None:
        invalid = "Дорогой Роман, I am ready. С уважением, Роман"
        valid = "Здравствуйте!\n\nПодхожу по опыту.\n\nРоман Иванов"
        with (
            mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"),
            mock.patch(
                "ai_analyzer._ask_ollama",
                new=mock.AsyncMock(side_effect=[invalid, valid]),
            ) as ask,
            mock.patch(
                "ai_analyzer._cover_letter_fact_issues",
                new=mock.AsyncMock(return_value=[]),
            ),
        ):
            result = asyncio.run(
                ai_analyzer.generate_cover_letter("Product Owner", "Fintech")
            )

        self.assertEqual(result, valid)
        self.assertEqual(ask.await_count, 2)

    def test_cover_letter_fact_check_rejects_moved_achievement(self) -> None:
        answer = '{"valid": false}'
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value=answer),
        ):
            issues = asyncio.run(
                ai_analyzer._cover_letter_fact_issues("Проверяемое письмо")
            )

        self.assertEqual(
            issues,
            ["проверка фактов: письмо содержит неподтверждённое утверждение"],
        )


if __name__ == "__main__":
    unittest.main()
