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


import shutil

MECAB_BIN = shutil.which("mecab") or "mecab"
MECABRC = os.environ.get("MECABRC")
ENV_DICDIR = os.environ.get("MECAB_DICDIR")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

class TextIn(BaseModel):
    text: str
    min_freq: int = 5  # 프런트 기본값과 맞춤

# ------------------ MeCab 환경/인코딩 ------------------

def _run(args: List[str], text: str, charset: str) -> subprocess.CompletedProcess:
    """UTF-8 환경에서 MeCab 실행"""
    try:
        # Windows에서 UTF-8 환경 변수 설정
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        
        return subprocess.run(
            args,
            input=text,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors="ignore",
            env=env  # 환경 변수 전달
        )
    except Exception as e:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr=str(e))

def _mecab_info() -> Tuple[str, str]:
    """mecab -D 출력에서 (dicdir, charset)을 추출 (실패 시 기본값)."""
    try:
        cmd = [MECAB_BIN, "-D"]
        if MECABRC:
            cmd += ["-r", MECABRC]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        out = proc.stdout or ""
        dicdir = None
        charset = "utf-8"

        m_dic = re.search(r"dicdir:\s*(.+)", out)
        if m_dic:
            dicdir = m_dic.group(1).strip()

        m_cs = re.search(r"charset:\s*([^\s]+)", out, re.IGNORECASE)
        if m_cs:
            cs = m_cs.group(1).strip().lower()
            if cs in ("utf-8", "utf8"):
                charset = "utf-8"
            elif cs in ("euc-kr", "cp949", "cp-949", "ks_c_5601-1987"):
                charset = "cp949"
            else:
                charset = "utf-8"
        return dicdir or "", charset
    except Exception:
        return "", "utf-8"

def _run(args: List[str], text: str, charset: str) -> subprocess.CompletedProcess:
    """
    MeCab 실행 헬퍼: 입력 텍스트를 지정 인코딩으로 전달하고 결과를 같은 인코딩으로 반환.
    예외는 삼켜서 호출부에서 returncode와 stderr로 판단 가능하게 함.
    """
    try:
        return subprocess.run(
            args,
            input=text,
            capture_output=True,
            text=True,
            encoding=charset,
            errors="ignore"
        )
    except Exception as e:
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr=str(e))

