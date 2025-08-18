from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from konlpy.tag import Okt

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

okt = Okt()

STOPWORDS = {"이다", "하다", "없다", "있다", "보다", "되다", "들다", "자다", "말다", "오다", "가다", "주다", "되어다"}



class TextInput(BaseModel):
    text: str
    min_freq: int = 3  # 기본값 3

@app.post("/analyze")
def analyze_text(data: TextInput):
    morphs = okt.pos(data.text, stem=True)

    nouns = []
    verbs = []

    for word, tag in morphs:
        if tag in ["Josa", "Suffix", "Punctuation", "Foreign", "Number"]:
            continue
        if len(word.strip()) < 2:
            continue
        if word in STOPWORDS:
            continue

        if tag.startswith("Noun"):
            nouns.append(word)
        elif tag.startswith("Verb") or tag.startswith("Adjective"):
            verbs.append(word)

    def get_frequency_list(words):
        freq = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        return sorted(
            [(word, count) for word, count in freq.items() if count >= data.min_freq],
            key=lambda x: x[1],
            reverse=True
        )

    return {
        "nouns": get_frequency_list(nouns),
        "verbs": get_frequency_list(verbs)
    }


