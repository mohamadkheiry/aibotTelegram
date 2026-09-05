FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system bot \
    && useradd --system --gid bot --home-dir /app --shell /usr/sbin/nologin bot

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /app/requirements.txt

COPY --chown=bot:bot app /app/app
RUN install -d -o bot -g bot -m 0700 /app/data

USER bot:bot
VOLUME ["/app/data"]

CMD ["python", "-m", "app.main"]
