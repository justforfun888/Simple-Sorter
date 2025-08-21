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

STOPWORDS = {
    # --- [필수] ---
    "이다", "하다", "없다", "있다", "보다", "되다", "들다", "자다", "말다", "오다", "가다", "주다", "되어다", "않다",
    "싶다", "지다", "였다", "그렇다", "이렇다"
      # --- [그룹 2] ---
    "할다", "하는다", "이었다", "이런다", "그런다", "잘다", "되어다",
    "채는다", "있는다", "없는다", "않는다", "있다는다", "어떻게다",
    "있던다", "하면다", "아니고다", "하게다", "같은다", "하지다",
    "그럴다", "될다", "보니다", "있을다", "달았다", "들은다",
    "하고다", "봐요다", "없어다", "나오는다",

    # --- [그룹 3]---
    "하다", "보다", "오다", "가다", "주다", "들다", "자다"
}

NOUN_STOPWORDS = {
    "조금", "거기", "여기", "저기",
    "이것", "그것", "저것", "무엇", "누구",
    "때문", "하나", "오늘", "어제", "내일",
    "정도", "가지", "경우", "동안", "수가", "건가", "보고", "무슨", "지금"
}

def analyze_with_okt(text: str) -> List[Dict[str, str]]:

    okt_result = okt.pos(text, norm=True, stem=True)
    
    tokens = []
    i = 0
    while i < len(okt_result):
        word, pos = okt_result[i]
        
        # --- [핵심 로직!] '명사 + 하다' 패턴을 감지하고 하나로 합치기 ---
        # 예: ('공부', 'Noun'), ('하다', 'Verb') -> '공부하다'
        # 예: ('후련', 'Noun'), ('하다', 'Adjective') -> '후련하다'
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



# main.py 파일의 filter_and_bucket_okt 함수를 아래 내용으로 교체해주세요.

def filter_and_bucket_okt(tokens: List[Dict[str, str]], min_len: int = 2):
    """
    깨끗하게 정제된 토큰들을 명사와 동사/형용사로 분류합니다.
    """
    nouns = []
    verbs = []
    
    for t in tokens:
        # 이제 t['lemma']는 '공부하다', '채다' 처럼 완벽한 형태입니다.
        lemma = t.get("lemma", "").strip()
        pos = t.get("pos", "").strip()
        
        # --- 이제 필터링이 아주 간단해집니다 ---
        if len(lemma) < min_len:
            continue
        # 두 종류의 불용어 목록을 한 번에 확인합니다.
        if lemma in STOPWORDS or lemma in NOUN_STOPWORDS:
            continue
        
        # KoNLPy의 정확한 품사 태그를 기준으로 분류합니다.
        if pos == 'Noun':
            nouns.append(lemma)
        elif pos == 'Verb' or pos == 'Adjective':
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







