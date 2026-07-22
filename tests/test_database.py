import tempfile
import unittest
from pathlib import Path

import database


class VacancyDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = str(Path(self.temp_dir.name) / "agent.db")
        database.init_db()

    def tearDown(self) -> None:
        database.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def add_pending(self, number: int, max_pending: int = 10) -> bool:
        return database.add_pending_job(
            str(number),
            f"Vacancy {number}",
            f"https://hh.ru/vacancy/{number}",
            f"Letter {number}",
            max_pending,
        )

    def test_queue_never_exceeds_ten_pending_jobs(self) -> None:
        for number in range(10):
            self.assertTrue(self.add_pending(number))

        self.assertFalse(self.add_pending(10))
        self.assertEqual(database.count_pending_jobs(), 10)
        self.assertFalse(database.is_job_processed("10"))

    def test_pending_job_and_edited_letter_survive_restart(self) -> None:
        self.assertTrue(self.add_pending(1))
        self.assertTrue(database.update_cover_letter("1", "Updated letter"))

        database.init_db()

        job = database.get_job("1")
        self.assertEqual(job["status"], database.PENDING)
        self.assertEqual(job["cover_letter"], "Updated letter")

    def test_skipped_job_is_never_offered_again(self) -> None:
        self.assertTrue(self.add_pending(1))
        self.assertTrue(database.skip_pending_job("1"))

        database.init_db()

        job = database.get_job("1")
        self.assertEqual(job["status"], database.SKIPPED)
        self.assertTrue(database.is_job_processed("1"))
        self.assertFalse(self.add_pending(1))

    def test_job_can_be_claimed_only_once(self) -> None:
        self.assertTrue(self.add_pending(1))

        first_claim = database.claim_pending_job("1")
        second_claim = database.claim_pending_job("1")

        self.assertIsNotNone(first_claim)
        self.assertIsNone(second_claim)
        self.assertTrue(database.mark_job_applied("1"))
        self.assertEqual(database.get_job("1")["status"], database.APPLIED)

    def test_failed_application_returns_job_to_pending(self) -> None:
        self.assertTrue(self.add_pending(1))
        self.assertIsNotNone(database.claim_pending_job("1"))

        self.assertTrue(database.restore_pending_job("1"))
        self.assertEqual(database.get_job("1")["status"], database.PENDING)

    def test_structured_analysis_survives_restart(self) -> None:
        self.assertTrue(
            database.add_pending_job(
                "1",
                "Product",
                "https://hh.ru/vacancy/1",
                "Generated letter",
                10,
                description="Full description",
                analysis={"relevance": "medium"},
                warnings=["Gap"],
                strengths=["Direct"],
                letter_version=2,
            )
        )
        database.init_db()
        job = database.get_job("1")
        self.assertEqual(job["description"], "Full description")
        self.assertIn('"relevance": "medium"', job["analysis_json"])
        self.assertEqual(job["letter_version"], 2)
        self.assertEqual(job["letter_edited"], 0)

    def test_regeneration_never_overwrites_manual_letter(self) -> None:
        self.assertTrue(self.add_pending(1))
        self.assertTrue(database.update_cover_letter("1", "Manual letter"))
        updated = database.update_generated_job(
            "1",
            "Description",
            "Generated letter",
            {"relevance": "high"},
            [],
            [],
            2,
        )
        self.assertFalse(updated)
        self.assertEqual(database.get_job("1")["cover_letter"], "Manual letter")


if __name__ == "__main__":
    unittest.main()
