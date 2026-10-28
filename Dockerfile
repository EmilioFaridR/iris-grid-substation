FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (pinned in pyproject.toml) for layer caching.
# shapely / pyproj / psycopg ship binary wheels: no system packages needed.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install ".[dev]"

COPY main.py alembic.ini ./
COPY alembic ./alembic
COPY sql ./sql
COPY tests ./tests

# Run as an unprivileged user; data/ and reports/ are mounted from the host.
RUN useradd --create-home --uid 10001 iris \
    && mkdir -p /app/data /app/reports \
    && chown -R iris:iris /app/data /app/reports
USER iris

ENTRYPOINT ["python", "main.py"]
CMD ["all"]
