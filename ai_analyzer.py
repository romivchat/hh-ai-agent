import json
import re

import aiohttp
from config import (
    APPLICANT_NAME,
    GITHUB_URL,
    MY_RESUME_SUMMARY,
    OLLAMA_MODEL,
    OLLAMA_URL,
)


class OllamaUnavailableError(RuntimeError):
    """Ollama недоступна или вернула ответ, который нельзя использовать."""


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


def _cover_letter_issues(
    text: str, vacancy_description: str = ""
) -> list[str]:
    issues = []
    if not text.startswith(("Здравствуйте!", "Добрый день!")):
        issues.append("письмо должно начинаться с нейтрального приветствия")
    if not text.rstrip().endswith(APPLICANT_NAME):
        issues.append(f"последняя строка должна быть: {APPLICANT_NAME}")

    forbidden = {
        "С уважением": "запрещена формула «С уважением»",
        f"Дорогой {APPLICANT_NAME.split()[0]}": "нельзя обращаться к кандидату",
        f"Уважаемый {APPLICANT_NAME.split()[0]}": "нельзя обращаться к кандидату",
        "На Альфа-Банке": "нужно писать «В Альфа-Банке»",
        "На Ренессансе": "нужно писать «В Банке Ренессанс»",
    }
    for fragment, problem in forbidden.items():
        if fragment.lower() in text.lower():
            issues.append(problem)

    if re.search(r"\b[\w.+-]+@[\w.-]+\.\w+\b", text):
        issues.append("нельзя добавлять email")
    if re.search(r"(?:\+7|8\s?\(?\d{3}\)?)[\d\s()-]{7,}", text):
        issues.append("нельзя добавлять телефон")
    if re.search(r"\b(?:Dear|I am|Best regards|Here is)\b", text, re.IGNORECASE):
        issues.append("нельзя добавлять английские фразы")
    if re.search(r"\bдо\s+-\s*\d", text, re.IGNORECASE):
        issues.append("нельзя использовать отрицательное конечное значение")

    mentions_github = "github" in text.lower() or bool(
        GITHUB_URL and GITHUB_URL.lower() in text.lower()
    )
    asks_for_portfolio = any(
        marker in vacancy_description.lower()
        for marker in ("github", "портфолио", "примеры проектов")
    )
    allowed_github_text = f"Мои проекты по автоматизации: {GITHUB_URL}"
    if mentions_github and not asks_for_portfolio:
        issues.append("GitHub можно упоминать только по прямому запросу вакансии")
    elif mentions_github and allowed_github_text not in text:
        issues.append(
            f"GitHub можно указать только точной фразой: {allowed_github_text}"
        )

    allowed_llm_text = (
        "В личных проектах использую локальные LLM для автоматизации."
    )
    other_llm_claims = text.replace(allowed_llm_text, "")
    if "llm" in other_llm_claims.lower():
        issues.append(
            f"про LLM можно написать только точную фразу: {allowed_llm_text}"
        )
    return issues


async def _cover_letter_fact_issues(text: str) -> list[str]:
    prompt = f"""
Сравни сопроводительное письмо с профилем кандидата.
Проверь каждый факт, число, работодателя и причинную связь. Если в письме есть
факт не из профиля, достижение перенесено между работодателями, число искажено
или добавлена неподтверждённая причинная связь, valid должен быть false.

Профиль:
{MY_RESUME_SUMMARY}

Письмо:
{text}

Верни только JSON.
"""
    response_format = {
        "type": "object",
        "properties": {"valid": {"type": "boolean"}},
        "required": ["valid"],
    }
    answer = await _ask_ollama(
        prompt,
        timeout_seconds=60,
        temperature=0.0,
        max_output_tokens=32,
        response_format=response_format,
    )
    try:
        result = json.loads(answer)
    except (json.JSONDecodeError, TypeError) as error:
        raise OllamaUnavailableError(
            "Ollama вернула некорректную проверку фактов."
        ) from error

    if (
        not isinstance(result, dict)
        or set(result) != {"valid"}
        or not isinstance(result["valid"], bool)
    ):
        raise OllamaUnavailableError(
            "Ollama вернула некорректную проверку фактов."
        )
    if result["valid"]:
        return []
    return ["проверка фактов: письмо содержит неподтверждённое утверждение"]


async def generate_cover_letter(vacancy_title: str, vacancy_description: str) -> str:
    base_prompt = f"""
Напиши сопроводительное письмо для отклика на вакансию.
Кандидат {APPLICANT_NAME} пишет работодателю. Работодатель не является кандидатом.
Мой профиль:
{MY_RESUME_SUMMARY}

Вакансия: {vacancy_title}
Описание: {vacancy_description}

КРИТИЧЕСКИЕ ПРАВИЛА (СТРОГО СОБЛЮДАТЬ):
1. Начни строго с «Здравствуйте!». Не пиши имя кандидата в начале.
2. Пиши только на русском языке, 3-4 коротких абзаца, 700-1500 знаков.
3. Стиль: живой, профессиональный, конкретный. Свяжи требования вакансии с 2-3 наиболее релевантными достижениями кандидата.
4. Используй только факты из моего профиля. Не придумывай опыт, образование, проекты или навыки.
Если вакансия связана с LLM, можно использовать только точную фразу:
«В личных проектах использую локальные LLM для автоматизации.»
Не связывай LLM с опытом в банках и не называй это коммерческим опытом.
5. Упоминай GitHub только если в описании вакансии прямо просят GitHub,
портфолио или примеры проектов. В таком случае используй только точную фразу:
«Мои проекты по автоматизации: {GITHUB_URL}». Ничего не выдумывай о содержимом
GitHub и не представляй эти проекты как коммерческий опыт.
6. Не добавляй телефон, email, адрес, должность кандидата отдельной шапкой или другие контактные данные.
7. Не называй банковский продукт собственностью кандидата. Не объединяй достижения разных работодателей.
Используй формы «В Альфа-Банке» и «В Банке Ренессанс».
8. Последняя строка строго «{APPLICANT_NAME}». Перед ней не пиши «С уважением».
9. Выводи только письмо без кавычек, заголовка, пояснений и вводных фраз.
"""

    issues = []
    for _ in range(3):
        prompt = base_prompt
        if issues:
            prompt += "\nПредыдущий вариант отклонён. Исправь ошибки:\n- " + "\n- ".join(
                issues
            )
        text = await _ask_ollama(
            prompt, timeout_seconds=180, temperature=0.2
        )
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        issues = _cover_letter_issues(text, vacancy_description)
        if not issues:
            issues = await _cover_letter_fact_issues(text)
        if not issues:
            return text

    raise OllamaUnavailableError(
        "Ollama трижды нарушила правила письма: " + "; ".join(issues)
    )

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
