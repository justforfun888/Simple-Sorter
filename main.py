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
MECAB_BIN = "mecab"
MECABRC = os.environ.get("MECABRC", r"C:\mecab\etc\mecabrc")
DEFAULT_DICDIR = r"C:\mecab\mecab-ko-dic"

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
    """mecab -D 출력에서 (dicdir, charset)을 추출 (실패 시 기본값)."""
    try:
        proc = subprocess.run(
            [MECAB_BIN, "-D", "-r", MECABRC],
            capture_output=True, text=True, encoding="utf-8", errors="ignore"
        )
        out = proc.stdout or ""
        dicdir = DEFAULT_DICDIR
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
        return DEFAULT_DICDIR, "utf-8"

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
    dicdir, charset = _mecab_info()
    args = [MECAB_BIN, "-O", "csv", "-r", MECABRC, "-d", dicdir]
    proc = _run(args, text, charset)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"csv-failed: code={proc.returncode}\n{proc.stderr}")
    return proc.stdout

def _mecab_run_tsv(text: str) -> str:
    """
    사용자 포맷(-F)으로 TSV 강제.
    컬럼: surface \t pos \t lemma
    """
    dicdir, charset = _mecab_info()
    fmt = "%m\t%f[0]\t%f[6]\n"   # 표면형, 품사, 원형
    args = [MECAB_BIN, "-r", MECABRC, "-d", dicdir, "-F", fmt, "-E", "EOS\n"]
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
            pos = (row[1] or "").strip() if len(row) > 1 else ""
            # lemma 후보: 8열(7) → 4열(3) → 표면형
            lemma = None
            for idx in (7, 3, 0):
                if len(row) > idx and row[idx] and row[idx] != "*":
                    lemma = row[idx].strip()
                    break
            if not lemma:
                lemma = surface
            # surface가 POS 코드(ETM 등)로만 찍힌 이상값 제거
            if surface == pos and pos.isupper() and 2 <= len(pos) <= 4:
                continue
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
            lemma = parts[2].strip() if len(parts) > 2 and parts[2].strip() != "*" else surface
            # surface가 POS 코드(ETM 등)로만 찍힌 이상값 제거
            if surface == pos and pos.isupper() and 2 <= len(pos) <= 4:
                continue
            tokens.append({"surface": surface, "pos": pos, "lemma": lemma})

    return tokens

# UI 요구: 명사 vs 동사·형용사 두 버킷
STOPWORDS = {
    "이다","하다","없다","있다","보다","되다","들다","자다","말다","오다","가다","주다","되어다"
}

def filter_and_bucket(tokens: List[Dict[str, str]], min_len: int = 2):
    """
    - 명사: POS startswith 'NN'
    - 동사/형용사: POS startswith 'VV' or 'VA'
    - 조사/어미/기호/접사 등은 제외
    """
    EXCLUDE_POS_PREFIX = ("J","E","X","S","F")
    EXCLUDE_EXACT = {"UNKNOWN"}

    nouns: List[str] = []
    v_adj: List[str] = []

    for t in tokens:
        lemma = (t["lemma"] or t["surface"]).strip()
        pos = t["pos"].strip()

        if not lemma or lemma == "*" or len(lemma) < min_len:
            continue
        if pos in EXCLUDE_EXACT or pos.startswith(EXCLUDE_POS_PREFIX):
            continue

        if lemma == pos and pos.isupper() and 2 <= len(pos) <= 4:
            continue
        if lemma in STOPWORDS:
            continue

        if pos.startswith("NN"):
            nouns.append(lemma)
        elif pos.startswith("VV") or pos.startswith("VA"):
            v_adj.append(lemma)

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
