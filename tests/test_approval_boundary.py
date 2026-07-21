import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def async_function_source(path: Path, function_name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {function_name} not found in {path.name}")


class ApprovalBoundaryTest(unittest.TestCase):
    def test_search_never_clicks_application_buttons(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "search_and_queue")

        self.assertNotIn("apply_btn.click", source)
        self.assertNotIn("submit_btn.click", source)
        self.assertIn("add_pending_job", source)

    def test_real_submission_is_isolated_behind_database_claim(self) -> None:
        source = async_function_source(ROOT / "hh_client.py", "apply_pending_job")

        self.assertIn("claim_pending_job", source)
        self.assertIn("apply_btn.click", source)
        self.assertIn("submit_btn.click", source)
        self.assertIn("mark_job_applied", source)
        self.assertIn("restore_pending_job", source)

    def test_telegram_apply_button_calls_only_configured_handler(self) -> None:
        source = async_function_source(ROOT / "tg_bot.py", "apply_job")

        self.assertIn("application_handler(job_id)", source)
        self.assertNotIn("submit_btn", source)


if __name__ == "__main__":
    unittest.main()
