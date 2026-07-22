import asyncio
import html
import json
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
from ai_analyzer import (
    CandidateProfileError,
    OllamaUnavailableError,
    draft_profile_fact,
    save_profile_fact,
    save_profile_value,
)
from config import TG_BOT_TOKEN, TG_USER_ID


ApplicationHandler = Callable[[str], Awaitable[tuple[bool, str]]]
RegenerationHandler = Callable[[str], Awaitable[tuple[bool, str]]]

dp = Dispatcher()
bot: Optional[Bot] = None
application_handler: Optional[ApplicationHandler] = None
regeneration_handler: Optional[RegenerationHandler] = None

captcha_event = asyncio.Event()
captcha_solution = ""
captcha_waiting = False
editing_job_id: Optional[str] = None
data_entry_state: Optional[dict] = None
pending_profile_change: Optional[dict] = None


def set_application_handler(handler: Optional[ApplicationHandler]) -> None:
    global application_handler
    application_handler = handler


def set_regeneration_handler(handler: Optional[RegenerationHandler]) -> None:
    global regeneration_handler
    regeneration_handler = handler


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
                    text="Дополнить данные", callback_data=f"job:enrich:{job_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Пропустить навсегда", callback_data=f"job:skip:{job_id}"
                )
            ],
        ]
    )


def profile_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Сохранить", callback_data="profile:confirm"),
                InlineKeyboardButton(text="Отмена", callback_data="profile:cancel"),
            ]
        ]
    )


