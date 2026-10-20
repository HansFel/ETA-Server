FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data /backups /targets /app/staticfiles && useradd -u 10001 -m eta && chown -R eta:eta /data /backups /targets /app
COPY --chown=eta:eta static/ /app/staticfiles/
USER eta
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "--access-logfile", "/dev/null"]
