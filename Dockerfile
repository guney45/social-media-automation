# Everything the render pipeline needs baked in: ffmpeg, Chromium, fonts.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-noto-color-emoji \
      fonts-noto-core \
      fontconfig \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first so code changes do not invalidate the layer.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --no-dev

RUN uv run playwright install --with-deps chromium

COPY src ./src
COPY README.md ./
RUN uv sync --no-dev

# Bundled fonts are gitignored, so fetch them at build time.
RUN uv run smauto fetch-fonts && fc-cache -f

VOLUME ["/app/data"]

ENTRYPOINT ["uv", "run", "smauto"]
CMD ["doctor"]
