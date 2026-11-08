FROM python:3.12-slim

# unar عشان نفك ملفات rar
RUN apt-get update && apt-get install -y --no-install-recommends unar && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV DATA_DIR=/data
# عامل واحد بس، عشان الرفع بيشتغل في الخلفية جوه نفس العملية
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 300
