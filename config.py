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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct")

# Данные кандидата для сопроводительных писем
APPLICANT_NAME = os.getenv("APPLICANT_NAME", "ИМЯ_НЕ_НАСТРОЕНО")
GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/romivchat")

# HH.ru настройки
# Ключевые слова для поиска (backend, python, c, c++, cv)
SEARCH_QUERIES = [
    "Python backend", 
    "Python разработчик",
    "FastAPI",
    "C++ разработчик", 
    "Программист C++",
    "Фулстек Python", 
    "Computer Vision",
    "Backend Developer",
    "Backend Python"
]
# Название резюме, которое агент должен выбирать при отклике (должно в точности совпадать с тем, что написано на HH)
TARGET_RESUME_NAME = os.getenv("TARGET_RESUME_NAME", "РЕЗЮМЕ_НЕ_НАСТРОЕНО")

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
    if TARGET_RESUME_NAME in {
        "РЕЗЮМЕ_НЕ_НАСТРОЕНО",
        "ТОЧНОЕ_НАЗВАНИЕ_РЕЗЮМЕ_НА_HH",
    }:
        missing.append("TARGET_RESUME_NAME")
    if MY_RESUME_SUMMARY in {
        "ПРОФИЛЬ_НЕ_НАСТРОЕН",
        "ПОДРОБНОЕ_ОПИСАНИЕ_ОПЫТА_НАВЫКОВ_И_ПОЖЕЛАНИЙ",
    }:
        missing.append("MY_RESUME_SUMMARY")

    if missing:
        raise RuntimeError(
            "Заполните обязательные настройки в .env: " + ", ".join(missing)
        )
