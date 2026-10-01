"""
rag.py  -  بحث بسيط بوثائق المعرفة (مشترك بين وكيل المعدات ووكيل الشحن)
بدون مكتبات إضافية: بنقسم الملفات لفقرات ونرتّبها حسب تطابق الكلمات (وزن IDF).
"""
import math
import os
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORD = re.compile(r"\w+", re.UNICODE)
_index = {}


def _tokens(text):
    return [w.lower() for w in _WORD.findall(text) if len(w) > 1]


def _build(folder):
    """يقرأ كل ملفات .md و .txt بالمجلد ويقسمها لفقرات"""
    path = os.path.join(ROOT, "knowledge", folder)
    chunks = []
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            if not name.endswith((".md", ".txt")):
                continue
            with open(os.path.join(path, name), encoding="utf-8") as f:
                text = f.read()
            heading = name
            for part in re.split(r"\n\s*\n", text):
                part = part.strip()
                if not part:
                    continue
                if part.startswith("#"):
                    heading = part.lstrip("# ").split("\n")[0]
                    if "\n" not in part:
                        continue
                chunks.append({"source": name, "heading": heading, "text": part,
                               "tokens": Counter(_tokens(part))})
    n = len(chunks)
    df = Counter()
    for c in chunks:
        df.update(c["tokens"].keys())
    idf = {w: math.log(1 + n / d) for w, d in df.items()}
    return {"chunks": chunks, "idf": idf}


def search_knowledge(folder, query, k=3):
    if folder not in _index:
        _index[folder] = _build(folder)
    idx = _index[folder]
    q = set(_tokens(query))
    scored = []
    for c in idx["chunks"]:
        score = sum(idx["idf"].get(w, 0) * (1 + math.log(c["tokens"][w])) for w in q if w in c["tokens"])
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [{"source": c["source"], "heading": c["heading"], "text": c["text"][:600],
             "score": round(float(s), 2)} for s, c in scored[:k]]


def make_search_tool(folder, tool_name="search_knowledge"):
    """يبني أداة جاهزة للوكيل (بتطلع بشكل العقد)"""
    def tool(query):
        try:
            hits = search_knowledge(folder, query)
        except Exception as e:  # noqa: BLE001
            return {"tool": tool_name, "error": str(e)}
        if not hits:
            return {"tool": tool_name, "strength": 0.0, "flagged": False, "sources": [],
                    "summary": "No relevant document found."}
        text = " | ".join(f"[{h['source']}] {h['text']}" for h in hits)
        return {"tool": tool_name, "strength": 0.0, "flagged": False,
                "sources": sorted({h['source'] for h in hits}), "summary": text}
    return tool


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "single sensor jump faulty sensor"
    for h in search_knowledge("plant", q):
        print(h["score"], h["source"], "|", h["heading"])
        print("   ", h["text"][:150].replace("\n", " "))