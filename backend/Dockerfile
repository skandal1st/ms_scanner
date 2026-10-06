FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ARG APP_VERSION=unknown
ENV APP_VERSION=$APP_VERSION

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
