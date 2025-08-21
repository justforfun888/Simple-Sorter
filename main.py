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

# --- [변경 1] MeCab 실행 파일 경로 자동 감지 (윈도우/리눅스 모두 대응) ---
# 우선 PATH에서 mecab을 찾고, 없으면 /usr/bin/mecab이 존재할 때만 사용합니다. 둘 다 없으면 None.
MECAB_BIN = shutil.which("mecab") or ("/usr/bin/mecab" if os.path.exists("/usr/bin/mecab") else None)

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
    if not MECAB_BIN:
        raise RuntimeError("MeCab 실행 파일을 찾을 수 없습니다. 서버에 MeCab이 설치되어 있는지 확인하세요.")
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
            # 일부 환경에서 EOS 라인이 포함될 수 있음
            if row[0].strip() == "EOS":
                continue
            
            surface = (row[0] or "").strip()
            # 다양한 사전 포맷을 고려한 lemma 추출: mecab-ko(7), IPADIC(6) 등
            lemma = ""
            for idx in (7, 6):
                if len(row) > idx and row[idx] and row[idx] != "*":
                    lemma = row[idx].strip()
                    break
            if not lemma:
                lemma = surface

            # POS는 일반적으로 1번째 칼럼이 상위 품사(coarse POS)
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
        pos = (t.get("pos", "") or "").strip()

        if not lemma or lemma == "*":
            continue
        if pos in EXCLUDE_EXACT or pos.startswith(EXCLUDE_POS_PREFIX):
            continue
        if lemma.isupper() and 2 <= len(lemma) <= 4:
            continue
        if lemma in STOPWORDS:
            continue

        # 배포 환경별 품사 태그 보정: 한국어(예: NNG/NNP, VV/VA) + 일반 한글 태그(명사/동사/형용사) + 일본어(名詞/動詞/形容詞)
        pos_lower = pos.lower()
        is_noun = (
            pos.startswith("NN")
            or ("명사" in pos)
            or ("名詞" in pos)
        )
        is_verb = (
            pos.startswith("VV")
            or ("동사" in pos)
            or ("動詞" in pos)
        )
        is_adj = (
            pos.startswith("VA")
            or ("형용사" in pos)
            or ("形容詞" in pos)
        )

        if is_noun:
            if len(lemma) >= min_len:
                nouns.append(lemma)
        elif is_verb or is_adj:
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

def _fallback_nouns(text: str, min_len: int = 2) -> List[str]:
    """MeCab 결과가 비거나 품사 매핑 실패 시, 한글만 기반으로 간단 추출.
    - 연속 한글(가-힣) min_len 이상을 단어로 간주
    - STOPWORDS 제거
    """
    # 한글 시퀀스 추출
    pattern = re.compile(rf"[가-힣]{{{min_len},}}")
    candidates = pattern.findall(text or "")
    results: List[str] = []
    for w in candidates:
        if w and w not in STOPWORDS:
            results.append(w)
    return results

# ------------------ API (이 부분은 수정 없음) ------------------

@app.post("/analyze")
def analyze_api(inp: TextIn):
    try:
        tokens = mecab_parse(inp.text)
        nouns, v_adj = filter_and_bucket(tokens, min_len=2)
        nouns_freq = freq_list(nouns, inp.min_freq)
        verbs_freq = freq_list(v_adj, inp.min_freq)

        # 폴백: 두 리스트가 모두 비면 간단 명사 추출로 최소 결과 제공
        if not nouns_freq and not verbs_freq:
            fb_nouns = _fallback_nouns(inp.text, min_len=2)
            nouns_freq = freq_list(fb_nouns, inp.min_freq)

        return {"nouns": nouns_freq, "verbs": verbs_freq}
    except Exception as e:
        # 프론트가 항상 JSON을 받도록 보장합니다.
        return JSONResponse(status_code=500, content={"error": str(e)})

# --- [변경 5] 괄호 오류를 수정한, 최종 웹페이지 서빙 코드 ---
app.mount("/", StaticFiles(directory=".", html=True), name="static")