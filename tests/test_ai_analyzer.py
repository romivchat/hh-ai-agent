import asyncio
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ai_analyzer


PBF_DESCRIPTION = " ".join(
    [
        "Обеспечивать своевременный и качественный выпуск релизов.",
        "Управлять процессом разработки и контролировать delivery.",
        "Опыт управления релизами от 2 лет.",
        "Знание Agile/Scrum.",
        "Понимание DevOps и CI/CD.",
        "Опыт с POS-терминалами будет преимуществом.",
        "Собственное производство с партнёрами из Китая.",
        "Просьба в сопроводительном письме указывать уровень дохода.",
    ]
)

PORTFOLIO_DESCRIPTION = " ".join(
    [
        "Управлять жизненным циклом кредитного портфеля.",
        "Развивать активацию, использование, остатки и удержание клиентов.",
        "Отвечать за качество и прибыльность портфеля.",
        "Работать с CRM, маркетингом, юридической функцией, рисками и аналитикой.",
        "Английский язык не ниже B2.",
        "Продукт развивается на рынке Латинской Америки.",
    ]
)


def evidence_item(
    item_id: str,
    kind: str,
    text: str,
    evidence: str,
    capabilities=None,
    request_kind: str = "other",
) -> dict:
    return {
        "id": item_id,
        "kind": kind,
        "text": text,
        "evidence": evidence,
        "capabilities": capabilities or [],
        "request_kind": request_kind,
    }


def candidate_profile() -> dict:
    return {
        "schema_version": 2,
        "positionings": [
            {
                "id": "leadership",
                "capabilities": ["team", "delivery", "strategy"],
                "domains": ["fintech"],
                "text": (
                    "У меня шесть лет опыта развития цифровых продуктов и управления "
                    "кросс-функциональными командами: от выбора направления до запуска "
                    "изменений и оценки бизнес-результата."
                ),
            },
            {
                "id": "credit",
                "capabilities": ["credit", "growth", "monetization", "fintech"],
                "domains": ["fintech", "credit products"],
                "text": (
                    "Последние шесть лет я развиваю финтех-продукты, включая кредитные "
                    "продукты мобильного банка с аудиторией 1,2 млн MAU, и отвечаю за "
                    "рост их использования и доходности."
                ),
            },
        ],
        "forbidden_terms": ["СЕКРЕТНЫЙ РАБОТОДАТЕЛЬ"],
        "preferences": {},
        "verified": {},
        "facts": [
            {
                "id": "zero_to_one",
                "capabilities": ["launch", "strategy", "discovery", "delivery"],
                "domains": ["digital products"],
                "public_text": (
                    "Запускал цифровые продукты с нуля и развивал их до масштаба "
                    "1 млн MAU. Отвечал за исследование потребностей, выбор направления, "
                    "проверку product-market fit, стратегию и delivery."
                ),
            },
            {
                "id": "team",
                "capabilities": ["team", "stakeholders", "delivery"],
                "domains": ["digital products"],
                "public_text": (
                    "Управлял кросс-функциональными командами разработки, QA, дизайна "
                    "и аналитики численностью до 12 человек, синхронизируя продуктовые "
                    "приоритеты, ограничения delivery и ожидания заинтересованных сторон."
                ),
            },
            {
                "id": "credit_growth",
                "capabilities": ["credit", "fintech", "growth", "monetization"],
                "domains": ["credit products"],
                "public_text": (
                    "Развивал кредитные продукты мобильного банка с 1,2 млн MAU. "
                    "Take rate вырос на 40%, выдачи через приложение увеличились "
                    "в 3 раза, а NPS — на 10 процентных пунктов."
                ),
            },
            {
                "id": "monetization",
                "capabilities": ["monetization", "strategy", "fintech"],
                "domains": ["fintech"],
                "public_text": (
                    "Пересобрал сегментацию и сделал клиентские сценарии и условия "
                    "предложений более прозрачными. В результате комиссионный доход "
                    "вырос в 4 раза без ухудшения пользовательского опыта."
                ),
            },
        ],
    }


