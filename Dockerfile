FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home appuser && mkdir /data && chown appuser:appuser /data
COPY --chown=appuser:appuser . .
USER appuser
ENV MINI_DB=/data/mini.sqlite3 MINI_SECURE=1 PORT=10000 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 10000
CMD ["sh", "start.sh"]
