from dotenv import load_dotenv
from config import active_config
from tavily import TavilyClient
import time
import os
import sys
import requests
from . import web_cache

load_dotenv()

MAX_CONTENT_PER_RESULT_BASIC = 500
MAX_CONTENT_PER_RESULT_ADVANCED = 1500

MAX_RESULTS_BASIC = 3
MAX_RESULTS_ADVANCED = 5

def web_search(query: str, max_results: int, max_content_per_result: int, search_depth: str):
    query = query.strip().lower()
    cache_mode = active_config().cache_mode
    cache_mode = "naif" if cache_mode is None else cache_mode

    cache = web_cache.load_cache(web_cache.NAIF_CACHE_FILE if cache_mode == "naif" else web_cache.SEMANTIC_CACHE_FILE)
    cache_result = web_cache.search_in_cache_naif(query, cache) if cache_mode == "naif" else web_cache.search_in_cache_semantic(query, cache)

    if cache_result is not None:
        return cache_result

    tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

    try:
        response = tavily_client.search(
            query=query,
            max_results=max_results,
            search_depth=search_depth
        )
    except requests.exceptions.ConnectionError:
        print("[erreur] connexion perdue. Essayez d'utiliser le mode de cache 'semantic' puis reposez votre question", file=sys.stderr)
        return "Connexion perdue"

    formatted_response = [
        {
            "title": r["title"],
            "url": r["url"],
            "content": r["content"][:max_content_per_result]
        }
        for r in response["results"]
    ]

    if cache_mode == "semantic":
        cache.append({
            "query": query.strip().lower(),
            "embedding": web_cache.model.encode(query).tolist() if web_cache.model is not None else [],
            "result": formatted_response,
            "timestamp": time.time()
        })
    else:
        cache.append({
            "query": query.strip().lower(),
            "result": formatted_response,
            "timestamp": time.time()
        })

    web_cache.save_cache(web_cache.NAIF_CACHE_FILE if cache_mode == "naif" else web_cache.SEMANTIC_CACHE_FILE, cache)

    return formatted_response


def web_search_basic(query: str):
    return web_search(query, MAX_RESULTS_BASIC, MAX_CONTENT_PER_RESULT_BASIC, "basic")


def web_search_deep(query: str):
    return web_search(query, MAX_RESULTS_ADVANCED, MAX_CONTENT_PER_RESULT_ADVANCED, "advanced")
