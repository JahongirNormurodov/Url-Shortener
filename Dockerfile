FROM python:3.12-slim

# Tizim paketlari
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# uv o'rnatish (tez Python paket menejeri)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Ish papkasi
WORKDIR /app

# Dependency fayllarni avval nusxalamiz (Docker layer keshlash uchun)
COPY pyproject.toml uv.lock ./

# Virtual environment yaratmasdan to'g'ridan-to'g'ri o'rnatish
ENV UV_SYSTEM_PYTHON=1
RUN uv sync --frozen --no-dev

# Ilovani nusxalaymiz
COPY . .

# Port
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Ishga tushirish
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
