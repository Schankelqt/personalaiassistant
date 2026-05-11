# Personal AI OS — пошаговый запуск

Этот гайд описывает итеративный путь: сначала запустить бота локально/на VPS, протестировать онбординг и работу агентов на одном пользователе (на себе), и только потом добавлять платёжный шлюз, OAuth-провайдеров и открывать доступ другим.

Сценарий: **Telegram + Claude API + Supabase + Redis на одном VPS.**

---

## Этап 0. Что должно быть у тебя на руках

Перед стартом подготовь четыре аккаунта/доступа. Время на каждый — 5–15 минут.

| Что | Где взять | Зачем |
|---|---|---|
| **Telegram bot token** | Чат с [@BotFather](https://t.me/BotFather), команда `/newbot` | Без него нет бота |
| **Anthropic API key** | [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key | Без него агенты не отвечают |
| **Supabase project** | [supabase.com](https://supabase.com) → New project (Free tier хватает) | Postgres для данных пользователей |
| **VPS с публичным IP и доменом** | Hetzner CCX13 (€13.5/мес) + Cloudflare DNS (бесплатно) | Telegram требует HTTPS webhook |

Минимальный набор инструментов на VPS: Docker, docker-compose, git, openssl. Всё остальное — внутри контейнеров.

---

## Этап 1. Завести Supabase

1. Создай проект на supabase.com (регион — ближайший к VPS, например `eu-central-1` для Hetzner FSN).
2. Дождись провижининга (~2 минуты).
3. Зайди в **SQL Editor → New query** и выполни весь файл `migrations/001_schema.sql` из репозитория (открой локально, скопируй содержимое, вставь, нажми Run). Должно появиться сообщение «Success. No rows returned».
4. Зайди в **Settings → Database → Connection string** и выбери режим **Transaction pooler**. Скопируй строку — это твой `DATABASE_URL`. Подставь пароль из этого же экрана.

> Важно: используй именно **pooler** (порт 6543), не direct (5432). Пулер выживает в serverless и нужен для нашего asyncpg-пула.

Проверка: в **Database → Tables** должны появиться `users`, `agents`, `memory_entries`, `reminders`, `oauth_tokens`, `token_logs`, `billing_events`, `interaction_history`, `message_feedback`.

---

## Этап 2. Поднять VPS

Я предполагаю Hetzner CCX13 / Ubuntu 24.04 LTS. Если у тебя другой — адаптируй.

### 2.1 Начальная настройка

```bash
# Подключение
ssh root@<твой-IP>

# Создать обычного пользователя для работы
adduser deploy
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

# Базовая защита: только SSH/HTTP/HTTPS снаружи; всё остальное блокировано.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

# Отключаем root login по SSH
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl reload ssh
```

С этого момента подключаемся как `deploy`.

### 2.2 Установить Docker

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
# Выйти и зайти заново, чтобы группа docker применилась.
exit
```

### 2.3 Привязать домен

В Cloudflare (или другом DNS-провайдере) сделай **A-запись**:

```
aios.example.com  →  <IP твоего VPS>
Proxy: DNS only (серое облако), не proxied
```

Серый режим важен — Cloudflare proxy для webhook'ов Telegram создаёт неочевидные проблемы с TLS. Включишь оранжевое облако позже.

---

## Этап 3. Развернуть приложение

### 3.1 Скопировать код

```bash
ssh deploy@<твой-IP>
mkdir -p ~/apps && cd ~/apps
# Если код в Git:
git clone <твой-репозиторий> personal_ai_os
# Или скопировать через scp с локальной машины:
# scp -r personal_ai_os deploy@<IP>:~/apps/

cd personal_ai_os
```

### 3.2 Создать `.env`

```bash
cp .env.minimal.example .env
nano .env  # заполнить значения
```

Минимальные обязательные поля:

```bash
APP_BASE_URL=https://aios.example.com
DATABASE_URL=<строка из Supabase>
REDIS_URL=redis://redis:6379/0
TELEGRAM_BOT_TOKEN=<токен от BotFather>
TELEGRAM_WEBHOOK_SECRET=<вывод команды openssl rand -hex 32>
ANTHROPIC_API_KEY=<ключ из console.anthropic.com>
OAUTH_ENCRYPTION_KEY=<вывод команды openssl rand -hex 32>
```

> **OAUTH_ENCRYPTION_KEY обязателен**, даже если не используешь OAuth. Приложение валидирует длину при старте. Сгенерируй один раз и сохрани — потом потеря этого ключа потребует переавторизации всех пользователей.

Полезные команды для генерации секретов:

```bash
echo "TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)"
echo "OAUTH_ENCRYPTION_KEY=$(openssl rand -hex 32)"
```

### 3.3 Запустить TLS-прокси (Caddy)

Telegram требует HTTPS для webhook. Caddy сам получает сертификат Let's Encrypt и проксирует трафик на бот.

Создай файл `~/apps/Caddyfile`:

```
aios.example.com {
    reverse_proxy 127.0.0.1:8000

    encode gzip

    # SEC: базовые security headers.
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    # SEC: лимит body size на уровне reverse proxy.
    request_body {
        max_size 2MB
    }

    # SEC: блокируем сторонние пути (на нашем домене должны жить только webhook и oauth callbacks).
    @badpaths {
        not path /webhook /webhooks/* /oauth/callback/* /health /health/full
    }
    respond @badpaths 404
}
```

Запусти Caddy (host network, чтобы достучаться до `127.0.0.1:8000` бота):

```bash
docker run -d --name caddy --restart unless-stopped \
  --network host \
  -v ~/apps/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data \
  -v caddy_config:/config \
  caddy:2
```

> Использование `--network host` оправдано: Caddy должен слушать порты 80/443 хоста и связываться с ботом на 127.0.0.1:8000 без выхода в публичную сеть. UFW при этом продолжает блокировать всё кроме 22/80/443.

Через 30–60 секунд проверь:

```bash
curl https://aios.example.com/health
# Должно вернуть: {"status":"ok"} — но 502, потому что бот ещё не запущен. Это нормально.
```

### 3.4 Запустить docker-compose

```bash
cd ~/apps/personal_ai_os
docker compose up -d --build
```

Проверь статус контейнеров:

```bash
docker compose ps
# Должны быть Up: redis, bot, worker, beat
docker compose logs -f bot --tail 50
# Не должно быть Traceback. Должна появиться строка "Application startup complete".
```

Полная проверка зависимостей:

```bash
curl https://aios.example.com/health/full
# {"status":"ok","postgres":"ok","redis":"ok"}
```

Если что-то `degraded` — смотри `docker compose logs bot`.

### 3.5 Привязать webhook

Когда `health/full` зелёный — регистрируем webhook у Telegram. Один раз:

```bash
TOKEN="<твой TELEGRAM_BOT_TOKEN>"
SECRET="<твой TELEGRAM_WEBHOOK_SECRET>"
URL="https://aios.example.com/webhook"

curl -X POST "https://api.telegram.org/bot${TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"${URL}\",\"secret_token\":\"${SECRET}\",\"allowed_updates\":[\"message\",\"callback_query\"],\"drop_pending_updates\":true}"

# Проверка установки:
curl "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
# В ответе должно быть "url":"https://aios.example.com/webhook" и "pending_update_count":0
```

---

## Этап 4. Прогнать «сам на себе»

Открой Telegram, найди своего бота по username, напиши `/start`. Ожидаемое поведение:

| Шаг | Что вводишь | Что должен ответить бот |
|---|---|---|
| 1 | `/start` | Приветствие + первый вопрос онбординга «Как тебя зовут?» |
| 2 | «Артём» | Вопрос «Чем ты занимаешься?» |
| 3 | «Продакт-менеджер» | Вопрос про инструменты |
| 4 | «Jira, Notion, Google Calendar» | Вопрос про задачи для автоматизации |
| 5 | «статусы Jira и расписание встреч» | Вопрос про ДР |
| 6 | «Иван — 15.03, Маша — 22.06» или «пропустить» | «Онбординг завершён. Созданы агенты: Engineer, Память, Работа» |
| 7 | `/agents` | Список с UUID каждого агента |
| 8 | `/status` | Тариф free, токены X из 50000, активных агентов 3 |
| 9 | «запомни Петю с ДР 10 апреля как коллегу» | Memory-агент создаёт запись |
| 10 | `/people` | Видишь Петю (и Ивана/Машу если ввёл) |
| 11 | «создай агента для написания постов \| Пиши черновики коротких постов в стиле Сергея Капустина» | Engineer создаёт нового агента |
| 12 | «напиши пост про продуктивность» | Кастомный агент отвечает |

Если какой-то шаг сломался — `docker compose logs -f bot` покажет stack trace.

### Что протестировать дополнительно

```
/help               — справка по командам
/setup              — повторный онбординг (агенты сохранятся)
/history            — последние 20 взаимодействий
/settings           — текущие настройки
/settings tz=Europe/Moscow remind=10 meeting=10
                    — изменить часовой пояс и параметры напоминаний
/forget Иван        — удалить человека из памяти
/export_my_data     — выгрузка профиля как JSON
/agent_toggle <id> off  — отключить агента
/referral           — твоя реферальная ссылка
```

### Что НЕ заработает на этом этапе (и это нормально)

- `/upgrade` — выдаст пустой список, пока не заполнены `CHECKOUT_URL_*` (Paddle настраивается позже).
- «подключи Google Calendar» — Engineer ответит «Google OAuth не настроен администратором», пока нет `GOOGLE_CLIENT_ID`.
- «подключи Jira» — то же самое.
- Birthday reminders в 09:00 локального времени — заработают, но нужно проверить с прошедшим временем (создать человека с ДР через 7 или 1 день).

---

## Этап 5. Базовая операционка

### 5.1 Просмотр логов

```bash
docker compose logs -f bot       # webhook handler, FastAPI
docker compose logs -f worker    # выполнение Celery tasks
docker compose logs -f beat      # расписание тасков
docker compose logs -f redis
docker logs -f caddy             # TLS и реверс-прокси
```

### 5.2 Перезапуск после изменений

```bash
git pull
docker compose up -d --build
# Или для пересборки только bot без даунтайма worker/beat:
docker compose up -d --build --no-deps bot
```

### 5.3 Бэкап Supabase

Supabase в Free tier делает автоматические бэкапы 1 раз в день, хранит 7 дней. Для прода (Pro tier) — Point-in-Time Recovery до 7 дней.

Дополнительно — раз в неделю руками:

```bash
# Supabase → Database → Backups → Generate manual backup
# Или через CLI: supabase db dump --db-url $DATABASE_URL > backup-$(date +%F).sql
```

### 5.4 Мониторинг (минимум)

Бесплатно:

- **UptimeRobot** → добавь монитор на `https://aios.example.com/health` (5 мин interval). Если упадёт — придёт письмо.
- **Sentry** (опционально, после первой неделя) → добавь `SENTRY_DSN` в `.env` и перезапусти. Поймает все Python-исключения.

---

## Этап 6. Что добавлять дальше (когда базовый MVP работает)

Порядок добавления — от меньшего риска к большему.

### Неделя 1: фаза «сам на себе»

1. Прогнать все команды, найти UX-шероховатости.
2. Завести 5–10 человек в Memory с реальными ДР, проверить что reminders прилетают.
3. Зафиксировать чек-лист NPS вопросов для будущих бета-тестеров.

### Неделя 2: подключить Google Calendar

1. Google Cloud Console → New project «Personal AI OS».
2. APIs & Services → OAuth consent screen → External, Testing, добавить свой email в test users.
3. Credentials → Create OAuth client ID → Web application.
4. Authorized redirect URI: `https://aios.example.com/oauth/callback/google`.
5. Скопировать Client ID/Secret в `.env`, добавить scope `.../auth/calendar`.
6. `docker compose up -d --build`.
7. В Telegram: «подключи Google Calendar» → пройти OAuth → проверить что Work-агент видит события.

### Неделя 3: подключить Jira

1. [Atlassian Developer Console](https://developer.atlassian.com/console/myapps/) → Create → OAuth 2.0 integration.
2. Permissions: `read:jira-user read:jira-work write:jira-work offline_access`.
3. Callback URL: `https://aios.example.com/oauth/callback/jira`.
4. Client ID/Secret в `.env`.
5. В Telegram: «подключи Jira» → авторизация → «покажи мои задачи».

### Неделя 4–5: пригласить 3–5 человек беты

1. Никаких изменений в коде — просто дать им @username бота.
2. Слушать обратную связь, фиксировать в `/feedback`.
3. Параллельно: подать заявку на Google OAuth verification (на случай если соберёшь >100 юзеров).

### Неделя 6+: биллинг

1. Регистрация Paddle (KYC процесс — 3–7 дней).
2. Создать продукты Personal/Pro/Business и пакеты S/M/L → получить Price IDs.
3. Webhook URL: `https://aios.example.com/webhooks/paddle`.
4. Заполнить `PADDLE_*` и `CHECKOUT_URL_*` в `.env`.
5. `docker compose up -d --build`.
6. Тестовая транзакция в sandbox-режиме Paddle.

---

## Безопасность — обязательный чек-лист

Прежде чем открывать бота даже на себя, пройди этот чек-лист. Это не paranoid, это минимум для интернет-доступного сервиса.

### Перед первым `git commit`

- [ ] В корне проекта есть `.gitignore` (в этом репо уже есть — см. `agent/.gitignore`).
- [ ] `git status` НЕ показывает `.env`, `.venv/`, `__pycache__`, `.DS_Store` среди untracked.
- [ ] `git ls-files | grep -E '\.env$'` — должен быть пустой результат.
- [ ] Если ранее закоммитил `.env` с реальными значениями — **немедленно отзови все токены** (Telegram BotFather → /revoke, Anthropic console → revoke key, Supabase → reset DB password), затем `git filter-repo` для очистки истории.

### Перед деплоем на VPS

- [ ] `TELEGRAM_WEBHOOK_SECRET` и `OAUTH_ENCRYPTION_KEY` сгенерированы через `openssl rand -hex 32` (не «12345» и не имя кота).
- [ ] `OAUTH_ENCRYPTION_KEY` записан в **парольном менеджере** — его потеря = переавторизация всех пользователей.
- [ ] `DATABASE_URL` указывает на pooler (порт 6543), а не direct (5432).
- [ ] UFW настроен: разрешены только 22/80/443.
- [ ] Root login по SSH отключён, работа только через `deploy`-пользователя по ключу.
- [ ] Контейнеры запущены через `docker compose up -d` (не вручную с `--network host` где не нужно).

### После деплоя

- [ ] `curl https://aios.example.com/health` → 200 OK
- [ ] `curl http://<IP>:8000/health` (по публичному IP без TLS) → **timeout или connection refused**. Если возвращает 200 — значит docker bypass'нул UFW, фикси `ports: "127.0.0.1:8000:8000"` в docker-compose.
- [ ] `curl http://<IP>:6379` (по публичному IP) → connection refused. Если Redis открыт — это критично, удали `ports:` у redis в docker-compose.
- [ ] `curl -X POST https://aios.example.com/webhook -d '{}'` → 403 (нет secret-token). Если 200 — значит проверка не работает.
- [ ] `curl -X POST https://aios.example.com/webhooks/paddle -d '{}'` → 503 (без `PADDLE_WEBHOOK_SECRET`) или 403. Если 200 — фикси.
- [ ] `docker exec -it $(docker compose ps -q bot) whoami` → возвращает `appuser`, не `root`. Если `root` — пересобери образ (Dockerfile уже содержит non-root user).
- [ ] В Caddyfile настроены security headers (HSTS, X-Content-Type-Options).
- [ ] Webhook привязан с тем же секретом что в `.env`:
      `curl "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"` → поле `url` совпадает, `last_error_date` отсутствует.

### Каждую неделю

- [ ] Просмотреть Caddy access log на подозрительные POST к нашим endpoint'ам:
      `docker logs caddy 2>&1 | grep -E "POST /webhook|POST /webhooks/paddle" | tail -50`
- [ ] Просмотреть Supabase → Database → Logs на необычные запросы.
- [ ] Проверить расход Anthropic API: `console.anthropic.com` → Usage. Резкий скачок = либо вирусный рост (хорошо), либо взлом ключа (плохо).
- [ ] Бэкап Supabase (см. этап 5.3 выше).

### Раз в квартал

- [ ] Ротация `TELEGRAM_BOT_TOKEN`: BotFather → `/revoke` старый → новый в `.env` → `docker compose up -d`. Параллельно — `setWebhook` с новым токеном.
- [ ] Ротация `ANTHROPIC_API_KEY`: console.anthropic.com → Create new key → новый в `.env` → старый revoke.
- [ ] `pip-audit -r requirements.txt` — проверка зависимостей на новые CVE.
- [ ] Обновление базового образа `python:3.12-slim-bookworm` (`docker compose build --pull`).

### Что НЕ нужно делать

- ❌ Не оставляй `PADDLE_WEBHOOK_SECRET` пустым на проде. Текущий код отдаёт 503, но в будущем при включении биллинга это критично.
- ❌ Не запускай `python -m uvicorn` напрямую от root на хосте — только через docker.
- ❌ Не выставляй порт PostgreSQL/Redis в публичную сеть для удобства отладки. Используй SSH tunnel: `ssh -L 5432:db.supabase.co:5432 deploy@vps`.
- ❌ Не передавай `OAUTH_ENCRYPTION_KEY` в логи, чаты, скриншоты. Один пишешь — забываешь куда положил.
- ❌ Не игнорируй `last_error_message` в `getWebhookInfo`. Это или DNS, или TLS, или secret mismatch — всё это видимые проблемы.

---

## Чек-лист «всё работает» (после первого деплоя)

- [ ] `https://aios.example.com/health/full` → `postgres: ok, redis: ok`
- [ ] `getWebhookInfo` показывает мой URL и `pending_update_count = 0`
- [ ] `/start` → онбординг проходит до конца за ≤ 5 минут
- [ ] После онбординга в Supabase в таблице `users` есть моя запись с `onboarding_complete=true`
- [ ] В таблице `agents` 3 строки с `agent_type` = engineer / memory / work
- [ ] `docker compose ps` — все 4 сервиса Up
- [ ] `docker compose logs beat` — задачи запускаются по расписанию (`birthday_reminders` каждый час, `reset_daily_tokens` ночью)
- [ ] Sentry/UptimeRobot настроены (или явно отложены)
- [ ] Создан секрет для бэкапа `OAUTH_ENCRYPTION_KEY` (записать в безопасное место — потеря = переавторизация всех)

---

## Типичные проблемы и решения

| Симптом | Причина | Решение |
|---|---|---|
| `ValueError: OAUTH_ENCRYPTION_KEY must be 64 hex chars` при старте | Пустой или неправильный ключ | `openssl rand -hex 32` → в `.env` |
| `health/full` показывает `postgres: error` | Неверный `DATABASE_URL` или Supabase спит (Free tier) | Открой проект в Supabase dashboard, чтобы разбудить. Проверь pooler-строку |
| `getWebhookInfo` показывает `last_error_message: "Wrong response from the webhook"` | Caddy ещё не получил сертификат / DNS не обновился | Подожди 2–5 минут, проверь `dig aios.example.com`, `docker logs caddy` |
| Бот не отвечает после `/start` | webhook возвращает 403 (неверный `TELEGRAM_WEBHOOK_SECRET`) | Сверь значение в `.env` и в `setWebhook` запросе. Они должны совпадать байт-в-байт |
| Engineer-агент висит долго | Anthropic API недоступен или ключ невалиден | `docker compose logs bot \| grep anthropic` — увидишь ошибку |
| Beat не запускает таски | Redis недоступен | `docker compose logs redis`, `docker compose restart beat` |
| «дневной лимит токенов исчерпан» уже на старте | Тестируешь много, free лимит 50k быстро кончается | Либо подожди до 00:05 UTC (cron-task сбросит), либо вручную: SQL `UPDATE users SET daily_tokens_used = 0` в Supabase |

---

## Что НЕ нужно делать на старте

- Не открывай бот публично — пока что в `.env` нет защиты от «холодных» пользователей. После онбординга они начнут тратить твой Anthropic-баланс.
- Не вкладывайся в Sentry/Mixpanel/PostHog — UptimeRobot + `/health/full` достаточно для одного юзера.
- Не настраивай Paddle до того как 5–10 человек подтвердят NPS ≥ 7.
- Не запускай Telegram Mini App / голос / multi-channel — это Phase 4 по нашему roadmap.

---

## Полезные команды для итерации

```bash
# Очистить свою сессию (если онбординг застрял)
docker exec -it $(docker compose ps -q redis) redis-cli FLUSHDB

# Полностью удалить себя из БД (чтобы пройти онбординг заново)
# Через Supabase SQL Editor:
# DELETE FROM users WHERE telegram_id = <твой telegram id>;

# Проверить расход токенов за сегодня
# В Supabase SQL Editor:
# SELECT model, SUM(total_tokens), COUNT(*) FROM token_logs WHERE created_at::date = CURRENT_DATE GROUP BY 1;

# Посмотреть последние ошибки Claude API
docker compose logs bot --since 1h | grep -i "anthropic\|error"

# Принудительно сменить тариф себе для тестов (например, на pro)
# UPDATE users SET plan='pro', daily_token_limit=1000000 WHERE telegram_id=<твой id>;
```

---

## Что дальше

Когда базовая итерация на себе пройдена и бот стабильно работает 3–5 дней — можно:

1. Пригласить 3–5 человек из тёплого круга на ручной онбординг.
2. Параллельно начать smoke-test landing page для холодного трафика.
3. После 10 NPS ≥ 7 — открыть закрытую бету и подключать Paddle.

Документация по дальнейшим этапам — в `agent/Документация/`.