def pbf_analysis() -> dict:
    return {
        "suitable": True,
        "role_family": "release_delivery",
        "role_summary": "управление релизами и delivery финтех-решения",
        "primary_goal": evidence_item(
            "goal",
            "task",
            "своевременный и качественный выпуск релизов",
            "Обеспечивать своевременный и качественный выпуск релизов.",
            ["release_management", "delivery"],
        ),
        "items": [
            evidence_item("release", "must_have", "управление релизами", "Опыт управления релизами от 2 лет.", ["release_management"]),
            evidence_item("agile", "must_have", "Agile/Scrum", "Знание Agile/Scrum.", ["agile"]),
            evidence_item("cicd", "must_have", "DevOps и CI/CD", "Понимание DevOps и CI/CD.", ["ci_cd"]),
            evidence_item("pos", "nice_to_have", "опыт с POS-терминалами", "Опыт с POS-терминалами будет преимуществом.", ["hardware"]),
            evidence_item("china", "context", "производство с партнёрами из Китая", "Собственное производство с партнёрами из Китая."),
            evidence_item("salary", "cover_letter_request", "указать уровень дохода", "Просьба в сопроводительном письме указывать уровень дохода.", request_kind="compensation"),
        ],
        "matches": [
            {"requirement_id": "goal", "status": "transferable", "fact_ids": ["team"]},
            {"requirement_id": "release", "status": "gap", "fact_ids": []},
            {"requirement_id": "agile", "status": "transferable", "fact_ids": ["team"]},
            {"requirement_id": "cicd", "status": "gap", "fact_ids": []},
        ],
        "relevance": "medium",
        "positioning_id": "leadership",
        "selected_fact_ids": ["team", "zero_to_one"],
    }


def portfolio_analysis() -> dict:
    return {
        "suitable": True,
        "role_family": "product_leadership",
        "role_summary": "управление жизненным циклом кредитного портфеля",
        "primary_goal": evidence_item(
            "goal",
            "task",
            "управление жизненным циклом кредитного портфеля",
            "Управлять жизненным циклом кредитного портфеля.",
            ["portfolio", "credit"],
        ),
        "items": [
            evidence_item("lifecycle", "task", "активация, использование, остатки и удержание", "Развивать активацию, использование, остатки и удержание клиентов.", ["activation", "retention", "portfolio"]),
            evidence_item("profit", "task", "качество и прибыльность портфеля", "Отвечать за качество и прибыльность портфеля.", ["portfolio", "monetization"]),
            evidence_item("teams", "must_have", "работа с CRM, рисками и аналитикой", "Работать с CRM, маркетингом, юридической функцией, рисками и аналитикой.", ["crm", "risk", "analytics"]),
            evidence_item("english", "language", "английский B2", "Английский язык не ниже B2."),
            evidence_item("latam", "geography", "рынок Латинской Америки", "Продукт развивается на рынке Латинской Америки."),
        ],
        "matches": [
            {"requirement_id": "goal", "status": "direct", "fact_ids": ["credit_growth"]},
            {"requirement_id": "lifecycle", "status": "transferable", "fact_ids": ["credit_growth"]},
            {"requirement_id": "profit", "status": "direct", "fact_ids": ["monetization"]},
            {"requirement_id": "teams", "status": "unknown", "fact_ids": []},
            {"requirement_id": "english", "status": "unknown", "fact_ids": []},
        ],
        "relevance": "medium",
        "positioning_id": "credit",
        "selected_fact_ids": ["credit_growth", "monetization"],
    }


