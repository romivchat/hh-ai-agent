import unittest

import tg_bot


class TelegramUiTest(unittest.TestCase):
    def test_main_keyboard_contains_search_and_pending_actions(self) -> None:
        keyboard = tg_bot.main_keyboard()
        buttons = [button for row in keyboard.keyboard for button in row]

        self.assertEqual(
            [button.text for button in buttons],
            ["Поиск вакансий", "Ожидают решения"],
        )
        self.assertTrue(keyboard.resize_keyboard)
        self.assertTrue(keyboard.is_persistent)

    def test_decision_keyboard_contains_required_actions(self) -> None:
        keyboard = tg_bot.decision_keyboard("123")
        buttons = [button for row in keyboard.inline_keyboard for button in row]

        self.assertEqual(
            [button.text for button in buttons],
            [
                "Откликнуться",
                "Изменить письмо",
                "Дополнить данные",
                "Пропустить навсегда",
            ],
        )
        self.assertEqual(
            [button.callback_data for button in buttons],
            [
                "job:apply:123",
                "job:edit:123",
                "job:enrich:123",
                "job:skip:123",
            ],
        )

    def test_long_message_is_split_without_losing_text(self) -> None:
        text = "A" * 4500

        parts = tg_bot._split_message(text)

        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part) <= 4000 for part in parts))

    def test_pending_message_contains_vacancy_and_letter(self) -> None:
        message = tg_bot.format_pending_job(
            {
                "title": "Python developer",
                "url": "https://hh.ru/vacancy/123",
                "cover_letter": "My letter",
            }
        )

        self.assertIn("Python developer", message)
        self.assertIn("https://hh.ru/vacancy/123", message)
        self.assertIn("My letter", message)
        self.assertIn("<blockquote expandable>My letter</blockquote>", message)

    def test_pending_message_escapes_html_from_vacancy_and_letter(self) -> None:
        message = tg_bot.format_pending_job(
            {
                "title": "Product <Owner> & CPO",
                "url": "https://hh.ru/vacancy/123?a=1&b=2",
                "cover_letter": "Опыт <6 лет> & рост",
            }
        )

        self.assertIn("Product &lt;Owner&gt; &amp; CPO", message)
        self.assertIn("a=1&amp;b=2", message)
        self.assertIn("Опыт &lt;6 лет&gt; &amp; рост", message)

    def test_long_cover_letter_is_split_into_valid_expandable_blocks(self) -> None:
        messages = tg_bot._cover_letter_messages("&" * 5000)

        self.assertGreater(len(messages), 1)
        self.assertTrue(all(len(message) <= 4000 for message in messages))
        self.assertTrue(
            all("<blockquote expandable>" in message for message in messages)
        )

    def test_pending_message_contains_analysis_and_warnings(self) -> None:
        message = tg_bot.format_pending_job(
            {
                "title": "Product Leader",
                "url": "https://hh.ru/vacancy/123",
                "cover_letter": "Letter",
                "analysis_json": (
                    '{"relevance":"medium","role_summary":"кредитный портфель",'
                    '"primary_goal":{"text":"рост доходности портфеля"}}'
                ),
                "strengths_json": '["Прямой опыт: кредитные продукты"]',
                "warnings_json": '["Нужно уточнить опыт: CRM"]',
            }
        )

        self.assertIn("Релевантность: Средняя", message)
        self.assertIn("Фактическая роль: кредитный портфель", message)
        self.assertIn("Главная задача: рост доходности портфеля", message)
        self.assertIn("Прямой опыт: кредитные продукты", message)
        self.assertIn("Нужно уточнить опыт: CRM", message)


if __name__ == "__main__":
    unittest.main()
