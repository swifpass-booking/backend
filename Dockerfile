# Swiftpass API (Django + gunicorn). Runs on any container host; set DATABASE_URL,
# DJANGO_SECRET_KEY, JWT_SECRET and DJANGO_ALLOWED_HOSTS (see .env.example).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# Static files are baked into the image; settings need *some* secrets to import.
RUN DJANGO_SECRET_KEY=build JWT_SECRET=build python manage.py collectstatic --noinput \
    && useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000
CMD ["bash", "start.sh"]
