from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import subprocess
from typing import List, Dict, Tuple
import os
import re
import csv
from io import StringIO

# --- [변경 1] 윈도우 전용 경로 변수 모두 제거 ---
# Docker 컨테이너 안에 설치된 mecab의 절대 경로를 직접 지정합니다.
MECAB_BIN = "/usr/bin/mecab"
# Render(리눅스) 환경에서는 mecabrc 파일 경로를 지정할 필요가 없으므로,
# 관련 변수들을 모두 제거하여 윈도우 의존성을 없앱니다.

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

class TextIn(BaseModel):
    text: str
    min_freq: int = 5  # 프런트 기본값과 맞춤

# --- [변경 2] MeCab 환경 관련 함수들을 Docker에 맞게 아주 단순하게 변경 ---

def _mecab_info() -> Tuple[str, str]:
    """
    Render Docker 환경에서는 mecab -D로 정보를 확인할 필요 없이,
    기본값(utf-8)을 사용하도록 단순화합니다.
    """
    return None, "utf-8"

def _mecab_args_base() -> Tuple[List[str], str]:
    """
    Render Docker 환경에 맞게 mecab 실행 인자를 구성합니다.
    -d (사전 경로)나 -r (설정 파일) 없이 기본 mecab을 호출합니다.
    """
    dicdir, charset = _mecab_info()
    args = [MECAB_BIN]
    # Docker에 설치된 기본 사전을 사용하므로 -d, -r 옵션이 필요 없습니다.
    return args, charset

def _run(args: List[str], text: str, encoding: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        input=text,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors="replace",
    )

def _mecab_run_csv(text: str) -> str:
    """CSV 포맷(-O csv)으로 실행. 실패 시 예외."""
    # [변경 3] 복잡한 인자 생성 대신, 단순화된 _mecab_args_base 함수 사용
    args_base, charset = _mecab_args_base()
    proc = _run(args_base + ["-O", "csv"], text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"csv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout

def _mecab_run_tsv(text: str) -> str:
    """
    사용자 포맷(-F)으로 TSV 강제.
    컬럼: surface \t pos \t lemma
    """
    # [변경 4] 여기도 마찬가지로 단순화된 함수 사용
    args_base, charset = _mecab_args_base()
    fmt = "%m\t%f[0]\t%f[6]\n"   # 표면형, 품사, 원형
    proc = _run(args_base + ["-F", fmt, "-E", "EOS\n"], text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"tsv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout

# ------------------ 파싱/분류/집계 (이 부분은 수정 없음) ------------------

def mecab_parse(text: str) -> List[Dict[str, str]]:
    """
    1) -O csv 시도 → 2) 실패 시 -F(사용자 TSV) 폴백
    """
    raw = None
    used = None
    try:
        raw = _mecab_run_csv(text)
        used = "csv"
    except Exception:
        raw = _mecab_run_tsv(text)
        used = "tsv"

    tokens: List[Dict[str, str]] = []

    if used == "csv":
        reader = csv.reader(StringIO(raw))
        for row in reader:
            if not row:
                continue
            
            surface = (row[0] or "").strip()
            lemma = (row[7] or "").strip() if len(row) > 7 and row[7] != "*" else surface
            pos = (row[1] or "").strip() if len(row) > 1 else ""

            if not surface: continue

            tokens.append({"surface": surface, "pos": pos, "lemma": lemma})

    else:  # used == "tsv"
        for line in raw.splitlines():
            if not line or line == "EOS":
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            
            surface = parts[0].strip()
            pos = parts[1].strip()
            lemma = parts[2].strip() if len(parts) > 2 and parts[2].strip() != "*" else surface

            if not surface: continue

            tokens.append({"surface": surface, "pos": pos, "lemma": lemma})

    return tokens

STOPWORDS = {
    "이다","하다","없다","있다","보다","되다","들다","자다","말다","오다","가다","주다","되어다"
}

def filter_and_bucket(tokens: List[Dict[str, str]], min_len: int = 2):
    EXCLUDE_POS_PREFIX = ("J", "E", "X", "S", "F")
    EXCLUDE_EXACT = {"UNKNOWN"}

    nouns: List[str] = []
    v_adj: List[str] = []

    for t in tokens:
        lemma = (t.get("lemma") or t.get("surface", "")).strip()
        pos = t.get("pos", "").strip()

        if not lemma or lemma == "*":
            continue
        if pos in EXCLUDE_EXACT or pos.startswith(EXCLUDE_POS_PREFIX):
            continue
        if lemma.isupper() and 2 <= len(lemma) <= 4:
            continue
        if lemma in STOPWORDS:
            continue

        if pos.startswith("NN"):
            if len(lemma) >= min_len:
                nouns.append(lemma)
        elif pos.startswith("VV") or pos.startswith("VA"):
            basic_form = lemma + "다"
            if basic_form in STOPWORDS:
                continue
            v_adj.append(basic_form)

    return nouns, v_adj

def freq_list(words: List[str], min_count: int):
    counter: Dict[str, int] = {}
    for w in words:
        counter[w] = counter.get(w, 0) + 1
    return sorted(
        [(w, c) for w, c in counter.items() if c >= min_count],
        key=lambda x: x[1], reverse=True
    )

# ------------------ API (이 부분은 수정 없음) ------------------

@app.post("/analyze")
def analyze_api(inp: TextIn):
    tokens = mecab_parse(inp.text)
    nouns, v_adj = filter_and_bucket(tokens, min_len=2)
    return {
        "nouns": freq_list(nouns, inp.min_freq),
        "verbs": freq_list(v_adj, inp.min_freq) 
    }

# --- [변경 5] 괄호 오류를 수정한, 최종 웹페이지 서빙 코드 ---
app.mount("/", StaticFiles(directory=".", html=True), name="static")