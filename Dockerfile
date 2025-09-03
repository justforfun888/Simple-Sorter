# 1. 파이썬 3.9이 설치된 가벼운 리눅스를 기본 환경으로 사용합니다.
FROM python:3.9-slim

# 2. Java 설치 (KoNLPy 의존성)
RUN apt-get update && \
    apt-get install -y openjdk-11-jdk && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 3. JAVA_HOME 환경변수 설정
ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64

# 4. 컨테이너 안에 /app 이라는 작업 폴더를 만듭니다.
WORKDIR /app

# 5. requirements.txt 파일을 먼저 복사해서 파이썬 라이브러리를 설치합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. 현재 폴더의 모든 파일(main.py, index.html 등)을 컨테이너 안으로 복사합니다.
COPY . .

# 7. [수정됨] Render가 지정하는 포트를 사용하도록 CMD를 변경합니다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "$PORT"]