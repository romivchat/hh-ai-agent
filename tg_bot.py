import asyncio
from collections.abc import Awaitable, Callable
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import database
from config import TG_BOT_TOKEN, TG_USER_ID


ApplicationHandler = Callable[[str], Awaitable[tuple[bool, str]]]

dp = Dispatcher()
bot: Optional[Bot] = None
application_handler: Optional[ApplicationHandler] = None

captcha_event = asyncio.Event()
captcha_solution = ""
captcha_waiting = False
editing_job_id: Optional[str] = None


def set_application_handler(handler: Optional[ApplicationHandler]) -> None:
    global application_handler
    application_handler = handler


def _is_owner(user_id: Optional[int]) -> bool:
    return user_id is not None and str(user_id) == str(TG_USER_ID)


def _telegram_is_configured() -> bool:
    invalid_tokens = {"your_bot_token_here", "YOUR_BOT_TOKEN_HERE"}
    invalid_users = {"your_telegram_id_here", "YOUR_USER_ID_HERE"}
    return TG_BOT_TOKEN not in invalid_tokens and TG_USER_ID not in invalid_users


def decision_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Откликнуться", callback_data=f"job:apply:{job_id}"
                ),
                InlineKeyboardButton(
                    text="Изменить письмо", callback_data=f"job:edit:{job_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить навсегда", callback_data=f"job:skip:{job_id}"
                )
            ],
        ]
    )


def format_pending_job(job: dict) -> str:
    return (
        "Найдена подходящая вакансия\n\n"
        f"{job['title']}\n"
        f"{job['url']}\n\n"
        "Сопроводительное письмо:\n"
        f"{job['cover_letter']}"
    )


def _split_message(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]

    parts = []
    remaining = text
    while remaining:
        split_at = min(limit, len(remaining))
        if split_at < len(remaining):
            newline_at = remaining.rfind("\n", 0, split_at)
            if newline_at > limit // 2:
                split_at = newline_at + 1
        parts.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return parts


async def _get_bot() -> Optional[Bot]:
    if not _telegram_is_configured():
        return None
    return bot


async def send_notification(text: str) -> None:
    current_bot = await _get_bot()
    if current_bot is None:
        print("ОШИБКА: Telegram не настроен или бот ещё не запущен. Уведомление:")
        print(text)
        return

    try:
        await current_bot.send_message(
            chat_id=TG_USER_ID,
            text=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        print(f"Ошибка при отправке сообщения в TG: {exc}")


async def send_pending_vacancy(job: dict) -> None:
    current_bot = await _get_bot()
    if current_bot is None:
        raise RuntimeError("Telegram не настроен или бот ещё не запущен")

    parts = _split_message(format_pending_job(job))
    for index, part in enumerate(parts):
        reply_markup = decision_keyboard(job["id"]) if index == len(parts) - 1 else None
        await current_bot.send_message(
            chat_id=TG_USER_ID,
            text=part,
            reply_markup=reply_markup,
        )


async def send_captcha_request(filepath: str, text: str) -> None:
    global captcha_waiting

    current_bot = await _get_bot()
    if current_bot is None:
        print("ОШИБКА: Telegram не настроен. Капча сохранена в", filepath)
        return

    try:
        captcha_event.clear()
        captcha_waiting = True
        photo = FSInputFile(filepath)
        await current_bot.send_photo(
            chat_id=TG_USER_ID,
            photo=photo,
            caption=text,
            parse_mode="HTML",
        )
    except Exception as exc:
        captcha_waiting = False
        print(f"Ошибка при отправке капчи в TG: {exc}")


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if _is_owner(message.from_user.id if message.from_user else None):
        pending_count = database.count_pending_jobs()
        await message.answer(
            "Бот запущен. Я пришлю подходящие вакансии и не буду откликаться "
            f"без вашего решения. Сейчас ожидают решения: {pending_count}."
        )
    else:
        user_id = message.from_user.id if message.from_user else "неизвестен"
        await message.answer(
            "Извините, у вас нет доступа к этому боту.\n"
            f"Ваш ID: {user_id}"
        )


@dp.message(Command("pending"))
async def cmd_pending(message: Message) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return

    jobs = database.list_pending_jobs()
    if not jobs:
        await message.answer("Сейчас нет вакансий, ожидающих решения.")
        return

    await message.answer(f"Ожидают решения: {len(jobs)}")
    for job in jobs:
        await send_pending_vacancy(job)


@dp.callback_query(F.data.startswith("job:apply:"))
async def apply_job(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if application_handler is None:
        await callback.answer("Браузер HH ещё не готов", show_alert=True)
        return

    job_id = callback.data.rsplit(":", 1)[-1]
    await callback.answer("Пробую отправить отклик")
    success, result_message = await application_handler(job_id)

    if callback.message:
        await callback.message.answer(result_message)
        if success:
            await callback.message.edit_reply_markup(reply_markup=None)


@dp.callback_query(F.data.startswith("job:edit:"))
async def edit_job(callback: CallbackQuery) -> None:
    global editing_job_id

    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    if captcha_waiting:
        await callback.answer("Сначала отправьте код капчи", show_alert=True)
        return

    job_id = callback.data.rsplit(":", 1)[-1]
    job = database.get_job(job_id)
    if not job or job["status"] != database.PENDING:
        await callback.answer("Вакансия уже обработана", show_alert=True)
        return

    editing_job_id = job_id
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Пришлите новый текст сопроводительного письма одним сообщением. "
            "Отклик без отдельного нажатия кнопки отправлен не будет."
        )


@dp.callback_query(F.data.startswith("job:skip:"))
async def skip_job(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    job_id = callback.data.rsplit(":", 1)[-1]
    skipped = database.skip_pending_job(job_id)
    if not skipped:
        await callback.answer("Вакансия уже обработана", show_alert=True)
        return

    await callback.answer("Вакансия больше не будет предлагаться")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Вакансия пропущена навсегда.")


@dp.message()
async def handle_text(message: Message) -> None:
    global captcha_solution, captcha_waiting, editing_job_id

    if not _is_owner(message.from_user.id if message.from_user else None):
        return
    if not message.text:
        return

    text = message.text.strip()
    if editing_job_id is not None:
        job_id = editing_job_id
        if not text:
            await message.answer("Письмо не может быть пустым. Пришлите текст ещё раз.")
            return

        updated = database.update_cover_letter(job_id, text)
        editing_job_id = None
        if not updated:
            await message.answer("Вакансия уже обработана, письмо не изменено.")
            return

        await message.answer("Письмо обновлено. Для отправки нажмите «Откликнуться».")
        job = database.get_job(job_id)
        if job:
            await send_pending_vacancy(job)
        return

    if captcha_waiting:
        captcha_solution = text
        captcha_waiting = False
        captcha_event.set()
        await message.answer("Код принят, пробую ввести.")
        return

    await message.answer("Используйте кнопки под вакансией или команду /pending.")


async def start_bot() -> None:
    global bot

    if not _telegram_is_configured():
        raise RuntimeError("Заполните TG_BOT_TOKEN и TG_USER_ID в .env")

    bot = Bot(token=TG_BOT_TOKEN)
    print("Запуск Telegram-бота...")
    try:
        await dp.start_polling(bot)
    finally:
        bot = None


if __name__ == "__main__":
    asyncio.run(start_bot())
