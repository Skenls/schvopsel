FROM node:24-alpine AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.14-slim AS backend-base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update -qq \
    && apt-get upgrade -y --no-install-recommends \
    && rm -f /var/lib/apt/lists/*_Packages* /var/lib/apt/lists/*_InRelease /var/lib/apt/lists/*_Release*
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

FROM backend-base AS schedule-worker
COPY backend/requirements-ocr.txt /tmp/requirements-ocr.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ocr.txt
RUN python -m pip uninstall --yes pip && rm -f /usr/local/lib/python3.14/ensurepip/_bundled/*.whl
COPY backend ./backend
USER 65534:65534
CMD ["python", "-m", "backend.schedule_sync"]

FROM backend-base AS telegram-bot
COPY backend/requirements-bot.txt /tmp/requirements-bot.txt
RUN pip install --no-cache-dir -r /tmp/requirements-bot.txt
RUN python -m pip uninstall --yes pip && rm -f /usr/local/lib/python3.14/ensurepip/_bundled/*.whl
COPY backend ./backend
USER 65534:65534
CMD ["python", "-m", "backend.bot"]

FROM postgres:18-alpine AS database
RUN apk upgrade --no-cache
# Use Alpine's privilege dropper for the PostgreSQL entrypoint.
RUN apk add --no-cache su-exec \
    && sed -i 's/exec gosu postgres/exec su-exec postgres/' /usr/local/bin/docker-entrypoint.sh \
    && grep -q 'exec su-exec postgres' /usr/local/bin/docker-entrypoint.sh \
    && rm -f /usr/local/bin/gosu

FROM caddy:2.11.9-alpine AS caddy-proxy
RUN apk upgrade --no-cache

FROM backend-base AS app
RUN python -m pip uninstall --yes pip && rm -f /usr/local/lib/python3.14/ensurepip/_bundled/*.whl
COPY backend ./backend
COPY --from=web /web/dist ./web/dist
USER 65534:65534
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
