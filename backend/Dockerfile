FROM python:3.14.7-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system routeops && adduser --system --ingroup routeops routeops

COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --requirement requirements.lock.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY data ./data

USER routeops

EXPOSE 8000

CMD ["uvicorn", "routeops.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
