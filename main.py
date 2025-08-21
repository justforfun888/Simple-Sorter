from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import List, Dict
import re
from konlpy.tag import Okt

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

okt = Okt()

class TextIn(BaseModel):
    text: str
    min_freq: int = 5


# --- [1] 동사/형용사 불용어 목록 ---
# 문법적인 기능만 하거나, 너무 자주 등장하여 분석에 방해가 될 수 있는 단어들
STOPWORDS = {
    # 핵심 기능 단어
    "이다", "아니다", "있다", "없다", "같다", "되다", "않다", "말다", "싶다",
    "지다", "였다", "그렇다", "이렇다", "돼다", 

    # 기본적인 움직임 동사
    "하다", "보다", "오다", "가다", "주다", "들다", "자다"
}

# --- [2] 명사 불용어 목록 ---
# 문법적으로는 명사이지만, 글의 핵심 내용과 거리가 먼 단어들
NOUN_STOPWORDS = {
    # 대명사
    "거기", "여기", "저기", "이것", "그것", "저것", "무엇", "누구", "지금",

    # 의존 명사 및 단위/시간 명사
    "때문", "하나", "오늘", "어제", "내일", "정도", "가지", "경우", "동안",
    "수가", "건가", 
    
    # 기타 일반 명사/부사성 명사
    "사실", "조금", "무슨", "보고"
}

def analyze_with_okt(text: str) -> List[Dict[str, str]]:

    okt_result = okt.pos(text, norm=True, stem=True)
    
    tokens = []
    i = 0
    while i < len(okt_result):
        word, pos = okt_result[i]

        if pos == 'Noun' and (i + 1) < len(okt_result):
            next_word, next_pos = okt_result[i+1]
            if next_word == '하다' and (next_pos == 'Verb' or next_pos == 'Adjective'):
                # 두 단어를 합쳐서 하나의 동사/형용사로 만듭니다.
                combined_word = word + next_word
                tokens.append({"lemma": combined_word, "pos": next_pos})
                # '하다' 부분은 이미 처리했으므로 건너뜁니다.
                i += 2
                continue

        # 위의 특수 패턴에 해당하지 않는 일반적인 단어들을 추가합니다.
        tokens.append({"lemma": word, "pos": pos})
        i += 1
            
    return tokens

def filter_and_bucket_okt(tokens: List[Dict[str, str]], min_len: int = 2):
    """KoNLPy 태그에 맞춘 필터링"""
    nouns = []
    verbs = []
    
    for t in tokens:
        lemma = t.get("lemma", "").strip()
        pos = t.get("pos", "").strip()
        
        # --- [변경 없음] 공통 필터링 규칙 ---
        if lemma in STOPWORDS or lemma in NOUN_STOPWORDS:
            continue
        if not re.search(r"[가-힣]", lemma):
            continue
        
        # --- KoNLPy 태그 기준 분류 ---
        if pos == 'Noun':
            # 명사는 기존 규칙(min_len, 기본값 2)을 그대로 사용합니다.
            if len(lemma) >= min_len:
                nouns.append(lemma)
                
        elif pos == 'Verb' or pos == 'Adjective':
            # 3글자 미만인 동사/형용사는 모두 제외합니다.
            if len(lemma) < 3:
                continue
            
            verbs.append(lemma)
    
    return nouns, verbs

def freq_list(words: List[str], min_count: int):
    """단어 빈도 계산"""
    counter: Dict[str, int] = {}
    for w in words:
        counter[w] = counter.get(w, 0) + 1
    return sorted(
        [(w, c) for w, c in counter.items() if c >= min_count],
        key=lambda x: x[1], reverse=True
    )

@app.post("/analyze")
def analyze_api(inp: TextIn):
    # Okt로 형태소 분석
    tokens = analyze_with_okt(inp.text)
    
    print("--- [단계 1] Okt가 분석한 원본 데이터 ---")
    print(tokens)
    
    # 필터링 및 분류
    nouns, verbs = filter_and_bucket_okt(tokens, min_len=2)
    
    print("--- [단계 2] 필터링 후 살아남은 단어 목록 ---")
    print("명사 목록:", nouns[:10])
    print("동사/형용사 목록:", verbs[:10])
    
    return {
        "nouns": freq_list(nouns, inp.min_freq),
        "verbs": freq_list(verbs, inp.min_freq) 
    }


app.mount("/", StaticFiles(directory=".", html=True), name="static")








