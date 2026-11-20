# Playwright's official image ships Chromium and its system deps, so the build only pulls and pip-installs.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WATCHTOWER_DATA=/data

WORKDIR /app
COPY pyproject.toml ./
COPY watchtower ./watchtower
RUN pip install --no-cache-dir . && python -m playwright install chromium

VOLUME /data
EXPOSE 8080
CMD ["uvicorn", "watchtower.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
