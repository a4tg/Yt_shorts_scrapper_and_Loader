FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server.py server_core.py media_metadata.py ai_service.py observability.py database.py saas_models.py job_queue.py auth_service.py auth_routes.py admin_routes.py email_service.py billing_service.py billing_routes.py payment_service.py payment_routes.py yookassa_client.py manage_users.py workspace_service.py workspace_routes.py content_routes.py file_validation.py messaging_routes.py ./
COPY alembic.ini ./
COPY migrations ./migrations
COPY web ./web

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data

USER appuser
ENV YT_LOADER_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
