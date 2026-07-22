import hashlib
import json
import re
from pathlib import Path

import aiohttp
from config import (
    APPLICANT_NAME,
    CANDIDATE_PROFILE_PATH,
    MY_RESUME_SUMMARY,
    OLLAMA_MODEL,
    OLLAMA_URL,
)


class OllamaUnavailableError(RuntimeError):
    """Ollama недоступна или вернула ответ, который нельзя использовать."""


class CandidateProfileError(RuntimeError):
    """Закрытый профиль кандидата отсутствует или заполнен неверно."""


COVER_LETTER_FOCUSES = {
    "strategy": {
        "description": "продуктовая стратегия, vision, roadmap и приоритеты",
        "task": "выбор продуктового направления и приоритетов",
        "interest": "определять направление развития продукта",
        "experience": "продуктовой стратегии",
        "outcome": "связать продуктовые решения с целями бизнеса",
    },
    "discovery": {
        "description": "discovery, исследования, JTBD и проверка гипотез",
        "task": "discovery и проверка продуктовых гипотез",
        "interest": "находить сильные решения через исследования и эксперименты",
        "experience": "discovery и проверке гипотез",
        "outcome": "быстрее находить решения с подтверждённой ценностью",
    },
    "growth": {
        "description": "рост MAU, retention, conversion, activation и NPS",
        "task": "рост ключевых продуктовых метрик",
        "interest": "масштабировать продукт на основе данных",
        "experience": "росте и удержании пользователей",
        "outcome": "улучшить ключевые продуктовые метрики",
    },
    "monetization": {
        "description": "выручка, монетизация, unit economics, P&L и LTV",
        "task": "монетизация и связь продукта с экономикой бизнеса",
        "interest": "развивать монетизацию без ухудшения клиентского опыта",
        "experience": "монетизации и юнит-экономике",
        "outcome": "повысить бизнес-эффект продукта",
    },
    "launch": {
        "description": "запуск продукта с нуля, MVP и выход на рынок",
        "task": "запуск и развитие новых продуктов",
        "interest": "проводить продукт от идеи до работающего решения",
        "experience": "запуске продуктов с нуля",
        "outcome": "снизить неопределённость нового направления",
    },
    "b2b": {
        "description": "B2B, корпоративные клиенты и сложные пользовательские роли",
        "task": "развитие B2B-продукта",
        "interest": "решать задачи корпоративных пользователей",
        "experience": "B2B-продуктах",
        "outcome": "создать ценность для корпоративных клиентов",
    },
    "fintech": {
        "description": "финтех, банки, платежи, кредитные и финансовые продукты",
        "task": "развитие финансового цифрового продукта",
        "interest": "улучшать сложные финансовые сценарии",
        "experience": "финтех-продуктах",
        "outcome": "упростить клиентские финансовые сценарии",
    },
    "team": {
        "description": "кросс-функциональная команда, stakeholders и руководство",
        "task": "синхронизация команды и заинтересованных сторон",
        "interest": "объединять команду вокруг продуктового результата",
        "experience": "управлении кросс-функциональными командами",
        "outcome": "ускорить принятие и реализацию продуктовых решений",
    },
    "process": {
        "description": "delivery, процессы, time-to-market и скорость команды",
        "task": "ускорение delivery и продуктовых процессов",
        "interest": "сокращать путь от гипотезы до результата",
        "experience": "discovery и delivery",
        "outcome": "ускорить выпуск проверенных изменений",
    },
    "ai": {
        "description": "AI, LLM, автоматизация и продукты на основе ИИ",
        "task": "развитие продукта с AI-функциями",
        "interest": "применять AI там, где он даёт измеримую ценность",
        "experience": "практическом применении локальных LLM для автоматизации",
        "outcome": "найти практичные сценарии применения AI",
    },
}


async def _ask_ollama(
    prompt: str,
    timeout_seconds: int = 60,
    temperature: float = 0.1,
    max_output_tokens: int = 768,
    response_format=None,
) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_output_tokens,
        },
    }
    if response_format is not None:
        payload["format"] = response_format

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OLLAMA_URL, json=payload) as response:
                response.raise_for_status()
                data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise OllamaUnavailableError(
            f"Ollama недоступна по адресу {OLLAMA_URL}: {error}"
        ) from error

    answer = data.get("response", "").strip()
    if not answer:
        raise OllamaUnavailableError("Ollama вернула пустой ответ.")
    return answer


