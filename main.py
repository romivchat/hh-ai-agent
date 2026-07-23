import asyncio
from config import validate_configuration
from database import count_pending_jobs, init_db
from tg_bot import (
    send_notification,
    send_pending_vacancy,
    set_application_handler,
    set_regeneration_handler,
    set_search_handler,
    start_bot,
)
from hh_client import HHClient

async def agent_loop():
    client = HHClient()
    await client.start()
    
    # Первая авторизация
    logged_in = await client.login_if_needed()
    if not logged_in:
        print("Не удалось авторизоваться. Завершение работы.")
        await client.stop()
        return

    set_application_handler(client.apply_pending_job)
    set_regeneration_handler(client.regenerate_pending_job)
    search_lock = asyncio.Lock()

    async def run_search() -> tuple[bool, str]:
        if search_lock.locked():
            return False, "Поиск уже выполняется. Новые вакансии придут в этот чат."
        async with search_lock:
            await client.search_and_queue(send_notification, send_pending_vacancy)
            pending_count = count_pending_jobs()
            return True, f"Поиск завершён. Ожидают решения: {pending_count}."

    set_search_handler(run_search)

    # Один раз обновляем старые сгенерированные письма. Ручные правки защищены.
    await client.regenerate_pending_jobs()

    await send_notification("🤖 ИИ-агент успешно запущен и начал работу!")

    try:
        while True:
            try:
                # Ищем вакансии и отправляем их в Telegram на согласование.
                search_started, _ = await run_search()
                
                # Проверяем чаты
                if search_started:
                    await client.check_chats(send_notification)
            except Exception as e:
                print(f"Ошибка в основном цикле агента: {e}")
            
            # Ждем 30 минут перед следующим запуском
            print("Ожидание 30 минут...")
            await asyncio.sleep(1800)
    finally:
        set_application_handler(None)
        set_regeneration_handler(None)
        set_search_handler(None)
        await client.stop()

async def main():
    validate_configuration()

    # Инициализация БД
    init_db()
    print("Инициализация завершена.")
    
    # Запускаем бота и логику агента параллельно
    bot_task = asyncio.create_task(start_bot())
    agent_task = asyncio.create_task(agent_loop())
    
    await asyncio.gather(bot_task, agent_task)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Остановка работы.")
