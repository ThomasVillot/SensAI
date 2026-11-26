import json
import os
import time
from sentence_transformers import SentenceTransformer
from sentence_transformers.util import cos_sim
import logging
import sys
import contextlib

SEMANTIC_CACHE_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

SEMANTIC_CACHE_FILE = ".web_cache_semantic.json"
NAIF_CACHE_FILE = ".web_cache_naif.json"

CACHE_SIMILARITY_PCT = 0.80
CACHE_TTL_SECONDS = 6 * 60 * 60

model = None


def prune_expired_cache(cache, cache_file):
    now = time.time()
    fresh_cache = []

    for item in cache:
        timestamp = item.get("timestamp")
        if timestamp is None:
            fresh_cache.append(item)
            continue

        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            continue

        if now - timestamp < CACHE_TTL_SECONDS:
            fresh_cache.append(item)

    cache[:] = fresh_cache
    reset_cache(cache_file)
    save_cache(cache_file, cache)
    return cache

def remove_logs_load_cache_model():
    for logger_name in [
        "huggingface_hub",
        "sentence_transformers",
        "transformers",
        "httpx",
        "httpcore",
    ]:
        logging.getLogger(logger_name).setLevel(logging.ERROR)

def init_cache_model_semantic():
    global model

    remove_logs_load_cache_model()

    load_model_cache_start = time.time()

    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stderr(devnull):
            model = SentenceTransformer(
                SEMANTIC_CACHE_MODEL
            )

    load_model_cache_end = time.time()

    print(f"[load semantic cache model] : {load_model_cache_end - load_model_cache_start:.2f}s", file=sys.stderr)


def load_cache(cache_file: str):
    if not os.path.exists(cache_file):
        return []

    with open(cache_file, "r", encoding="utf-8") as f:
        content = f.read()

    if content.strip() == "":
        return []

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print(f"Erreur: fichier {cache_file} malformé")
        return []


def save_cache(cache_file: str, cache):
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=4)

def reset_cache(cache_file: str):
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write("")

def search_in_cache_naif(query, cache):
    prune_expired_cache(cache, NAIF_CACHE_FILE)

    for item in cache:
        if item.get("query", "") == query:
            return item.get("result", [])
    return None

def search_in_cache_semantic(query, cache):
    prune_expired_cache(cache, SEMANTIC_CACHE_FILE)

    if model is None:
        init_cache_model_semantic()

    query_embedding = model.encode(query)

    best_result = None
    best_similarity = 0.0

    for item in cache:

        try:
            similarity = cos_sim(
                query_embedding,
                item.get("embedding", None)
            ).tolist()
        except:
            print(f"Item in cache is malformed")
            continue

        similarity_pct = similarity[0][0] if len(similarity) >= 1 and len(similarity[0]) >= 1 else 0.0

        if similarity_pct > best_similarity:
            best_similarity = similarity_pct
            best_result = item

    if best_similarity >= CACHE_SIMILARITY_PCT and best_result is not None:
        return best_result.get("result", None)

    return None

def exemple():
    msg = "Quelle est la capitale de la France ?"
    print(f"PREMIERE QUESTION : {msg}")

    cache = load_cache(SEMANTIC_CACHE_FILE)
    cache.append({
        "query": msg.strip().lower(),
        "embedding": model.encode(msg).tolist(),
        "result": "La capitale de la France est Paris",
        "timestamp": time.time()
    })

    save_cache(cache)

    msg2 = "La ville qui est la capitale française est ?"
    print(f"DEUXIEME QUESTION : {msg2}")

    start_cache = time.time()
    response = search_in_cache_semantic(msg2, cache)
    end_cache = time.time()

    print(f"RESPONSE ({end_cache - start_cache:.2f}s):\n{response}")

if __name__ == '__main__':
    exemple()