def _load_candidate_profile() -> dict:
    path = Path(CANDIDATE_PROFILE_PATH)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CandidateProfileError(f"Не найден профиль кандидата: {path}") from error
    except (json.JSONDecodeError, OSError) as error:
        raise CandidateProfileError(f"Не удалось прочитать профиль: {path}") from error

    for field in ("positioning", "specialization", "seniority"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise CandidateProfileError(f"В профиле не заполнено поле: {field}")

    facts = profile.get("facts")
    if not isinstance(facts, list) or len(facts) < 2:
        raise CandidateProfileError("В профиле должно быть минимум два факта")

    fact_ids = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise CandidateProfileError("Каждый факт должен быть объектом")
        fact_id = fact.get("id")
        text = fact.get("text")
        focus_ids = fact.get("focus_ids")
        if not isinstance(fact_id, str) or not fact_id or fact_id in fact_ids:
            raise CandidateProfileError("ID фактов должны быть уникальными")
        if not isinstance(text, str) or not text.strip():
            raise CandidateProfileError(f"Не заполнен текст факта: {fact_id}")
        if (
            not isinstance(focus_ids, list)
            or not focus_ids
            or any(focus_id not in COVER_LETTER_FOCUSES for focus_id in focus_ids)
        ):
            raise CandidateProfileError(f"Неверные focus_ids факта: {fact_id}")
        fact_ids.add(fact_id)

    forbidden_terms = profile.get("forbidden_terms", [])
    if not isinstance(forbidden_terms, list) or any(
        not isinstance(term, str) or not term for term in forbidden_terms
    ):
        raise CandidateProfileError("forbidden_terms должен быть списком строк")
    return profile


async def _select_cover_letter_focuses(
    vacancy_title: str, vacancy_description: str
) -> list[str]:
    focus_descriptions = "\n".join(
        f"- {focus_id}: {focus['description']}"
        for focus_id, focus in COVER_LETTER_FOCUSES.items()
    )
    prompt = f"""
Выбери ровно два направления, которые важнее всего для этой вакансии.
Не пиши письмо и не добавляй объяснения.

Направления:
{focus_descriptions}

Вакансия: {vacancy_title}
Описание: {vacancy_description}

Верни только JSON с полем focus_ids.
"""
    focus_ids = list(COVER_LETTER_FOCUSES)
    response_format = {
        "type": "object",
        "properties": {
            "focus_ids": {
                "type": "array",
                "items": {"type": "string", "enum": focus_ids},
                "minItems": 2,
                "maxItems": 2,
                "uniqueItems": True,
            }
        },
        "required": ["focus_ids"],
    }
    answer = await _ask_ollama(
        prompt,
        timeout_seconds=30,
        temperature=0.0,
        max_output_tokens=48,
        response_format=response_format,
    )
    try:
        result = json.loads(answer)
    except (json.JSONDecodeError, TypeError) as error:
        raise OllamaUnavailableError("Ollama вернула неверный выбор фактов") from error

    selected = result.get("focus_ids") if isinstance(result, dict) else None
    if (
        not isinstance(selected, list)
        or len(selected) != 2
        or len(set(selected)) != 2
        or any(focus_id not in COVER_LETTER_FOCUSES for focus_id in selected)
    ):
        raise OllamaUnavailableError("Ollama вернула неверный выбор фактов")
    vacancy_text = f"{vacancy_title}\n{vacancy_description}".lower()
    ai_markers = (" ai ", "ai-", " llm", "ии-", "искусственн", "нейросет")
    padded_text = f" {vacancy_text} "
    if any(marker in padded_text for marker in ai_markers) and "ai" not in selected:
        selected[-1] = "ai"
    return selected


def _select_profile_facts(profile: dict, focus_ids: list[str]) -> list[dict]:
    selected = []
    for focus_id in focus_ids:
        candidates = [
            fact
            for fact in profile["facts"]
            if focus_id in fact["focus_ids"] and fact not in selected
        ]
        if candidates:
            candidates.sort(
                key=lambda fact: (
                    -sum(item in fact["focus_ids"] for item in focus_ids),
                    fact["id"],
                )
            )
            selected.append(candidates[0])

    if len(selected) < 2:
        remaining = [fact for fact in profile["facts"] if fact not in selected]
        remaining.sort(
            key=lambda fact: (
                -sum(item in fact["focus_ids"] for item in focus_ids),
                fact["id"],
            )
        )
        selected.extend(remaining[: 2 - len(selected)])
    return selected


def _compose_cover_letter(
    vacancy_title: str,
    profile: dict,
    focus_ids: list[str],
    facts: list[dict],
) -> str:
    focuses = [COVER_LETTER_FOCUSES[focus_id] for focus_id in focus_ids]
    fact_text = " ".join(fact["text"].strip() for fact in facts)
    first, second = focuses

    variants = [
        (
            f"{profile['positioning']} {profile['specialization']}",
            f"В описании роли вижу две ключевые задачи. Первая — "
            f"{first['task']}. Вторая — {second['task']}. {fact_text}",
            f"{profile['seniority']} Поэтому мне интересна возможность "
            f"{first['interest']}. Также хочу {second['interest']}.",
            f"Буду рад обсудить, как мой опыт в {first['experience']} поможет "
            f"вашей команде {first['outcome']}. Отдельно могу быть полезен в "
            f"{second['experience']}.",
        ),
        (
            f"{profile['positioning']} {profile['specialization']}",
            f"Для этой роли особенно важны две задачи. Первая — "
            f"{first['task']}. Вторая — {second['task']}. {fact_text}",
            f"Мне близка возможность {first['interest']}. Также интересна "
            f"задача {second['interest']}. {profile['seniority']}",
            f"Буду рад обсудить, как опыт в {first['experience']} может помочь "
            f"вашей команде {first['outcome']}. Дополнительно могу усилить "
            f"работу в {second['experience']}.",
        ),
        (
            f"{profile['positioning']} {profile['specialization']}",
            f"Две задачи вакансии напрямую связаны с моим опытом. Первая — "
            f"{first['task']}. Вторая — {second['task']}. {fact_text}",
            f"{profile['seniority']} В этой роли мне интересна возможность "
            f"{first['interest']}. Кроме того, хочу {second['interest']}.",
            f"Буду рад обсудить, как мой опыт в {first['experience']} поможет "
            f"вам {first['outcome']}. Также могу быть полезен в "
            f"{second['experience']}.",
        ),
    ]
    variant_index = int(hashlib.sha256(vacancy_title.encode()).hexdigest(), 16) % 3
    paragraphs = variants[variant_index]
    return "Здравствуйте!\n\n" + "\n\n".join(paragraphs) + f"\n\n{APPLICANT_NAME}"


def _cover_letter_issues(text: str, profile: dict) -> list[str]:
    issues = []
    if not text.startswith("Здравствуйте!"):
        issues.append("письмо должно начинаться с «Здравствуйте!»")
    if not text.rstrip().endswith(APPLICANT_NAME):
        issues.append(f"последняя строка должна быть: {APPLICANT_NAME}")

    forbidden_terms = ["С уважением", *profile.get("forbidden_terms", [])]
    for term in forbidden_terms:
        if term.lower() in text.lower():
            issues.append(f"запрещено упоминание: {term}")

    if re.search(r"\b[\w.+-]+@[\w.-]+\.\w+\b", text):
        issues.append("нельзя добавлять email")
    if re.search(r"(?:\+7|8\s?\(?\d{3}\)?)[\d\s()-]{7,}", text):
        issues.append("нельзя добавлять телефон")
    word_count = len(re.findall(r"\b[\w-]+\b", text, flags=re.UNICODE))
    if not 150 <= word_count <= 250:
        issues.append(f"длина должна быть 150–250 слов, сейчас: {word_count}")
    if len(text.split("\n\n")) != 6:
        issues.append("письмо должно содержать четыре смысловых блока")
    return issues


async def generate_cover_letter(vacancy_title: str, vacancy_description: str) -> str:
    profile = _load_candidate_profile()
    focus_ids = await _select_cover_letter_focuses(
        vacancy_title, vacancy_description
    )
    facts = _select_profile_facts(profile, focus_ids)
    text = _compose_cover_letter(vacancy_title, profile, focus_ids, facts)
    issues = _cover_letter_issues(text, profile)
    if issues:
        raise CandidateProfileError("; ".join(issues))
    return text


async def is_vacancy_suitable(vacancy_title: str, vacancy_description: str) -> bool:
    prompt = f"""
Твоя задача — оценить, подходит ли вакансия под мои критерии поиска.
Мои требования и профиль:
{MY_RESUME_SUMMARY}

Также мне СТРОГО НЕ подходят (отклоняй сразу, отвечая NO):
- Project Manager, менеджер проектов, Scrum Master, аккаунт-менеджер, менеджер
  по продажам, руководитель продаж, Head of Sales, X-Sell Head и Cross-Sell Head.
- Чисто маркетинговые, аналитические, дизайнерские, HR и инженерные роли без ответственности за продукт.
- Стажировки и позиции Junior.
- Роли, где нет управления цифровым продуктом, продуктовой стратегией, discovery/delivery, метриками или развитием продукта.

Подходящие направления: Product Manager, Product Owner, Senior Product Manager,
Head of Product, CPO и Chief Product Officer. Fintech и B2B/B2C digital products
особенно релевантны, но сильные продуктовые роли в других цифровых отраслях тоже подходят.

Вакансия:
Название: {vacancy_title}
Описание: {vacancy_description}

Верни только JSON с логическим полем suitable: true, если вакансия подходит,
или false, если не подходит.
"""

    response_format = {
        "type": "object",
        "properties": {"suitable": {"type": "boolean"}},
        "required": ["suitable"],
    }
    answer = await _ask_ollama(
        prompt,
        timeout_seconds=30,
        temperature=0.0,
        max_output_tokens=32,
        response_format=response_format,
    )
    try:
        result = json.loads(answer)
    except (json.JSONDecodeError, TypeError) as error:
        raise OllamaUnavailableError(
            f"Ollama вернула некорректный результат анализа: {answer[:100]}"
        ) from error

    if (
        not isinstance(result, dict)
        or set(result) != {"suitable"}
        or not isinstance(result["suitable"], bool)
    ):
        raise OllamaUnavailableError(
            f"Ollama вернула некорректный результат анализа: {answer[:100]}"
        )
    return result["suitable"]
