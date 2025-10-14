FROM python:3.13-slim

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Создаем пользователя для безопасности
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Устанавливаем uv
RUN pip install uv

# Создаем рабочую директорию
WORKDIR /app

# Копируем файлы зависимостей
COPY pyproject.toml uv.lock ./

# Устанавливаем зависимости
RUN uv sync

# Копируем код приложения
COPY . .

# Создаем директории для логов и сессий
RUN mkdir -p /app/logs /app/userbot/sessions /app/staticfiles && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app/logs /app/staticfiles

# Переключаемся на непривилегированного пользователя
USER appuser

# Переменные окружения
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=settings.prod
ENV UV_CACHE_DIR=/app/.uv-cache

# Проверяем здоровье контейнера
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/ || exit 1

# Открываем порт
EXPOSE 8000

# Команда по умолчанию
CMD ["uv", "run", "python", "manage.py", "runserver", "0.0.0.0:8000"]
