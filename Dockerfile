# 1. 파이썬 3.11이 설치된 가벼운 리눅스를 기본 환경으로 사용합니다.
FROM python:3.11-slim

# 2. 리눅스 시스템에 Mecab과 한국어 사전을 설치합니다.
RUN apt-get update && apt-get install -y mecab libmecab-dev mecab-ipadic-utf8

# 3. 컨테이너 안에 /app 이라는 작업 폴더를 만듭니다.
WORKDIR /app

# 4. requirements.txt 파일을 먼저 복사해서 파이썬 라이브러리를 설치합니다.
#    (이렇게 하면 나중에 코드만 바뀔 때 빌드 속도가 빨라집니다.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 현재 폴더의 모든 파일(main.py, index.html 등)을 컨테이너 안으로 복사합니다.
COPY . .

# 6. 서버를 시작하는 명령어를 지정합니다.
#    Render는 이 명령어를 보고 우리 프로그램을 실행합니다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