def _json_value(raw_value, default):
    if isinstance(raw_value, type(default)):
        return raw_value
    try:
        value = json.loads(raw_value or "")
    except (json.JSONDecodeError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


def _escape_text(value, limit: Optional[int] = None) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return html.escape(text)


def _format_lines(title: str, values: list[str], empty: str) -> str:
    if not values:
        return f"{html.escape(title)}: {html.escape(empty)}"
    lines = "\n".join(f"• {_escape_text(value, 240)}" for value in values[:3])
    return f"{html.escape(title)}:\n{lines}"


def format_pending_summary(job: dict) -> str:
    analysis = _json_value(job.get("analysis_json"), {})
    warnings = _json_value(job.get("warnings_json"), [])
    strengths = _json_value(job.get("strengths_json"), [])
    relevance_labels = {"high": "Высокая", "medium": "Средняя", "low": "Низкая"}
    relevance = relevance_labels.get(analysis.get("relevance"), "Не оценена")
    goal = analysis.get("primary_goal", {}).get("text", "не определена")
    role_summary = analysis.get("role_summary")
    role_line = (
        f"\nФактическая роль: {_escape_text(role_summary, 320)}"
        if role_summary
        else ""
    )
    return (
        "<b>Найдена вакансия</b>\n\n"
        f"<b>{_escape_text(job['title'], 320)}</b>\n"
        f"{_escape_text(job['url'], 600)}\n\n"
        f"Релевантность: {relevance}{role_line}\n"
        f"Главная задача: {_escape_text(goal, 400)}\n\n"
        f"{_format_lines('Сильные совпадения', strengths, 'не найдены')}\n\n"
        f"{_format_lines('Предупреждения', warnings, 'нет')}"
    )


def format_cover_letter(cover_letter: str, continuation: bool = False) -> str:
    heading = "Сопроводительное письмо"
    if continuation:
        heading += " (продолжение)"
    letter = _escape_text(cover_letter) or "Письмо не сформировано."
    return f"<b>{heading}</b>\n<blockquote expandable>{letter}</blockquote>"


def format_pending_job(job: dict) -> str:
    return (
        f"{format_pending_summary(job)}\n\n"
        f"{format_cover_letter(job.get('cover_letter', ''))}"
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


def _split_for_html(text: str, escaped_limit: int) -> list[str]:
    """Split raw text while counting its escaped HTML representation."""
    if not text:
        return [""]

    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for char in text:
        escaped_length = len(html.escape(char))
        if current and current_length + escaped_length > escaped_limit:
            parts.append("".join(current))
            current = []
            current_length = 0
        current.append(char)
        current_length += escaped_length
    if current:
        parts.append("".join(current))
    return parts


def _cover_letter_messages(cover_letter: str, limit: int = 4000) -> list[str]:
    overhead = len(format_cover_letter("", continuation=True))
    chunks = _split_for_html(cover_letter, limit - overhead)
    return [
        format_cover_letter(chunk, continuation=index > 0)
        for index, chunk in enumerate(chunks)
    ]


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

    message = format_pending_job(job)
    if len(message) <= 4000:
        await current_bot.send_message(
            chat_id=TG_USER_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=decision_keyboard(job["id"]),
        )
        return

    await current_bot.send_message(
        chat_id=TG_USER_ID,
        text=format_pending_summary(job),
        parse_mode="HTML",
    )
    letter_messages = _cover_letter_messages(job.get("cover_letter", ""))
    for index, letter_message in enumerate(letter_messages):
        reply_markup = (
            decision_keyboard(job["id"])
            if index == len(letter_messages) - 1
            else None
        )
        await current_bot.send_message(
            chat_id=TG_USER_ID,
            text=letter_message,
            parse_mode="HTML",
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


@dp.callback_query(F.data.startswith("job:enrich:"))
async def enrich_job(callback: CallbackQuery) -> None:
    global data_entry_state, pending_profile_change

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

    analysis = _json_value(job.get("analysis_json"), {})
    warnings = _json_value(job.get("warnings_json"), [])
    items = {
        item.get("id"): item
        for item in [analysis.get("primary_goal", {}), *analysis.get("items", [])]
        if isinstance(item, dict) and item.get("id")
    }

    kind = "fact"
    requirement = analysis.get("primary_goal", {}).get("text", job["title"])
    prompt = (
        "Опишите одним сообщением реальный опыт, который подтверждает это "
        f"требование:\n{requirement}"
    )
    if any("доход" in warning.casefold() for warning in warnings):
        kind = "compensation"
        requirement = "зарплатные ожидания"
        prompt = (
            "Пришлите зарплатные ожидания в готовом виде. Например: "
            "от 350 000 рублей на руки, окончательная сумма зависит от объёма ответственности."
        )
    elif any("язык" in warning.casefold() for warning in warnings):
        kind = "english"
        requirement = "уровень и применение английского"
        prompt = (
            "Пришлите подтверждённый уровень английского и как вы его используете. "
            "Например: B2, использую для встреч и рабочей переписки."
        )
    else:
        for match in analysis.get("matches", []):
            if match.get("status") in {"gap", "unknown"}:
                item = items.get(match.get("requirement_id"))
                if item:
                    requirement = item["text"]
                    prompt = (
                        "Опишите одним сообщением только реальный опыт по требованию:\n"
                        f"{requirement}"
                    )
                    break

    data_entry_state = {
        "job_id": job_id,
        "kind": kind,
        "requirement": requirement,
    }
    pending_profile_change = None
    await callback.answer()
    if callback.message:
        await callback.message.answer(prompt)


@dp.callback_query(F.data == "profile:confirm")
async def confirm_profile_change(callback: CallbackQuery) -> None:
    global pending_profile_change

    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    change = pending_profile_change
    if not change:
        await callback.answer("Нет данных для сохранения", show_alert=True)
        return

    try:
        if change["kind"] == "fact":
            save_profile_fact(change["draft"])
        else:
            save_profile_value(change["kind"], change["value"])
    except CandidateProfileError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    pending_profile_change = None
    await callback.answer("Сохранено")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Данные сохранены. Пересобираю письмо.")

    if regeneration_handler is None:
        if callback.message:
            await callback.message.answer("Браузер HH ещё не готов. Используйте /pending позже.")
        return
    success, message = await regeneration_handler(change["job_id"])
    if callback.message:
        await callback.message.answer(message)
        if success:
            job = database.get_job(change["job_id"])
            if job:
                await send_pending_vacancy(job)


@dp.callback_query(F.data == "profile:cancel")
async def cancel_profile_change(callback: CallbackQuery) -> None:
    global data_entry_state, pending_profile_change

    if not _is_owner(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data_entry_state = None
    pending_profile_change = None
    await callback.answer("Отменено")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Изменение данных отменено.")


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
    global data_entry_state, pending_profile_change

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

    if data_entry_state is not None:
        state = data_entry_state
        data_entry_state = None
        try:
            if state["kind"] == "fact":
                draft = await draft_profile_fact(text, state["requirement"])
                pending_profile_change = {
                    **state,
                    "draft": draft,
                }
                preview = (
                    "Проверьте формулировку перед сохранением:\n\n"
                    f"{draft['public_text']}\n\n"
                    "Она будет доступна для будущих писем."
                )
            else:
                pending_profile_change = {
                    **state,
                    "value": text,
                }
                label = "Зарплатные ожидания" if state["kind"] == "compensation" else "Английский"
                preview = f"Проверьте перед сохранением:\n\n{label}: {text}"
        except (OllamaUnavailableError, CandidateProfileError) as exc:
            await message.answer(f"Не удалось подготовить данные: {exc}")
            return
        await message.answer(preview, reply_markup=profile_confirmation_keyboard())
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
