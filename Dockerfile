# syntax=docker/dockerfile:1.6   # для BuildKit и cache mounts

FROM python:3.13-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

COPY . .

ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

EXPOSE 8050

CMD ["uvicorn", "core.asgi:application", "--host", "0.0.0.0", "--port", "8050"]
