FROM python:3.12-slim-bookworm

# SEC: создаём non-root пользователя для рантайма приложения.
# Если в нашем коде когда-нибудь найдут RCE — атакующий не получит root внутри контейнера.
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

# Build-time зависимости (только для сборки нативных wheel-ов).
# Прячем под DEBIAN_FRONTEND, чтобы apt не задавал интерактивных вопросов в CI.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Зависимости отдельным слоем для эффективного кеша Docker.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Код приложения (только нужная часть).
COPY personal_ai_os ./personal_ai_os

# SEC: меняем владельца файлов на appuser перед переключением.
RUN chown -R appuser:appuser /app

USER appuser

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# uvicorn внутри контейнера слушает 0.0.0.0:8000, но порт пробрасывается
# на 127.0.0.1 в docker-compose — реально из интернета доступен только через Caddy.
CMD ["uvicorn", "personal_ai_os.bot.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
