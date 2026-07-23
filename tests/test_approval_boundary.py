import ast
import asyncio
import unittest
from pathlib import Path
from unittest import mock

from hh_client import HHClient, is_target_product_title


ROOT = Path(__file__).resolve().parents[1]


def async_function_source(path: Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {function_name} not found in {path.name}")


class ApprovalBoundaryTest(unittest.TestCase):
    def test_submission_kill_switch_stops_before_browser_access(self) -> None:
        client = HHClient()
        client.context = object()

        with mock.patch("hh_client.HH_SUBMISSION_ENABLED", False):
            success, message = asyncio.run(client.apply_pending_job("123"))

        self.assertFalse(success)
        self.assertIn("HH_SUBMISSION_ENABLED", message)

    def test_search_never_clicks_application_buttons(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "search_and_queue")

        self.assertNotIn("apply_btn.click", source)
        self.assertNotIn("submit_btn.click", source)
        self.assertIn("add_pending_job", source)

    def test_manual_and_automatic_search_share_one_lock(self) -> None:
        source = async_function_source(ROOT / "main.py", "agent_loop")

        self.assertIn("search_lock = asyncio.Lock()", source)
        self.assertIn("search_lock.locked()", source)
        self.assertIn("async with search_lock", source)
        self.assertIn("set_search_handler(run_search)", source)

    def test_search_uses_moscow_and_remote_modes(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "search_and_queue")

        self.assertIn('"&area=1"', source)
        self.assertIn('"&area=113&schedule=remote"', source)
        self.assertNotIn('"&area=2"', source)

    def test_search_targets_senior_experience(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "search_and_queue")

        self.assertIn("experience=between3And6", source)
        self.assertIn("experience=moreThan6", source)
        self.assertNotIn("experience=noExperience", source)
        self.assertNotIn("experience=between1And3", source)

    def test_search_rejects_sales_leadership_titles(self) -> None:
        for title in (
            "Руководитель продаж",
            "Head of Sales",
            "X-sell Head",
            "Cross-sell Head",
        ):
            self.assertFalse(is_target_product_title(title), title)

    def test_search_requires_explicit_product_role_in_title(self) -> None:
        for title in (
            "Senior Product Manager",
            "FTUE Product Owner",
            "Head of Product",
            "CPO",
            "Продакт-менеджер",
            "Менеджер продукта",
            "Руководитель направления / Владелец продукта",
            "Руководитель группы продактов Озон Джоб",
            "Директор Продуктовой Фабрики",
        ):
            self.assertTrue(is_target_product_title(title), title)

        for title in (
            "Бренд-менеджер по чаю",
            "KYC Officer / Document Verification Specialist",
            "DevOps-инженер Middle+/Senior",
            "Старший аналитик в кластер Growth",
            "Федеральный Медицинский Советник",
            "Менеджер по продажам на маркетплейсах",
        ):
            self.assertFalse(is_target_product_title(title), title)

    def test_ollama_outage_stops_search_without_filtering_current_job(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "search_and_queue")

        exception = "except (OllamaUnavailableError, CandidateProfileError)"
        self.assertIn(exception, source)
        outage_handler = source.split(exception, 1)[1].split("except Exception", 1)[0]
        self.assertIn("return", outage_handler)
        self.assertNotIn("add_filtered_job", outage_handler)

    def test_real_submission_is_isolated_behind_database_claim(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "apply_pending_job")

        self.assertIn("HH_SUBMISSION_ENABLED", source)
        self.assertIn("claim_pending_job", source)
        self.assertIn("apply_btn.click", source)
        self.assertIn("submit_btn.click", source)
        self.assertIn("mark_job_applied", source)
        self.assertIn("restore_pending_job", source)
        self.assertIn("_verify_application_sent", source)
        self.assertIn("_fill_screening_answers", source)
        self.assertIn("vacancy-response-popup-form-letter-input", source)
        self.assertIn("vacancy-response-letter-submit", source)
        self.assertIn("Сопроводительное письмо не подтверждено HH", source)
        self.assertLess(
            source.index("_verify_application_sent"),
            source.index("mark_job_applied"),
        )
        self.assertLess(
            source.index("_fill_screening_answers"),
            source.index("submit_btn.click"),
        )

    def test_application_tries_configured_resumes_in_order(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "apply_pending_job")

        self.assertIn("for target_resume_name in TARGET_RESUME_NAMES", source)
        self.assertIn("selected_resume", source)

    def test_application_verification_requires_visible_hh_chat(self) -> None:
        client = HHClient()
        verification_page = mock.AsyncMock()
        chat_link = mock.AsyncMock()
        chat_link.is_visible.return_value = True
        locator = mock.Mock(first=chat_link)
        verification_page.locator = mock.Mock(return_value=locator)
        context = mock.Mock()
        context.new_page = mock.AsyncMock(return_value=verification_page)
        client.context = context

        with (
            mock.patch(
                "hh_client.Stealth.apply_stealth_async", new=mock.AsyncMock()
            ),
            mock.patch("hh_client.asyncio.sleep", new=mock.AsyncMock()),
        ):
            confirmed = asyncio.run(
                client._verify_application_sent("https://hh.ru/vacancy/123")
            )

        self.assertTrue(confirmed)
        verification_page.goto.assert_awaited_once()
        chat_link.is_visible.assert_awaited_once()
        verification_page.close.assert_awaited_once()

    def test_application_verification_waits_for_delayed_hh_status(self) -> None:
        client = HHClient()
        verification_page = mock.AsyncMock()
        chat_link = mock.AsyncMock()
        chat_link.is_visible.side_effect = [False, False, True]
        locator = mock.Mock(first=chat_link)
        verification_page.locator = mock.Mock(return_value=locator)
        context = mock.Mock()
        context.new_page = mock.AsyncMock(return_value=verification_page)
        client.context = context

        with (
            mock.patch(
                "hh_client.Stealth.apply_stealth_async", new=mock.AsyncMock()
            ),
            mock.patch("hh_client.asyncio.sleep", new=mock.AsyncMock()) as sleep,
        ):
            confirmed = asyncio.run(
                client._verify_application_sent("https://hh.ru/vacancy/123")
            )

        self.assertTrue(confirmed)
        self.assertEqual(sleep.await_count, 2)
        self.assertEqual(verification_page.reload.await_count, 2)
        verification_page.close.assert_awaited_once()

    def test_cover_letter_verification_waits_until_form_disappears(self) -> None:
        client = HHClient()
        textarea = mock.AsyncMock()
        textarea.is_visible.side_effect = [True, True, False]
        submit_button = mock.AsyncMock()
        submit_button.is_visible.return_value = True

        with mock.patch("hh_client.asyncio.sleep", new=mock.AsyncMock()) as sleep:
            confirmed = asyncio.run(
                client._verify_cover_letter_sent(textarea, submit_button)
            )

        self.assertTrue(confirmed)
        self.assertEqual(sleep.await_count, 2)

    def test_telegram_apply_button_calls_only_configured_handler(self) -> None:
        source = async_function_source(ROOT / "tg_bot.py", "apply_job")

        self.assertIn("application_handler(job_id)", source)
        self.assertNotIn("submit_btn", source)


if __name__ == "__main__":
    unittest.main()
