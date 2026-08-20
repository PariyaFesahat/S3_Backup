FROM hub.hamdocker.ir/python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

RUN mkdir -p /app/config

CMD ["python", "-u", "-m", "src.main"]