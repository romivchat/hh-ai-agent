import asyncio
from config import validate_configuration
from database import init_db
from tg_bot import (
    send_notification,
    send_pending_vacancy,
    set_application_handler,
    set_regeneration_handler,
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

    # Один раз обновляем старые сгенерированные письма. Ручные правки защищены.
    await client.regenerate_pending_jobs()

    await send_notification("🤖 ИИ-агент успешно запущен и начал работу!")

    try:
        while True:
            try:
                # Ищем вакансии и отправляем их в Telegram на согласование.
                await client.search_and_queue(send_notification, send_pending_vacancy)
                
                # Проверяем чаты
                await client.check_chats(send_notification)
            except Exception as e:
                print(f"Ошибка в основном цикле агента: {e}")
            
            # Ждем 30 минут перед следующим запуском
            print("Ожидание 30 минут...")
            await asyncio.sleep(1800)
    finally:
        set_application_handler(None)
        set_regeneration_handler(None)
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
