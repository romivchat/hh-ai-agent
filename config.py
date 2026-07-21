import os
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Telegram
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TG_USER_ID = os.getenv("TG_USER_ID", "YOUR_USER_ID_HERE")
MAX_PENDING_JOBS = int(os.getenv("MAX_PENDING_JOBS", "10"))
HH_SUBMISSION_ENABLED = os.getenv("HH_SUBMISSION_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Ollama (локальная модель)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")

# Данные кандидата для сопроводительных писем
APPLICANT_NAME = os.getenv("APPLICANT_NAME", "ИМЯ_НЕ_НАСТРОЕНО")
GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/romivchat")

# HH.ru настройки
# Ключевые слова для продуктовых вакансий
SEARCH_QUERIES = [
    "Product Manager",
    "Продакт-менеджер",
    "Product Owner",
    "Владелец продукта",
    "CPO",
    "Chief Product Officer",
    "Head of Product",
    "Руководитель продукта",
    "Директор по продукту",
]
# Названия резюме в порядке приоритета. Разделитель в .env - вертикальная черта.
_target_resume_names = os.getenv(
    "TARGET_RESUME_NAMES",
    os.getenv("TARGET_RESUME_NAME", "РЕЗЮМЕ_НЕ_НАСТРОЕНО"),
)
TARGET_RESUME_NAMES = [
    name.strip() for name in _target_resume_names.split("|") if name.strip()
]

# Резюме (для генерации сопроводительного письма)
MY_RESUME_SUMMARY = os.getenv("MY_RESUME_SUMMARY", "ПРОФИЛЬ_НЕ_НАСТРОЕН")


def validate_configuration() -> None:
    missing = []
    if TG_BOT_TOKEN in {"YOUR_BOT_TOKEN_HERE", "ВАШ_ТОКЕН_ОТ_BOTFATHER"}:
        missing.append("TG_BOT_TOKEN")
    if not TG_USER_ID.isdigit():
        missing.append("TG_USER_ID")
    if APPLICANT_NAME in {"ИМЯ_НЕ_НАСТРОЕНО", "ВАШЕ_ИМЯ"}:
        missing.append("APPLICANT_NAME")
    resume_placeholders = {
        "РЕЗЮМЕ_НЕ_НАСТРОЕНО",
        "ТОЧНОЕ_НАЗВАНИЕ_РЕЗЮМЕ_НА_HH",
    }
    if not TARGET_RESUME_NAMES or any(
        name in resume_placeholders for name in TARGET_RESUME_NAMES
    ):
        missing.append("TARGET_RESUME_NAMES")
    if MY_RESUME_SUMMARY in {
        "ПРОФИЛЬ_НЕ_НАСТРОЕН",
        "ПОДРОБНОЕ_ОПИСАНИЕ_ОПЫТА_НАВЫКОВ_И_ПОЖЕЛАНИЙ",
    }:
        missing.append("MY_RESUME_SUMMARY")

    if missing:
        raise RuntimeError(
            "Заполните обязательные настройки в .env: " + ", ".join(missing)
        )
