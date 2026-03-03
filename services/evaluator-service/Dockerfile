FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

COPY contracts /contracts
COPY shared /shared

COPY services/evaluator-service/pyproject.toml /app/pyproject.toml
COPY services/evaluator-service/app /app/app

RUN pip install --no-cache-dir -U pip \
 && pip install --no-cache-dir /contracts/python \
 && pip install --no-cache-dir /shared/python \
 && pip install --no-cache-dir .

EXPOSE 8006
CMD ["python", "-m", "app.main"]