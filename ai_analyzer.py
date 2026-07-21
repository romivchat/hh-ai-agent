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


async def _ask_ollama(prompt: str, timeout_seconds: int = 60) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

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


async def generate_cover_letter(vacancy_title: str, vacancy_description: str) -> str:
    prompt = f"""
Напиши сопроводительное письмо для отклика на вакансию.
Мой профиль:
{MY_RESUME_SUMMARY}

Вакансия: {vacancy_title}
Описание: {vacancy_description}

КРИТИЧЕСКИЕ ПРАВИЛА (СТРОГО СОБЛЮДАТЬ):
1. ПИСАТЬ СТРОГО ТОЛЬКО НА РУССКОМ ЯЗЫКЕ! Никакого английского текста.
2. Пиши развернуто, структурировано (3-4 абзаца).
3. Стиль: живой, профессиональный, уверенный.
4. Используй только факты из моего профиля. Не придумывай опыт, образование, проекты или навыки.
5. Если в профиле есть подходящие проекты, упомяни наиболее релевантный. Вставь ссылку на мой GitHub: {GITHUB_URL}
6. Никаких подписей в начале письма! Только в самом конце.
7. Подпись строго: "{APPLICANT_NAME}". Никаких "С уважением".
8. ВЫВОДИ ТОЛЬКО ТЕКСТ ПИСЬМА БЕЗ КАВЫЧЕК. Твой ответ копируется автоматически! Строго запрещены любые вводные фразы (например, "Here is a sample...", "Вот письмо:"). Ни слова, кроме самого письма.
"""
    
    text = await _ask_ollama(prompt)
    # Жесткая очистка от частых вводных фраз модели.
    text = text.replace('"', '').replace("'", "")
    if "Here is" in text or "Here's" in text:
        text = text.split("\n\n", 1)[-1]
    if "Note:" in text:
        text = text.split("Note:")[0].strip()
    if not text.strip():
        raise OllamaUnavailableError("После очистки письмо оказалось пустым.")
    return text.strip()

async def is_vacancy_suitable(vacancy_title: str, vacancy_description: str) -> bool:
    prompt = f"""
Твоя задача — оценить, подходит ли вакансия под мои критерии поиска.
Мои требования и профиль (внимательно учти желаемую зарплату, локацию и стек технологий):
{MY_RESUME_SUMMARY}

Также мне СТРОГО НЕ подходят (отклоняй сразу, отвечая NO):
- Вакансии уровня Senior (Сеньор), Lead или Архитектор.
- Вакансии, где требуется опыт работы более 3 лет (у меня от 1 до 3 лет опыта).
- Вакансии из других сфер: менеджеры, аналитики, HR, маркетологи, дизайнеры, преподаватели, риелторы, продавцы, слесари, инженеры по эксплуатации и техподдержка.
- Любые вакансии, которые НЕ связаны напрямую с написанием кода и разработкой ПО (Backend, Fullstack, C++, Python, Computer Vision). Если вакансия не про программирование — сразу пиши NO.

Вакансия:
Название: {vacancy_title}
Описание: {vacancy_description}

Если вакансия подходит под мои критерии, ответь ТОЛЬКО одним словом: YES.
Если не подходит, ответь ТОЛЬКО одним словом: NO.
"""
    
    answer = (await _ask_ollama(prompt, timeout_seconds=30)).strip().upper()
    if answer == "YES":
        return True
    if answer == "NO":
        return False
    raise OllamaUnavailableError(
        f"Ollama вернула неожиданный ответ на анализ вакансии: {answer[:100]}"
    )
