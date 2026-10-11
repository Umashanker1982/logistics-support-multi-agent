FROM python:3.12-slim

# Non-root user: the container never needs root privileges.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
ENV PYTHONUNBUFFERED=1 \
    MEMORY_DIR=/data/sessions \
    LOG_DIR=/data/logs \
    APPROVALS_FILE=/data/approvals/queue.json
RUN mkdir -p /data && chown app:app /data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz')"
# Web demo by default. Hosts like Render inject $PORT. For the CLI: docker compose run --rm cli demo
CMD ["sh", "-c", "exec uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
