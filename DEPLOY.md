# Установка HH AI Agent на production-сервер

Эта инструкция рассчитана на владельца сервера. Бот взаимных подписок продолжает работать отдельно и не должен останавливаться во время установки.

## Текущий статус

- Репозиторий: `https://github.com/romivchat/hh-ai-agent`.
- Ветка для production: `main`.
- Сервер: `root@213.176.115.241`.
- Папка проекта: `/opt/projects/hh-ai-agent`.
- Сервис: `hh-ai-agent.service`.
- Реальная отправка по умолчанию отключена: `HH_SUBMISSION_ENABLED=false`.
- Серверная часть установлена в `/opt/projects/hh-ai-agent`, зависимости и Chromium подготовлены.
- Ollama работает на Mac владельца; сервер обращается к ней через закрытый SSH-туннель.

## 1. SSH-доступ

Обычная команда входа:

```bash
ssh vzbot
```

Следующая команда должна вывести имя сервера без ошибки:

```bash
ssh vzbot hostname
```

## 2. Проверить ресурсы сервера

Выполнить на сервере:

```bash
hostnamectl
python3 --version
free -h
df -h / /opt
nproc
docker ps
ss -ltn | grep 11434
```

Зачем: Playwright требует Python 3.10 или новее. Ollama на сервер не устанавливается, потому что модель запускается на Mac.

## 3. Скачать собственный репозиторий

Выполнить на сервере от `root`:

```bash
mkdir -p /opt/projects
cd /opt/projects
git clone https://github.com/romivchat/hh-ai-agent.git hh-ai-agent
cd /opt/projects/hh-ai-agent
git checkout main
```

Проверить источник кода:

```bash
git remote -v
git log -1 --oneline
```

В `origin` должен быть `romivchat/hh-ai-agent`, а не репозиторий автора.

## 4. Создать отдельного пользователя

```bash
useradd --system --create-home --shell /usr/sbin/nologin hh-agent
chown -R hh-agent:hh-agent /opt/projects/hh-ai-agent
```

Зачем: ошибка HH-бота не должна давать ему права на файлы других проектов и всего сервера.

## 5. Установить Python-зависимости

Сначала убедиться, что `python3 --version` показывает 3.10 или новее. Затем:

```bash
cd /opt/projects/hh-ai-agent
sudo -u hh-agent python3 -m venv .venv
sudo -u hh-agent .venv/bin/python -m pip install --upgrade pip
sudo -u hh-agent .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Версии зависимостей зафиксированы в `requirements.txt` и предварительно проверены на PyPI.

## 6. Установить Chromium для Playwright

Системные библиотеки устанавливаются от `root`:

```bash
cd /opt/projects/hh-ai-agent
PLAYWRIGHT_BROWSERS_PATH=/opt/projects/hh-ai-agent/.playwright .venv/bin/python -m playwright install-deps chromium
```

Сам браузер устанавливается от пользователя бота:

```bash
cd /opt/projects/hh-ai-agent
sudo -u hh-agent PLAYWRIGHT_BROWSERS_PATH=/opt/projects/hh-ai-agent/.playwright .venv/bin/python -m playwright install chromium
```

## 7. Создать настройки

```bash
cd /opt/projects/hh-ai-agent
cp .env.example .env
chown hh-agent:hh-agent .env
chmod 600 .env
nano .env
```

Обязательные значения:

```env
TG_BOT_TOKEN="ТОКЕН_ОТДЕЛЬНОГО_TELEGRAM_БОТА"
TG_USER_ID="ВАШ_TELEGRAM_ID"
MAX_PENDING_JOBS="10"
HH_SUBMISSION_ENABLED="false"
OLLAMA_URL="http://localhost:11434/api/generate"
OLLAMA_MODEL="qwen2.5:1.5b-instruct"
APPLICANT_NAME="ИМЯ_ДЛЯ_ПОДПИСИ"
GITHUB_URL="https://github.com/romivchat"
TARGET_RESUME_NAME="ТОЧНОЕ_НАЗВАНИЕ_РЕЗЮМЕ_НА_HH"
MY_RESUME_SUMMARY="ОПЫТ_НАВЫКИ_ПРОЕКТЫ_И_ПОЖЕЛАНИЯ"
```

На этапе настройки `HH_SUBMISSION_ENABLED` должен оставаться `false`.

Данные автора исходного проекта удалены из шаблона. Перед запуском обязательно заполнить `APPLICANT_NAME`, `TARGET_RESUME_NAME` и `MY_RESUME_SUMMARY`. Поисковые запросы `SEARCH_QUERIES` при необходимости меняются в `config.py`.

## 8. Подключить Ollama с Mac

На Mac установлены Ollama и модель `qwen2.5:1.5b-instruct`. Соединение создаёт фоновая служба `com.romiv.hh-ollama-tunnel` с отдельным ключом `~/.ssh/hh_ollama_tunnel`.

Схема соединения:

```text
бот на сервере -> 127.0.0.1:11434 на сервере
              -> зашифрованный SSH-туннель
              -> 127.0.0.1:11434 на Mac -> Ollama -> Qwen
