import os
import asyncio
import random
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import database
from ai_analyzer import (
    CandidateProfileError,
    OllamaUnavailableError,
    analyze_and_generate,
)
from config import (
    HH_SUBMISSION_ENABLED,
    MAX_PENDING_JOBS,
    SEARCH_QUERIES,
    TARGET_RESUME_NAMES,
)

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

class HHClient:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.application_lock = asyncio.Lock()

    async def start(self):
        self.playwright = await async_playwright().start()
        # Запуск в headless=False для того, чтобы в первый раз пользователь мог войти (ввести смс/пароль),
        # либо полностью headless, если state.json существует.
        headless = os.path.exists(STATE_FILE)
        self.browser = await self.playwright.chromium.launch(headless=headless)
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        if os.path.exists(STATE_FILE):
            self.context = await self.browser.new_context(storage_state=STATE_FILE, user_agent=user_agent)
        else:
            self.context = await self.browser.new_context(user_agent=user_agent)
        
        self.page = await self.context.new_page()
        await Stealth().apply_stealth_async(self.page)

    async def login_if_needed(self):
        print("Переходим на HH.ru для проверки авторизации...")
        await self.page.goto("https://hh.ru/")
        await asyncio.sleep(3)
        
        # Ждем, пока страница реально прогрузится, чтобы не ловить "пустой" экран
        await self.page.wait_for_load_state('networkidle')
        await asyncio.sleep(2)
        
        # Ищем любую ссылку или кнопку с текстом "Войти"
        login_link = self.page.locator('a:has-text("Войти")')
        login_button = self.page.locator('button:has-text("Войти")')
        
        if not await login_link.count() and not await login_button.count():
            print("Уже авторизованы (кнопка 'Войти' не найдена).")
            return True

        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("❌ Файл сессии (state.json) недействителен. Я его удалил.")
            print("Пожалуйста, перезапустите скрипт (python main.py), чтобы открылось окно браузера для входа.")
            return False

        print("=========================================")
        print("❗ НУЖНА АВТОРИЗАЦИЯ ❗")
        print("1. В открывшемся браузере войдите в свой аккаунт HH.ru.")
        print("2. Дождитесь, пока загрузится ваш профиль.")
        print("3. ВЕРНИТЕСЬ В ЭТО ОКНО КОНСОЛИ И НАЖМИТЕ КЛАВИШУ ENTER.")
        print("=========================================")
        
        try:
            # Ожидаем нажатия Enter (в отдельном потоке, чтобы не блокировать асинхронность)
            await asyncio.to_thread(input, "👉 Нажмите ENTER здесь, когда войдете в аккаунт: ")
            
            print("⏳ Сохраняем сессию...")
            await asyncio.sleep(2) # На всякий случай даем странице загрузиться
            await self.context.storage_state(path=STATE_FILE)
            print("✅ Авторизация успешна, состояние сохранено!")
            return True
        except Exception as e:
            print(f"❌ Произошла ошибка при сохранении авторизации: {e}")
            return False

    async def search_and_queue(self, send_notification_func, send_pending_vacancy_func):
        print("Начинаем поиск вакансий...")
        if database.count_pending_jobs() >= MAX_PENDING_JOBS:
            print(f"Очередь заполнена: ожидают решения {MAX_PENDING_JOBS} вакансий.")
            return

        for query in SEARCH_QUERIES:
            print(f"\n======================================")
            print(f"🔍 Поиск по запросу: {query}")
            print(f"======================================")
            
            # Два режима поиска: сначала Москва, потом удалённые вакансии по РФ.
            search_configs = [
                {"name": "Москва (любой график)", "params": "&area=1"},
                {"name": "Вся Россия (только удаленка)", "params": "&area=113&schedule=remote"}
            ]
            
            for config in search_configs:
                print(f"📍 Режим: {config['name']}")
                url = (
                    f"https://hh.ru/search/vacancy?text={query}"
                    "&order_by=publication_time"
                    "&experience=between3And6&experience=moreThan6"
                    f"{config['params']}"
                )
                await self.page.goto(url)
                await asyncio.sleep(3)
                page_num = 1
                while True:
                    print(f"📄 Парсим страницу {page_num} по запросу '{query}' ({config['name']})...")
                    vacancies = await self.page.locator('a[data-qa="serp-item__title"]').all()
                
                    # Собираем ссылки заранее, чтобы избежать ошибки Detached Node при долгом парсинге
                    links_to_process = []
                    for v in vacancies:
                        href = await v.get_attribute("href")
                        title = await v.inner_text()
                        if href:
                            links_to_process.append((title, href))
                        
                    for title, href in links_to_process:
                        if database.count_pending_jobs() >= MAX_PENDING_JOBS:
                            print(f"Очередь заполнена: ожидают решения {MAX_PENDING_JOBS} вакансий.")
                            return

                        # Парсим ID вакансии из URL (https://hh.ru/vacancy/123456?...)
                        job_id = None
                        if "vacancy/" in href:
                            job_id = href.split("vacancy/")[1].split("?")[0]
                    
                        if not job_id or database.is_job_processed(job_id):
                            # print(f"Пропускаем (уже обработано): {title}") # Раскомментировать, если нужно видеть все пропуски
                            continue
                    
                        print(f"👁️ Открываем вакансию: {title}")
                        page = await self.context.new_page()
                        await Stealth().apply_stealth_async(page)
                        try:
                            await page.goto(href)
                            await asyncio.sleep(2)
                        
                            desc_loc = page.locator('div[data-qa="vacancy-description"]')
                            # Если описания нет - возможно капча. Запускаем цикл решения.
                            while not await desc_loc.is_visible():
                                print(f"⚠️ Описание не найдено. Возможно, вылезла капча: {title}")
                                import tg_bot
                                
                                try:
                                    # Делаем скриншот видимой области (без full_page, чтобы не триггерить ресайз окна)
                                    await page.screenshot(path="captcha.png")
                                    await tg_bot.send_captcha_request("captcha.png", f"🚨 <b>Подозрение на капчу!</b>\nБот застрял на вакансии <i>{title}</i>.\n\nПожалуйста, введите текст с картинки прямо в этот чат (если там два слова, введите через пробел):")
                                    
                                    print("Ожидаем ввод капчи из Telegram...")
                                    # Ожидание снятия блокировки (когда юзер введет текст)
                                    await tg_bot.captcha_event.wait()
                                    
                                    # Вводим текст
                                    solution = tg_bot.captcha_solution
                                    print(f"Вводим решение: {solution}")
                                    
                                    input_field = page.locator('input[type="text"]').first
                                    if await input_field.is_visible():
                                        await input_field.click()
                                        await asyncio.sleep(random.uniform(0.5, 1.2))
                                        
                                        for char in solution:
                                            if char == " ":
                                                await asyncio.sleep(random.uniform(0.6, 1.5)) # Медленный пробел между словами
                                            await input_field.type(char, delay=random.randint(150, 400)) # Человечный ввод
                                            
                                        await asyncio.sleep(random.uniform(1.0, 2.5))
                                        await input_field.press('Enter')
                                        await asyncio.sleep(5) # Ждем прогрузки после ввода
                                    else:
                                        # Если поля ввода нет (возможно это галочка Cloudflare или вы уже решили её в другом браузере)
                                        # Просто обновляем страницу, чтобы проверить, не снят ли бан по IP
                                        print("Поле ввода не найдено. Обновляем страницу...")
                                        await page.reload()
                                        await asyncio.sleep(4)
                                    
                                    # Проверяем, появилось ли описание
                                    desc_loc = page.locator('div[data-qa="vacancy-description"]')
                                    if await desc_loc.is_visible():
                                        try:
                                            await send_notification_func("✅ Капча успешно пройдена! Бот продолжает работу.")
                                        except:
                                            pass
                                        print("✅ Капча пройдена!")
                                        break # Выходим из цикла решения капчи
                                    else:
                                        try:
                                            await send_notification_func("❌ Капча решена неверно (или появилась новая). Пробуем еще раз!")
                                        except:
                                            pass
                                        print("❌ Капча не пройдена. Повторная попытка...")
                                        # Цикл while начнется заново: сделает новый скриншот и попросит ввод
                                        
                                except Exception as e:
                                    print(f"Ошибка при обработке капчи: {e}")
                                    break # В случае системной ошибки выходим, чтобы не зациклиться
                            description = await desc_loc.inner_text()

                            # Базовый фильтр только для явно непродуктовых ролей.
                            title_lower = title.lower()
                            stop_phrases = [
                                "junior", "стажер", "стажёр", "intern", "trainee",
                                "project manager", "менеджер проектов", "scrum master",
                                "product marketing", "маркетолог", "marketing manager",
                                "sales manager", "менеджер по продажам", "аккаунт-менеджер",
                                "руководитель продаж", "директор по продажам",
                                "head of sales", "x-sell head", "cross-sell head",
                                "бизнес-аналитик", "data analyst", "product analyst",
                                "продуктовый аналитик", "разработчик", "developer",
                                "дизайнер", "designer", "рекрутер", "риелтор",
                            ]
                            if any(phrase in title_lower for phrase in stop_phrases):
                                print(f"⏩ Пропускаем (непродуктовая роль): {title}")
                                database.add_filtered_job(job_id, title, href)
                                continue
                            
                            # Один структурированный анализ используется и для
                            # оценки релевантности, и для безопасного письма.
                            result = await analyze_and_generate(title, description)
                            if result["analysis"]["suitable"]:
                                print(f"✨ Вакансия подходит: {title}")

                                # На этапе поиска ничего не нажимаем: только проверяем,
                                # что для вакансии доступен отклик, и ставим её в очередь.
                                apply_btn = page.locator('a[data-qa="vacancy-response-link-top"]').first
                                if await apply_btn.is_visible():
                                    queued = database.add_pending_job(
                                        job_id,
                                        title,
                                        href,
                                        result["cover_letter"],
                                        MAX_PENDING_JOBS,
                                        description=description,
                                        analysis=result["analysis"],
                                        warnings=result["warnings"],
                                        strengths=result["strengths"],
                                        letter_version=result["letter_version"],
                                    )
                                    if queued:
                                        job = database.get_job(job_id)
                                        await send_pending_vacancy_func(job)
                                        print(f"Вакансия ожидает решения в Telegram: {title}")
                                    elif database.count_pending_jobs() >= MAX_PENDING_JOBS:
                                        print("Очередь решений заполнена.")
                                        return
                                else:
                                    print(f"Кнопка отклика не найдена: {title}")
                                    database.add_filtered_job(job_id, title, href)
                            else:
                                print(f"❌ ИИ отклонил: {title}")
                                database.add_filtered_job(job_id, title, href)
                            
                        except (OllamaUnavailableError, CandidateProfileError) as e:
                            print(f"Анализ вакансий приостановлен: {e}")
                            return
                        except Exception as e:
                            print(f"Ошибка при обработке вакансии {title}: {e}")
                        finally:
                            await page.close()
                    
                    # После того как все вакансии на странице обработаны, проверяем кнопку "Дальше"
                    next_btn = self.page.locator('a[data-qa="pager-next"]')
                    if await next_btn.count() > 0 and await next_btn.is_visible():
                        print("➡️ Переходим на следующую страницу...")
                        await next_btn.click()
                        await asyncio.sleep(4)
                        page_num += 1
                    else:
                        print("🛑 Больше страниц нет, переходим к следующему запросу.")
                        break

    async def regenerate_pending_job(
        self,
        job_id: str,
        force: bool = True,
    ) -> tuple[bool, str]:
        job = database.get_job(job_id)
        if not job or job["status"] != database.PENDING:
            return False, "Вакансия уже обработана."
        if job["letter_edited"]:
            return False, "Письмо изменено вручную и не было перезаписано."
        if not force and job["letter_version"] >= 2 and job["description"]:
            return True, "Письмо уже использует новую версию."
        if self.context is None:
            return False, "Браузер HH ещё не готов."

        page = None
        try:
            description = job["description"]
            if not description:
                page = await self.context.new_page()
                await Stealth().apply_stealth_async(page)
                await page.goto(job["url"])
                await asyncio.sleep(2)
                desc_loc = page.locator('div[data-qa="vacancy-description"]')
                if not await desc_loc.is_visible():
                    warning = "Вакансия недоступна: старое письмо не пересобрано."
                    database.add_job_warning(job_id, warning)
                    return False, warning
                description = await desc_loc.inner_text()

            result = await analyze_and_generate(job["title"], description)
            updated = database.update_generated_job(
                job_id,
                description,
                result["cover_letter"],
                result["analysis"],
                result["warnings"],
                result["strengths"],
                result["letter_version"],
            )
            if not updated:
                return False, "Письмо изменено вручную или вакансия уже обработана."
            return True, "Карточка и письмо пересобраны."
        except (OllamaUnavailableError, CandidateProfileError) as exc:
            warning = f"Не удалось пересобрать письмо: {exc}"
            database.add_job_warning(job_id, warning)
            return False, warning
        except Exception as exc:
            warning = f"Не удалось загрузить вакансию: {exc}"
            database.add_job_warning(job_id, warning)
            return False, warning
        finally:
            if page is not None:
                await page.close()

    async def regenerate_pending_jobs(self) -> None:
        jobs = database.list_pending_jobs()
        candidates = [
            job
            for job in jobs
            if not job["letter_edited"]
            and (job["letter_version"] < 2 or not job["description"])
        ]
        if not candidates:
            return
        print(f"Пересобираем старые письма: {len(candidates)}")
        for job in candidates:
            success, message = await self.regenerate_pending_job(job["id"], force=False)
            print(f"{job['title']}: {message}")

    async def _verify_application_sent(self, job_url: str) -> bool:
        """Confirm the response in HH instead of trusting the submit click."""
        if self.context is None:
            return False

        verification_page = await self.context.new_page()
        try:
            await Stealth().apply_stealth_async(verification_page)
            await verification_page.goto(job_url, wait_until="domcontentloaded")
            await asyncio.sleep(3)
            chat_link = verification_page.locator(
                '[data-qa="vacancy-response-link-view-topic"]'
            ).first
            return await chat_link.is_visible()
        finally:
            await verification_page.close()

    async def apply_pending_job(self, job_id: str) -> tuple[bool, str]:
        """Отправляет отклик только после явного нажатия кнопки в Telegram."""
        if not HH_SUBMISSION_ENABLED:
            return (
                False,
                "Отправка откликов временно отключена настройкой "
                "HH_SUBMISSION_ENABLED. Вакансия осталась в ожидании.",
            )

        async with self.application_lock:
            if self.context is None:
                return False, "Браузер HH ещё не готов. Попробуйте немного позже."

            job = database.claim_pending_job(job_id)
            if job is None:
                return False, "Вакансия уже обработана или отправляется."

            page = None
            try:
                page = await self.context.new_page()
                await Stealth().apply_stealth_async(page)
                await page.goto(job["url"])
                await asyncio.sleep(2)

                apply_btn = page.locator('a[data-qa="vacancy-response-link-top"]').first
                if not await apply_btn.is_visible():
                    raise RuntimeError("кнопка отклика недоступна или вакансия закрыта")

                await page.mouse.move(random.randint(100, 700), random.randint(100, 500))
                await page.mouse.wheel(0, random.randint(200, 600))
                await asyncio.sleep(random.uniform(0.8, 1.5))
                await apply_btn.click()
                await asyncio.sleep(3)

                if TARGET_RESUME_NAMES:
                    resume_dropdown = page.locator(
                        '[data-qa*="resume-select"], '
                        '[data-qa*="resume-selector"], '
                        '[data-qa="vacancy-response-resume-selector"]'
                    ).first
                    if await resume_dropdown.is_visible():
                        await resume_dropdown.click()
                        await asyncio.sleep(1)
                        selected_resume = None
                        for target_resume_name in TARGET_RESUME_NAMES:
                            target_resume_btn = page.get_by_text(
                                target_resume_name,
                                exact=True,
                            ).first
                            if await target_resume_btn.is_visible():
                                selected_resume = target_resume_btn
                                break
                        if selected_resume is None:
                            raise RuntimeError(
                                "не найдено резюме: " + ", ".join(TARGET_RESUME_NAMES)
                            )
                        await selected_resume.click()
                        await asyncio.sleep(1)

                toggle_btn = page.locator('[data-qa*="letter-toggle"]').or_(
                    page.locator('text="Написать сопроводительное"')
                ).or_(
                    page.locator('text="Добавить сопроводительное"')
                ).first
                if await toggle_btn.is_visible():
                    await toggle_btn.click()
                    await asyncio.sleep(1)

                letter_textarea = page.locator("textarea").first
                try:
                    await letter_textarea.wait_for(state="visible", timeout=3000)
                except Exception as exc:
                    raise RuntimeError(
                        "работодатель не разрешает добавить сопроводительное письмо"
                    ) from exc
                await letter_textarea.fill(job["cover_letter"])

                submit_btn = page.locator(
                    'button[data-qa*="vacancy-response-submit"]:visible'
                ).first
                if not await submit_btn.is_visible():
                    raise RuntimeError("кнопка отправки отклика не найдена")

                await submit_btn.click()
                if not await self._verify_application_sent(job["url"]):
                    raise RuntimeError(
                        "HH не подтвердил отправку: вакансия не появилась в откликах"
                    )
                if not database.mark_job_applied(job_id):
                    raise RuntimeError("не удалось сохранить результат в базе")

                print(f"Отклик отправлен после подтверждения: {job['title']}")
                return True, f"Отклик отправлен: {job['title']}"
            except Exception as exc:
                database.restore_pending_job(job_id)
                print(f"Ошибка подтверждённого отклика {job['title']}: {exc}")
                return (
                    False,
                    f"Отклик не отправлен: {job['title']}\n"
                    f"Причина: {exc}\n{job['url']}",
                )
            finally:
                if page is not None:
                    await page.close()

    async def check_chats(self, send_notification_func):
        print("Проверка новых сообщений в чатах HH...")
        await self.page.goto("https://hh.ru/applicant/negotiations")
        await asyncio.sleep(3)
        
        # Находим список откликов с бейджем непрочитанных сообщений (надежный поиск через filter(has=...))
        chat_cards = await self.page.locator('div[data-qa="negotiations-item"]').filter(has=self.page.locator('span[data-qa="negotiations-item-badge"]')).all()
        
        for chat_card in chat_cards:
            
            title_loc = chat_card.locator('a[data-qa="negotiations-item-vacancy-link"]')
            title = await title_loc.inner_text() if await title_loc.is_visible() else "Неизвестно"
            
            # Переходим в чат
            chat_link = await title_loc.get_attribute("href")
            if chat_link:
                chat_page = await self.context.new_page()
                await Stealth().apply_stealth_async(chat_page)
                await chat_page.goto(f"https://hh.ru{chat_link}")
                await asyncio.sleep(3)
                
                # Получаем последнее сообщение
                messages = await chat_page.locator('div[data-qa="chat-message-text"]').all()
                if messages:
                    last_msg = await messages[-1].inner_text()
                    msg_id = f"{chat_link}_{len(messages)}" # Примитивный ID
                    
                    if not database.is_message_processed(msg_id):
                        database.add_processed_message(msg_id, chat_link, last_msg)
                        await send_notification_func(f"🔔 <b>Новое сообщение от работодателя!</b>\nВакансия: {title}\n\n<i>{last_msg}</i>\n<a href='https://hh.ru{chat_link}'>Перейти к чату</a>")
                
                await chat_page.close()

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
