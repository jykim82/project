FROM python:3.12-slim

WORKDIR /app

# 시스템 의존성 (psycopg2-binary는 빌드 불필요)
# build-essential / g++ — EPANET 모듈의 wntr 패키지 C++ 확장 빌드용 (Migration 0064)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드는 볼륨 마운트로 반영 (개발 모드)
# 프로덕션 시에는 COPY . . 사용

EXPOSE 8000

CMD ["uvicorn", "ai_server:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