```

На сервере проверить туннель и список моделей:

```bash
ss -ltn | grep 127.0.0.1:11434
curl --max-time 10 http://127.0.0.1:11434/api/tags
```

Порт должен слушать только `127.0.0.1`, а не `0.0.0.0`. Для туннеля создан отдельный серверный пользователь `hh-ollama-tunnel`. Его ключ запрещает консольные команды и разрешает только обратное перенаправление порта `127.0.0.1:11434`.

На Mac работают две фоновые службы. Проверить их можно командами:

```bash
launchctl print gui/$(id -u)/com.romiv.hh-ollama | grep "state ="
launchctl print gui/$(id -u)/com.romiv.hh-ollama-tunnel | grep "state ="
curl http://127.0.0.1:11434/api/tags
```

Обе команды `launchctl` должны показать `state = running`. Готовые файлы служб хранятся в `deploy/macos/`, а рабочие копии находятся в `~/Library/LaunchAgents/`. Логи записываются в `~/Library/Logs/hh-ollama.log` и `~/Library/Logs/hh-ollama-tunnel.log`.

Mac должен быть включён и не спать. При выключенном Mac бот не помечает вакансию как неподходящую: анализ останавливается и повторяется в следующем 30-минутном цикле.

## 9. Получить сессию HH.ru

Файл сессии должен находиться здесь:

```text
/opt/projects/hh-ai-agent/state.json
```

Он содержит данные авторизации и по важности равен паролю. Права после копирования:

```bash
chown hh-agent:hh-agent /opt/projects/hh-ai-agent/state.json
chmod 600 /opt/projects/hh-ai-agent/state.json
```

Не добавлять `state.json` в GitHub и не пересылать его в открытые чаты.

## 10. Проверить без отправки откликов

Убедиться, что в `.env` установлено:

```env
HH_SUBMISSION_ENABLED="false"
```

Запустить тесты:

```bash
cd /opt/projects/hh-ai-agent
sudo -u hh-agent .venv/bin/python -m unittest discover -s tests -v
```

Все тесты должны завершиться словом `OK`.

## 11. Установить systemd-сервис

```bash
cp /opt/projects/hh-ai-agent/deploy/hh-ai-agent.service /etc/systemd/system/hh-ai-agent.service
systemctl daemon-reload
systemctl start hh-ai-agent
systemctl status hh-ai-agent --no-pager
journalctl -u hh-ai-agent -n 100 --no-pager
```

На этом этапе автозапуск ещё не включать.

## 12. Проверить Telegram

Написать отдельному Telegram-боту:

```text
/start
/pending
```

Проверить кнопки:

- `Изменить письмо` сохраняет новый текст;
- `Пропустить навсегда` больше не показывает вакансию;
- `Откликнуться` при `HH_SUBMISSION_ENABLED=false` сообщает, что отправка отключена.

## 13. Разрешить отправку после полной проверки

Только после успешной проверки Telegram, базы, HH-сессии и Ollama изменить:

```env
HH_SUBMISSION_ENABLED="true"
```

Затем:

```bash
systemctl restart hh-ai-agent
journalctl -u hh-ai-agent -n 100 --no-pager
```

После одного контролируемого отклика убедиться, что:

- без кнопки ничего не отправляется;
- после кнопки отправляется последняя сохранённая версия письма;
- повторное нажатие не создаёт второй отклик.

Только после этого включить автозапуск:

```bash
systemctl enable hh-ai-agent
```

## Обновление

```bash
ssh vzbot
cd /opt/projects/hh-ai-agent
systemctl stop hh-ai-agent
cp .env .env.backup
cp state.json state.json.backup
cp agent.db agent.db.backup
git pull --ff-only origin main
sudo -u hh-agent .venv/bin/python -m pip install -r requirements.txt
sudo -u hh-agent .venv/bin/python -m unittest discover -s tests -v
systemctl start hh-ai-agent
systemctl status hh-ai-agent --no-pager
```

## Диагностика

```bash
systemctl status hh-ai-agent --no-pager
journalctl -u hh-ai-agent -f
journalctl -u hh-ai-agent -n 100 --no-pager
curl http://localhost:11434/api/tags
```

Существующий бот взаимных подписок проверяется отдельно:

```bash
cd /opt/projects/vz-podpiski
docker compose ps
```

Команды HH-бота не должны останавливать или пересобирать контейнер `vz-podpiski-bot`.
