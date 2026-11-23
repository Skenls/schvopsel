# Хамамтон

Telegram Mini App и бот для расписания учебной группы. В вебе можно перемещаться по отсекам своего судна, смотреть расписание и состояние команды.

## Локальный запуск

Скопируйте `.env.example` в `.env` и запустите демо.

```sh
test -f .env || cp .env.example .env
docker compose up -d --build --wait
```

Демо доступно на `http://127.0.0.1:8080` без входа. База демо-профилей отделена от базы аккаунтов Telegram. В публичном развёртывании используйте `mode=prod`, заполните `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` и `MAIN_ADMIN_TELEGRAM_ID`, укажите сильный `POSTGRES_PASSWORD` до первого запуска PostgreSQL. Если том уже есть, пароль пользователя PostgreSQL меняют в базе отдельно. В BotFather укажите публичный HTTPS-адрес Mini App.

## Caddy

**Отдельный Caddy** запускается через профиль `proxy`.

```sh
docker compose -f compose.yaml -f compose.proxy.yaml --profile proxy up -d --build --wait
```

Локальный адрес прокси по умолчанию `http://localhost:8081`. Для самостоятельного публичного Caddy задайте `CADDY_SITE_ADDRESS` доменом, например `hamamton.example.org`. Для выдачи публичного сертификата укажите `CADDY_BIND=0.0.0.0`, `CADDY_HTTP_PORT=80` и `CADDY_HTTPS_PORT=443`.

**Существующий Caddy в Docker** должен быть в общей внешней сети с приложением. Укажите её имя в `CADDY_EXTERNAL_NETWORK`. Запустите Compose с `compose.edge.yaml`, чтобы приложение получило стабильный адрес `hamamton-app:8000` в этой сети. Добавьте отдельный сайт для своего домена в действующий Caddyfile и направьте `reverse_proxy` на этот адрес. Базу и бота во внешнюю сеть подключать не нужно.

```sh
docker compose -f compose.yaml -f compose.edge.yaml --profile bot --profile schedule up -d --build --wait
```

`db-ready` проверяет подключение к PostgreSQL при запуске и завершается с кодом 0. Готовность веба можно проверить запросом к `/api/health`. Для полной остановки передайте те же файлы Compose и профили.

```sh
docker compose -f compose.yaml -f compose.edge.yaml --profile bot --profile schedule down
```

## Расписание и бот

Для загрузки расписания заполните `GEMINI_API_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `CHANNEL_USERNAME` (числовой ID канала), `CHANNEL_TARGET` (имя канала) и `TARGET_GROUP`. Укажите авторизованную пользовательскую сессию через `TELEGRAM_SESSION_PATH`. Дайте доступ к файлу только владельцу или сервисному аккаунту. Профиль `schedule` подключает сессию только для чтения. Укажите `SCHEDULE_SOURCE=telegram`, чтобы веб показывал расписание из канала.

`GEMINI_MODELS` позволяет задать приоритет моделей через запятую. Оставьте строку пустой для встроенного порядка. Для OCR требуются входные изображения и структурированный текстовый вывод. Активные лимиты проекта видны в [Google AI Studio](https://aistudio.google.com/rate-limit).

```sh
docker compose --profile bot --profile schedule up -d --build --wait
```

`/today` показывает расписание на сегодня, `/nextday` — на следующий учебный день. `/start` включает уведомления и предлагает выбрать подгруппу. Подгруппу можно изменить командой `/subgroup`. Уведомления приходят за 15 минут до первого урока, ближайшей перемены или окончания занятий по прежней сетке звонков. Бот обслуживает команды расписания в личной переписке. Используйте один экземпляр long polling на токен.

В `/admin` главному админу доступны белый список, привязка Telegram ID к персонажам и назначение ролей. Админы могут менять судно и видимость персонажа. Скрытые персонажи остаются в списке управления. Главный админ привязывается через окружение.

Админы могут запросить повторный импорт через `/parse` или поиск следующего учебного дня через `/parsenext` в личном чате. Сервис `schedule-worker` проверяет историю при запуске, при новом фото и каждые 10 минут. Прочитанные даты и причины сбоя видны в `docker compose logs --tail=80 schedule-worker`.

Синхронизатор читает дату с печатного заголовка, ищет группу на всех листах поста и останавливается на старых листах. Сетка номеров, подгрупп и кабинетов перечитывается отдельно. При исчерпании квоты используется следующая модель Gemini. Временные сбои повторяются на том же этапе с задержкой. Если один лист не прочитан, синхронизатор проверяет остальные листы и сохраняет проверенные записи.

## Проверки

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-ocr.txt -r backend/requirements-bot.txt
.venv/bin/python -m unittest discover -s tests
npm --prefix web ci
npm --prefix web run build
```

Для локальной проверки одного снимка передайте ключ Gemini в окружении и выполните `.venv/bin/python -m backend.ocr schedule.jpg --group 99А`.

Результаты [аудита релиза](AUDIT.md) содержат сценарии проверки, замеры и границы теста.
