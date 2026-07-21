import unittest
from unittest import mock

import config


class ConfigurationTest(unittest.TestCase):
    def test_search_queries_target_product_roles(self) -> None:
        self.assertIn("Product Manager", config.SEARCH_QUERIES)
        self.assertIn("Product Owner", config.SEARCH_QUERIES)
        self.assertIn("CPO", config.SEARCH_QUERIES)
        self.assertNotIn("Python backend", config.SEARCH_QUERIES)

    def test_missing_profile_stops_startup(self) -> None:
        with mock.patch.multiple(
            config,
            TG_BOT_TOKEN="token",
            TG_USER_ID="123",
            APPLICANT_NAME="ИМЯ_НЕ_НАСТРОЕНО",
            TARGET_RESUME_NAMES=["РЕЗЮМЕ_НЕ_НАСТРОЕНО"],
            MY_RESUME_SUMMARY="ПРОФИЛЬ_НЕ_НАСТРОЕН",
        ):
            with self.assertRaisesRegex(RuntimeError, "APPLICANT_NAME"):
                config.validate_configuration()

    def test_example_placeholders_stop_startup(self) -> None:
        with mock.patch.multiple(
            config,
            TG_BOT_TOKEN="ВАШ_ТОКЕН_ОТ_BOTFATHER",
            TG_USER_ID="ВАШ_TELEGRAM_ID",
            APPLICANT_NAME="ВАШЕ_ИМЯ",
            TARGET_RESUME_NAMES=["ТОЧНОЕ_НАЗВАНИЕ_РЕЗЮМЕ_НА_HH"],
            MY_RESUME_SUMMARY=(
                "ПОДРОБНОЕ_ОПИСАНИЕ_ОПЫТА_НАВЫКОВ_И_ПОЖЕЛАНИЙ"
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "TG_BOT_TOKEN"):
                config.validate_configuration()

    def test_complete_configuration_is_accepted(self) -> None:
        with mock.patch.multiple(
            config,
            TG_BOT_TOKEN="token",
            TG_USER_ID="123",
            APPLICANT_NAME="Роман",
            TARGET_RESUME_NAMES=["Product Manager", "Product Owner", "CPO"],
            MY_RESUME_SUMMARY="Опыт и навыки кандидата",
        ):
            config.validate_configuration()


if __name__ == "__main__":
    unittest.main()
