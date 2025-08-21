from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import subprocess
from typing import List, Dict, Tuple
import os
import re
import csv
from io import StringIO

# ---------------------------------------------------------
# 전제:
# 1) MECABRC = C:\mecab\etc\mecabrc  (시스템 환경변수 권장)
# 2) mecabrc의 dicdir = C:\mecab\mecab-ko-dic
# 3) PATH에 C:\mecab\bin 포함 (mecab.exe 호출 가능)
# ---------------------------------------------------------
MECAB_BIN = os.environ.get("MECAB_BIN", "mecab")
# mecabrc는 실제 파일이 있을 때만 사용 (Windows만 쓸 수도, 서버는 비울 수도)
MECABRC = os.environ.get("MECABRC", "")
# 필요하면 사전 경로를 환경변수로 넘겨주고, 없으면 자동 감지에 맡김
DEFAULT_DICDIR = os.environ.get("MECAB_DICDIR", "")


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

def _mecab_info() -> Tuple[str, str]:
    """mecab -D 출력에서 (dicdir, charset) 추출. 실패해도 동작은 하게 기본값."""
    args = [MECAB_BIN, "-D"]
    if MECABRC and os.path.exists(MECABRC):
        args += ["-r", MECABRC]
    try:
        out = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="ignore"
        ).stdout or ""
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
        return dicdir, charset
    except Exception:
        return None, "utf-8"


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
    """CSV 포맷(-O csv) 실행. 실패 시 예외."""
    args_base, charset = _mecab_args_base()
    proc = _run(args_base + ["-O", "csv"], text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"csv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout

def _mecab_run_tsv(text: str) -> str:
    """
    사용자 포맷(-F)으로 TSV.
    컬럼: surface \t pos \t lemma
    """
    args_base, charset = _mecab_args_base()
    fmt = "%m\t%f[0]\t%f[6]\n"   # 표면형, 품사, 원형
    proc = _run(args_base + ["-F", fmt, "-E", "EOS\n"], text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"tsv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout


def _mecab_args_base() -> Tuple[List[str], str]:
    dicdir, charset = _mecab_info()
    args = [MECAB_BIN]
    # mecabrc가 실제로 있을 때만 -r 추가
    if MECABRC and os.path.exists(MECABRC):
        args += ["-r", MECABRC]
    # 사전 경로: -D로 감지된 dicdir 우선, 없으면 환경변수의 기본값 사용
    if dicdir and os.path.exists(dicdir):
        args += ["-d", dicdir]
    elif DEFAULT_DICDIR and os.path.exists(DEFAULT_DICDIR):
        args += ["-d", DEFAULT_DICDIR]
    return args, charset


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

# main.py 파일의 filter_and_bucket 함수를 아래 내용으로 교체해주세요.


def filter_and_bucket(tokens: List[Dict[str, str]], min_len: int = 2):
    """
    - 명사: POS startswith 'NN'
    - 동사/형용사: POS startswith 'VV' or 'VA'
    - 조사/어미/기호/접사 등은 제외
    """
    EXCLUDE_POS_PREFIX = ("J", "E", "X", "S", "F")
    EXCLUDE_EXACT = {"UNKNOWN"}

    nouns: List[str] = []
    v_adj: List[str] = []

    for t in tokens:
        # lemma가 비어있을 경우를 대비해 surface를 사용
        lemma = (t.get("lemma") or t.get("surface", "")).strip()
        pos = t.get("pos", "").strip()

        # 1. 기본적인 필터링 (단어가 없거나, 제외할 품사인 경우)
        if not lemma or lemma == "*":
            continue
        if pos in EXCLUDE_EXACT or pos.startswith(EXCLUDE_POS_PREFIX):
            continue
        
        # 2. 단어 자체가 품사 태그처럼 생긴 경우 제외 (NNB, JX 등)
        if lemma.isupper() and 2 <= len(lemma) <= 4:
            continue
        
        # 3. 불용어(stopwords) 처리
        if lemma in STOPWORDS:
            continue

        # 4. 품사별로 분류 및 최종 가공
        if pos.startswith("NN"):  # 명사 처리
            if len(lemma) >= min_len:
                nouns.append(lemma)
        
        elif pos.startswith("VV") or pos.startswith("VA"):  # 동사/형용사 처리
            # ##### 여기가 핵심! #####
            # 어간(lemma) 뒤에 '다'를 붙여 기본형으로 만들어줍니다.
            # 예: '하' -> '하다', '없' -> '없다'
            basic_form = lemma + "다"
            
            # 불용어 목록에 기본형이 있는지도 한번 더 확인합니다.
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

# ------------------ API ------------------

@app.post("/analyze")
def analyze_api(inp: TextIn):
    tokens = mecab_parse(inp.text)

    
    nouns, v_adj = filter_and_bucket(tokens, min_len=2)
    return {
        "nouns": freq_list(nouns, inp.min_freq),
        "verbs": freq_list(v_adj, inp.min_freq)  # 동사/형용사
    }
