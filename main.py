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

# Okt 인스턴스 생성 (앱 시작시 한 번만)
okt = Okt()

class TextIn(BaseModel):
    text: str
    min_freq: int = 5

# 불용어 설정
STOPWORDS = {
    "이다", "하다", "없다", "있다", "보다", "되다", "들다", "자다", "말다", "오다", "가다", "주다", "되어다"
}

def analyze_with_okt(text: str) -> List[Dict[str, str]]:
    """KoNLPy Okt로 형태소 분석"""
    try:
        # pos 함수로 (단어, 품사) 튜플 리스트 반환
        morphs = okt.pos(text)
        tokens = []
        
        for surface, pos in morphs:
            # 원형 추출 (동사/형용사는 기본형으로 변환)
            if pos.startswith('Verb') or pos.startswith('Adjective'):
                lemma = surface + "다" if not surface.endswith("다") else surface
            else:
                lemma = surface
                
            tokens.append({
                "surface": surface,
                "pos": pos, 
                "lemma": lemma
            })
        
        return tokens
    except Exception as e:
        print(f"KoNLPy 분석 실패: {e}")
        return []

def filter_and_bucket_okt(tokens: List[Dict[str, str]], min_len: int = 2):
    """KoNLPy 태그에 맞춘 필터링"""
    nouns = []
    verbs = []
    
    for t in tokens:
        surface = t.get("surface", "").strip()
        pos = t.get("pos", "").strip()
        lemma = t.get("lemma", surface).strip()
        
        # 길이 체크
        if len(lemma) < min_len:
            continue
            
        # 불용어 체크
        if lemma in STOPWORDS:
            continue
        
        # 한글이 포함된 단어만 처리
        if not re.search(r"[가-힣]", lemma):
            continue
        
        # KoNLPy 태그 기준 분류
        if pos.startswith('Noun'):
            nouns.append(lemma)
        elif pos.startswith('Verb') or pos.startswith('Adjective'):
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
    print(tokens[:10])  # 처음 10개만 출력
    
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