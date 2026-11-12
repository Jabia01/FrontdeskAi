# One image, two processes: the web service and the arq worker both run from it,
# with different commands. Keeping them identical means the worker can never be
# running different code from the webhook handler that queues its jobs.

FROM python:3.13-slim

# PYTHONUNBUFFERED is not optional here. Without it, Python block-buffers stdout
# when it is a pipe, which is exactly what a platform's log collector is - and
# the logs that matter most (a blocked send, a failed sweep, a login code in
# development) arrive minutes late or not at all.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# The project is installed editable and run from the source tree on purpose.
# A normal install would copy only the Python modules named in
# `[tool.setuptools] packages`, leaving `app/web/templates` and
# `app/web/static` behind - the console would start and then 500 on its first
# page. Editable keeps the files where `Path(__file__).parent` expects them.
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY seeds ./seeds
COPY alembic.ini ./
RUN pip install --no-cache-dir -e .

# No system timezone database in a slim image: `tzdata` comes from pyproject,
# and every slot calculation is timezone-aware.

# Run as nobody. Nothing is written to disk at runtime.
RUN useradd --create-home --uid 10001 frontdesk && chown -R frontdesk /app
USER frontdesk

EXPOSE 8000

# Overridden per service in render.yaml; this is the sensible local default.
# $PORT is what every container platform injects.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
