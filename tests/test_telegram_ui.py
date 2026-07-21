import unittest

import tg_bot


class TelegramUiTest(unittest.TestCase):
    def test_decision_keyboard_contains_required_actions(self) -> None:
        keyboard = tg_bot.decision_keyboard("123")
        buttons = [button for row in keyboard.inline_keyboard for button in row]

        self.assertEqual(
            [button.text for button in buttons],
            ["Откликнуться", "Изменить письмо", "Пропустить навсегда"],
        )
        self.assertEqual(
            [button.callback_data for button in buttons],
            ["job:apply:123", "job:edit:123", "job:skip:123"],
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


if __name__ == "__main__":
    unittest.main()