class AiAnalyzerTest(unittest.TestCase):
    def test_source_author_identity_is_not_hardcoded(self) -> None:
        source = Path(ai_analyzer.__file__).read_text(encoding="utf-8")
        self.assertNotIn("fikstt2", source)
        self.assertNotIn("VisionForge", source)
        self.assertNotIn("Евгений", source)

    def test_analysis_propagates_ollama_outage(self) -> None:
        with (
            mock.patch("ai_analyzer._load_candidate_profile", return_value=candidate_profile()),
            mock.patch("ai_analyzer._ask_ollama", side_effect=ai_analyzer.OllamaUnavailableError("offline")),
            mock.patch("ai_analyzer.asyncio.sleep", new=mock.AsyncMock()),
        ):
            with self.assertRaises(ai_analyzer.OllamaUnavailableError):
                asyncio.run(ai_analyzer.analyze_vacancy("Product", PBF_DESCRIPTION))

    def test_analysis_uses_grounded_fallback_for_unstructured_answer(self) -> None:
        with (
            mock.patch("ai_analyzer._load_candidate_profile", return_value=candidate_profile()),
            mock.patch("ai_analyzer._ask_ollama", new=mock.AsyncMock(return_value="probably yes")),
        ):
            result = asyncio.run(ai_analyzer.analyze_vacancy("Product", PBF_DESCRIPTION))

        self.assertTrue(result["fallback_used"])
        self.assertIn(
            result["primary_goal"]["evidence"].casefold(),
            PBF_DESCRIPTION.casefold(),
        )
        self.assertEqual(result["relevance"], "low")

    def test_pbf_analysis_separates_requirements_context_and_salary(self) -> None:
        answer = json.dumps(pbf_analysis(), ensure_ascii=False)
        with (
            mock.patch("ai_analyzer._load_candidate_profile", return_value=candidate_profile()),
            mock.patch("ai_analyzer._ask_ollama", new=mock.AsyncMock(return_value=answer)),
        ):
            result = asyncio.run(ai_analyzer.analyze_vacancy("Product Manager", PBF_DESCRIPTION))

        items = {item["id"]: item for item in result["items"]}
        self.assertEqual(result["role_family"], "release_delivery")
        self.assertEqual(result["relevance"], "low")
        self.assertEqual(items["cicd"]["kind"], "must_have")
        self.assertEqual(items["pos"]["kind"], "nice_to_have")
        self.assertEqual(items["china"]["kind"], "context")
        self.assertEqual(items["salary"]["request_kind"], "compensation")
        all_text = json.dumps(result, ensure_ascii=False).casefold()
        self.assertNotIn("английск", all_text)
        self.assertNotIn("техническ", all_text)
        self.assertNotIn("управление поставщиками", all_text)

    def test_hardware_requirement_cannot_match_general_delivery_fact(self) -> None:
        profile = candidate_profile()
        analysis = pbf_analysis()
        analysis["matches"].append(
            {
                "requirement_id": "pos",
                "status": "direct",
                "fact_ids": ["zero_to_one"],
            }
        )
        validated = ai_analyzer._validate_analysis(
            copy.deepcopy(analysis),
            PBF_DESCRIPTION,
            profile,
        )
        match = next(
            item for item in validated["matches"] if item["requirement_id"] == "pos"
        )
        self.assertEqual(match["status"], "gap")
        self.assertEqual(match["fact_ids"], [])

    def test_analysis_discards_requirement_missing_from_vacancy(self) -> None:
        result = pbf_analysis()
        result["items"][0]["text"] = "Управление космическим кораблём"
        result["items"][0]["evidence"] = "Придуманное требование"
        answer = json.dumps(result, ensure_ascii=False)
        with (
            mock.patch("ai_analyzer._load_candidate_profile", return_value=candidate_profile()),
            mock.patch("ai_analyzer._ask_ollama", new=mock.AsyncMock(return_value=answer)),
        ):
            analysis = asyncio.run(
                ai_analyzer.analyze_vacancy("Product", PBF_DESCRIPTION)
            )

        self.assertTrue(analysis["fallback_used"])
        self.assertNotIn(
            "космическим",
            json.dumps(analysis, ensure_ascii=False).casefold(),
        )

    def test_portfolio_letter_uses_approved_metrics_and_safe_wording(self) -> None:
        profile = candidate_profile()
        with (
            mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"),
            mock.patch("ai_analyzer._load_candidate_profile", return_value=profile),
            mock.patch("ai_analyzer.analyze_vacancy", new=mock.AsyncMock(return_value=portfolio_analysis())),
        ):
            result = asyncio.run(ai_analyzer.analyze_and_generate("Product Leader", PORTFOLIO_DESCRIPTION))

        letter = result["cover_letter"]
        self.assertIn("take rate вырос на 40%", letter.casefold())
        self.assertIn("увеличились в 3 раза", letter)
        self.assertIn("NPS — на 10", letter)
        self.assertIn("комиссионный доход вырос в 4 раза", letter)
        self.assertNotIn("тёмные паттерны", letter.casefold())
        self.assertNotIn("юнит-эконом", letter.casefold())
        self.assertNotIn("на senior-уровне", letter.casefold())
        self.assertNotIn("СЕКРЕТНЫЙ РАБОТОДАТЕЛЬ", letter)
        word_count = len(re.findall(r"\b[\w-]+\b", letter))
        self.assertGreaterEqual(word_count, 130)
        self.assertLessEqual(word_count, 190)
        self.assertEqual(len(letter.split("\n\n")), 6)

    def test_portfolio_analysis_prioritizes_credit_and_monetization_facts(self) -> None:
        profile = candidate_profile()
        analysis = ai_analyzer._validate_analysis(
            copy.deepcopy(portfolio_analysis()),
            PORTFOLIO_DESCRIPTION,
            profile,
        )
        self.assertEqual(
            analysis["selected_fact_ids"],
            ["credit_growth", "monetization"],
        )

    def test_missing_salary_and_english_become_warnings(self) -> None:
        profile = candidate_profile()
        pbf_warnings = ai_analyzer.build_warnings(pbf_analysis(), profile)
        portfolio_warnings = ai_analyzer.build_warnings(portfolio_analysis(), profile)
        self.assertTrue(any("доход" in warning for warning in pbf_warnings))
        self.assertTrue(any("английский B2" in warning for warning in portfolio_warnings))

    def test_confirmed_salary_is_included_without_warning(self) -> None:
        profile = candidate_profile()
        profile["preferences"]["compensation"] = "от 350 000 рублей на руки"
        analysis = pbf_analysis()
        letter = ai_analyzer._compose_cover_letter(analysis, profile)
        warnings = ai_analyzer.build_warnings(analysis, profile)
        self.assertIn("от 350 000 рублей на руки", letter)
        self.assertFalse(any("доход" in warning for warning in warnings))

    def test_letter_validator_rejects_forbidden_contacts_and_new_numbers(self) -> None:
        profile = candidate_profile()
        text = (
            "Здравствуйте!\n\nСЕКРЕТНЫЙ РАБОТОДАТЕЛЬ\n\n+79991234567 "
            "test@example.com\n\nРезультат 999%.\n\nТекст\n\nРоман Иванов"
        )
        with mock.patch.object(ai_analyzer, "APPLICANT_NAME", "Роман Иванов"):
            issues = ai_analyzer._cover_letter_issues(text, profile, "10%")
        self.assertIn("нельзя добавлять email", issues)
        self.assertIn("нельзя добавлять телефон", issues)
        self.assertTrue(any("999%" in issue for issue in issues))
        self.assertTrue(any("СЕКРЕТНЫЙ РАБОТОДАТЕЛЬ" in issue for issue in issues))

    def test_profile_value_is_saved_privately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_profile.json"
            path.write_text(json.dumps(candidate_profile(), ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(ai_analyzer, "CANDIDATE_PROFILE_PATH", str(path)):
                ai_analyzer.save_profile_value("english", "B2, рабочие встречи")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["verified"]["english"], "B2, рабочие встречи")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