def _mecab_run_csv(text: str) -> str:
    """CSV 포맷(-O csv)으로 실행. 실패 시 예외."""
    detected_dicdir, charset = _mecab_info()
    dicdir = ENV_DICDIR or (detected_dicdir if detected_dicdir else None)

    args = [MECAB_BIN, "-O", "csv"]
    if MECABRC:
        args += ["-r", MECABRC]
    if dicdir:
        args += ["-d", dicdir]

    proc = _run(args, text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"csv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout

def _mecab_run_tsv(text: str) -> str:
    """
    사용자 포맷(-F)으로 TSV 강제.
    컬럼: surface \t pos \t lemma
    """
    detected_dicdir, charset = _mecab_info()
    dicdir = ENV_DICDIR or (detected_dicdir if detected_dicdir else None)

    fmt = "%m\t%f[0]\t%f[6]\n"   # 표면형, 품사, 원형
    args = [MECAB_BIN, "-F", fmt, "-E", "EOS\n"]
    if MECABRC:
        args += ["-r", MECABRC]
    if dicdir:
        args += ["-d", dicdir]

    proc = _run(args, text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"tsv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout

# ------------------ 파싱/분류/집계 ------------------

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
            # CSV 출력에서 원형(lemma)은 8번째 열(인덱스 7)에 있습니다.
            # 없을 경우 표면형(surface)을 대신 사용합니다.
            lemma = (row[7] or "").strip() if len(row) > 7 and row[7] != "*" else surface
            pos = (row[1] or "").strip() if len(row) > 1 else ""

            if not surface: continue

            tokens.append({"surface": surface, "pos": pos, "lemma": lemma})

    else:  # used == "tsv"
        # 한 줄: surface \t pos \t lemma
        for line in raw.splitlines():
            if not line or line == "EOS":
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            
            surface = parts[0].strip()
            pos = parts[1].strip()
            # TSV 출력에서 원형(lemma)은 3번째 부분(인덱스 2)에 있습니다.
            # 없을 경우 표면형(surface)을 대신 사용합니다.
            lemma = parts[2].strip() if len(parts) > 2 and parts[2].strip() != "*" else surface

            if not surface: continue

            tokens.append({"surface": surface, "pos": pos, "lemma": lemma})

    return tokens

# UI 요구: 명사 vs 동사·형용사 두 버킷
STOPWORDS = {
    "이다","하다","없다","있다","보다","되다","들다","자다","말다","오다","가다","주다","되어다"
}




def filter_and_bucket(tokens: List[Dict[str, str]], min_len: int = 2):
    """
    - KO(MeCab-ko) 전용:
      - 명사: NN* 및 NP/NR (+ SL/SH는 명사 취급)
      - 동사/형용사: VV*/VA* 만 포함 (VX/VCP/VCN 제외)
    - 조사/어미/기호/접사 등 제외
    - 태그가 인식되지 않으면 제외 (fallback 로직 제거)
    """
    EXCLUDE_POS_PREFIX = ("J", "E", "SF", "SP", "SS", "SE", "SO", "SW", "X")
    EXCLUDE_EXACT = {"UNKNOWN"}

    nouns: List[str] = []
    v_adj: List[str] = []

    for t in tokens:
        lemma = (t.get("lemma") or t.get("surface", "")).strip()
        pos = (t.get("pos") or "").strip()
        if not lemma or lemma == "*":
            continue

        pos_u = pos.upper()

        # 1) 공통 제외
        if pos_u in EXCLUDE_EXACT or pos_u.startswith(EXCLUDE_POS_PREFIX):
            continue

        # 2) 불용어
        if lemma in STOPWORDS:
            continue

        # 3) 분류 - 명확한 태그만 처리
        is_noun = pos_u.startswith("NN") or pos_u in {"NP", "NR", "SL", "SH"}
        is_v = pos_u.startswith("VV")
        is_a = pos_u.startswith("VA")

        if is_noun:
            if len(lemma) >= min_len:
                nouns.append(lemma)
            continue

        if is_v or is_a:
            # 한국어 기본형 보정: '다' 붙이기
            basic_form = lemma
            if re.search(r"[가-힣]", lemma) and not lemma.endswith("다"):
                basic_form = lemma + "다"
            if basic_form in STOPWORDS:
                continue
            if len(basic_form) >= min_len:  # 길이 체크 추가
                v_adj.append(basic_form)
            continue


    return nouns, v_adj

def freq_list(words: List[str], min_count: int):
    counter: Dict[str, int] = {}
    for w in words:
        counter[w] = counter.get(w, 0) + 1
    return sorted(
        [(w, c) for w, c in counter.items() if c >= min_count],
        key=lambda x: x[1], reverse=True
    )

# ------------------ API ------------------

@app.post("/analyze")
def analyze_api(inp: TextIn):

    tokens = mecab_parse(inp.text)

    print("--- [단계 1] Mecab이 분석한 원본 데이터 ---")
    print(tokens)


    # 2. 분석된 결과에서 필요한 단어만 골라냅니다.
    nouns, v_adj = filter_and_bucket(tokens, min_len=2)


    print("--- [단계 2] 필터링 후 살아남은 단어 목록 ---")
    print("명사 목록:", nouns)
    print("동사/형용사 목록:", v_adj)



    return {
        "nouns": freq_list(nouns, inp.min_freq),
        "verbs": freq_list(v_adj, inp.min_freq) 
    }


app.mount("/", StaticFiles(directory=".", html=True), name="static")

