# TestHub application image.
# Python base image line is pinned to match research.md / plan.md.
FROM python:3.14.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings

# Use the same working directory for web and (later) worker processes.
WORKDIR /app

# Create a non-root runtime user and group before copying application code.
RUN addgroup --system app \
    && adduser --system --ingroup app --home /app app

# Install dependencies first so the layer is cached across source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

# Copy the project and hand ownership to the non-root runtime user.
COPY . .
RUN chown -R app:app /app

USER app

EXPOSE 8000

# Default command runs the development server; Compose may override it.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
