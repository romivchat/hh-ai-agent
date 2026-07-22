import asyncio
import re
import unittest
from pathlib import Path
from unittest import mock

import ai_analyzer


def candidate_profile() -> dict:
    return {
        "positioning": (
            "Я Senior Product Manager с 6 годами опыта в B2B- и B2C-финтехе, "
            "запуске новых направлений и развитии цифровых продуктов до "
            "масштаба 1 млн MAU."
        ),
        "specialization": (
            "Специализируюсь на продуктовой стратегии, discovery и delivery, "
            "росте ключевых метрик, проверке гипотез и управлении "
            "кросс-функциональными командами."
        ),
        "seniority": (
            "На senior-уровне определяю направление продукта, связываю решения "
            "с бизнес-результатом, работаю в условиях неопределённости и "
            "согласую интересы разработки, дизайна, аналитики и руководителей. "
            "Управлял командами до 12 человек и отвечал за полный цикл от "
            "исследования до запуска изменений."
        ),
        "forbidden_terms": ["СЕКРЕТНЫЙ РАБОТОДАТЕЛЬ", "СЕКРЕТНЫЙ ПРОДУКТ"],
        "facts": [
            {
                "id": "growth_fact",
                "focus_ids": ["growth", "b2b"],
                "text": (
                    "В B2B-продукте провёл полный цикл discovery и сформировал "
                    "стратегию развития. За период работы MAU рос на 20–30% "
                    "ежемесячно, а 30-дневный retention превысил 60%."
                ),
            },
            {
                "id": "discovery_fact",
                "focus_ids": ["discovery", "process"],
                "text": (
                    "Запустил практику A/B-тестирования и проверил более десяти "
                    "growth-гипотез. Подтверждённый эффект реализованных "
                    "экспериментов составил 40 млн рублей в год."
                ),
            },
            {
                "id": "unused_fact",
                "focus_ids": ["ai"],
                "text": "Этот факт не должен попасть в проверяемое письмо.",
            },
        ],
    }


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

    def test_focus_selector_uses_closed_enum(self) -> None:
        answer = '{"focus_ids": ["growth", "discovery"]}'
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value=answer),
        ) as ask:
            result = asyncio.run(
                ai_analyzer._select_cover_letter_focuses("Product", "Growth")
            )

        self.assertEqual(result, ["growth", "discovery"])
        schema = ask.await_args.kwargs["response_format"]
        enum = schema["properties"]["focus_ids"]["items"]["enum"]
        self.assertEqual(set(enum), set(ai_analyzer.COVER_LETTER_FOCUSES))

    def test_focus_selector_rejects_unknown_value(self) -> None:
        answer = '{"focus_ids": ["growth", "invented"]}'
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value=answer),
        ):
            with self.assertRaises(ai_analyzer.OllamaUnavailableError):
                asyncio.run(
                    ai_analyzer._select_cover_letter_focuses("Product", "Growth")
                )

    def test_explicit_ai_vacancy_always_gets_ai_focus(self) -> None:
        answer = '{"focus_ids": ["discovery", "launch"]}'
        with mock.patch(
            "ai_analyzer._ask_ollama",
            new=mock.AsyncMock(return_value=answer),
        ):
            result = asyncio.run(
                ai_analyzer._select_cover_letter_focuses(
                    "Product Owner AI-платформы", "Развитие LLM-функций"
                )
            )

        self.assertEqual(result, ["discovery", "ai"])

    def test_cover_letter_uses_only_approved_facts(self) -> None:
        profile = candidate_profile()
        with (
            mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"),
            mock.patch(
                "ai_analyzer._load_candidate_profile", return_value=profile
            ),
            mock.patch(
                "ai_analyzer._select_cover_letter_focuses",
                new=mock.AsyncMock(return_value=["growth", "discovery"]),
            ),
        ):
            letter = asyncio.run(
                ai_analyzer.generate_cover_letter("Senior Product", "Growth")
            )

        self.assertIn("MAU рос на 20–30%", letter)
        self.assertIn("40 млн рублей", letter)
        self.assertNotIn("Этот факт не должен попасть", letter)
        self.assertNotIn("СЕКРЕТНЫЙ РАБОТОДАТЕЛЬ", letter)
        self.assertTrue(letter.endswith("Роман Иванов"))
        word_count = len(re.findall(r"\b[\w-]+\b", letter))
        self.assertGreaterEqual(word_count, 150)
        self.assertLessEqual(word_count, 250)

    def test_cover_letter_model_only_selects_focuses(self) -> None:
        profile = candidate_profile()
        answer = '{"focus_ids": ["growth", "discovery"]}'
        with (
            mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"),
            mock.patch(
                "ai_analyzer._load_candidate_profile", return_value=profile
            ),
            mock.patch(
                "ai_analyzer._ask_ollama",
                new=mock.AsyncMock(return_value=answer),
            ) as ask,
        ):
            asyncio.run(
                ai_analyzer.generate_cover_letter("Senior Product", "Growth")
            )

        self.assertEqual(ask.await_count, 1)

    def test_cover_letter_rejects_forbidden_name_and_contacts(self) -> None:
        profile = candidate_profile()
        text = (
            "Здравствуйте!\n\nСЕКРЕТНЫЙ РАБОТОДАТЕЛЬ\n\n"
            "+79991234567 test@example.com\n\nТекст\n\nРоман Иванов"
        )
        with mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"):
            issues = ai_analyzer._cover_letter_issues(text, profile)

        self.assertIn("запрещено упоминание: СЕКРЕТНЫЙ РАБОТОДАТЕЛЬ", issues)
        self.assertIn("нельзя добавлять email", issues)
        self.assertIn("нельзя добавлять телефон", issues)


if __name__ == "__main__":
    unittest.main()
