FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

# copy contracts + shared so file:/// installs work
COPY contracts /contracts
COPY shared /shared

# copy ingestion service
COPY services/ingestion-service/pyproject.toml /app/pyproject.toml
COPY services/ingestion-service/app /app/app

RUN pip install --no-cache-dir -U pip \
 && pip install --no-cache-dir /contracts/python \
 && pip install --no-cache-dir /shared/python \
 && pip install --no-cache-dir .

EXPOSE 8001
CMD ["python", "-m", "app.main"]