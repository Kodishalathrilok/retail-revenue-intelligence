# API image for Fly.io. Slim rather than alpine: psycopg and statsmodels need
# manylinux wheels, and alpine's musl forces a slow source build of both.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency layer first so code changes do not reinstall the world.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --upgrade pip && pip install .

# Fly sets PORT; default matches the local convention.
ENV RRIP_API_HOST=0.0.0.0 \
    RRIP_API_PORT=8010
EXPOSE 8010

# uvicorn directly rather than `rrip serve`: the Windows event-loop workaround
# in rrip.api.run is a no-op on Linux, and this keeps the container honest
# about what it runs.
CMD ["uvicorn", "rrip.api.main:app", "--host", "0.0.0.0", "--port", "8010"]
