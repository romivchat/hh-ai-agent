import ast
import asyncio
import unittest
from pathlib import Path
from unittest import mock

from hh_client import HHClient


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

    def test_ollama_outage_stops_search_without_filtering_current_job(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "search_and_queue")

        self.assertIn("except OllamaUnavailableError", source)
        outage_handler = source.split("except OllamaUnavailableError", 1)[1].split(
            "except Exception", 1
        )[0]
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

    def test_application_tries_configured_resumes_in_order(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "apply_pending_job")

        self.assertIn("for target_resume_name in TARGET_RESUME_NAMES", source)
        self.assertIn("selected_resume", source)

    def test_telegram_apply_button_calls_only_configured_handler(self) -> None:
        source = async_function_source(ROOT / "tg_bot.py", "apply_job")

        self.assertIn("application_handler(job_id)", source)
        self.assertNotIn("submit_btn", source)


if __name__ == "__main__":
    unittest.main()
