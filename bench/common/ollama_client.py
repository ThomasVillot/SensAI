"""Client Ollama minimal — uniquement l'API HTTP, via requests.

Aucune bibliotheque LLM n'est utilisee ici : le sujet impose que tous les
appels au modele passent directement par l'API HTTP d'Ollama.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import requests

OLLAMA = "http://localhost:11434"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 300
SEED = 42

_THINK_TAGS = re.compile(r"<think>.*?</think>", re.S | re.I)
# Bloc <think> ferme, ou ouvert et pas encore ferme pendant le streaming.
_THINK_OPEN = re.compile(r"<think>.*?(?:</think>|\Z)", re.S | re.I)


class OllamaError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Modeles a tester
# --------------------------------------------------------------------------

@dataclass
class Spec:
    """Un modele + sa configuration. Deux Spec peuvent viser le meme modele.

    'qwen3:8b'          -> comportement par defaut du modele
    'qwen3:8b!nothink'  -> think=false, ce qui divise la latence par ~4
    'qwen3:8b!think'    -> think=true force
    """

    model: str
    think: bool | None = None

    @property
    def label(self) -> str:
        if self.think is None:
            return self.model
        return self.model + (" (think)" if self.think else " (nothink)")


def parse_spec(text: str) -> Spec:
    for suffix, think in (("!nothink", False), ("!think", True)):
        if text.endswith(suffix):
            return Spec(text[: -len(suffix)], think)
    return Spec(text)


def installed_models() -> set[str]:
    """Liste les modeles presents, via GET /api/tags."""
    try:
        r = requests.get(OLLAMA + "/api/tags", timeout=CONNECT_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(
            "Ollama ne repond pas sur " + OLLAMA + " — lance 'ollama serve'. (" + str(exc) + ")"
        ) from exc
    return {m.get("model", "") for m in r.json().get("models", [])}


def resolve_specs(names: list[str]) -> list[Spec]:
    """Transforme les noms passes en CLI en Spec, en sautant les modeles absents.

    Accepte 'mistral' comme 'mistral:latest'. Un modele absent est signale,
    pas une erreur : on peut lancer le panel complet sans tout avoir installe.
    """
    installed = installed_models()
    specs = []
    for name in names:
        spec = parse_spec(name)
        if spec.model not in installed and spec.model + ":latest" in installed:
            spec.model += ":latest"
        if spec.model not in installed:
            print("[skip] " + spec.model + " n'est pas installe  ->  ollama pull " + spec.model)
            continue
        specs.append(spec)
    if not specs:
        raise SystemExit("Aucun modele disponible. Installe-en un avec 'ollama pull'.")
    return specs


# --------------------------------------------------------------------------
# Appels
# --------------------------------------------------------------------------

def chat(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    fmt=None,
    think: bool | None = None,
    timeout: int = READ_TIMEOUT,
) -> dict:
    """Un appel POST /api/chat non streame, a temperature 0 et seed fixe.

    Renvoie le JSON brut d'Ollama + '_wall_s', ou {'_error': ...} en cas d'echec.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.0, "seed": SEED},
    }
    if tools:
        payload["tools"] = tools
    if fmt is not None:
        payload["format"] = fmt
    if think is not None:
        payload["think"] = think

    started = time.perf_counter()
    try:
        r = requests.post(
            OLLAMA + "/api/chat", json=payload, timeout=(CONNECT_TIMEOUT, timeout)
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as exc:
        return {"_error": type(exc).__name__ + ": " + str(exc),
                "_wall_s": time.perf_counter() - started}

    data["_wall_s"] = time.perf_counter() - started
    return data


def chat_stream(
    model: str,
    messages: list[dict],
    think: bool | None = None,
    temperature: float = 0.0,
    seed: int = SEED,
    max_tokens: int | None = None,
) -> dict:
    """POST /api/chat en streaming, pour mesurer le delai avant le premier mot.

    En non-streame on ne connait que la duree totale. Or la reponse finale est
    affichee au fil de l'eau : ce que l'utilisateur ressent, c'est le temps
    avant le premier mot visible — le raisonnement d'un qwen3 n'en fait pas partie.

    Renvoie le dernier chunk (compteurs natifs) avec le message reconstitue,
    + '_wall_s' et '_ttft_s', ou {'_error': ...} en cas d'echec.
    """
    options: dict = {"temperature": temperature, "seed": seed}
    if max_tokens:
        options["num_predict"] = max_tokens
    payload: dict = {"model": model, "messages": messages, "stream": True, "options": options}
    if think is not None:
        payload["think"] = think

    started = time.perf_counter()
    content, thinking = "", ""
    first = None
    final: dict = {}
    try:
        with requests.post(OLLAMA + "/api/chat", json=payload, stream=True,
                           timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)) as r:
            if r.status_code != 200:
                return {"_error": "HTTP " + str(r.status_code) + " : " + r.text[:200],
                        "_wall_s": time.perf_counter() - started}
            for line in r.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    return {"_error": str(chunk["error"]), "_wall_s": time.perf_counter() - started}
                msg = chunk.get("message") or {}
                thinking += msg.get("thinking") or ""
                content += msg.get("content") or ""
                if first is None and _THINK_OPEN.sub("", content).strip():
                    first = time.perf_counter() - started
                if chunk.get("done"):
                    final = chunk
    except (requests.RequestException, json.JSONDecodeError) as exc:
        return {"_error": type(exc).__name__ + ": " + str(exc),
                "_wall_s": time.perf_counter() - started}

    final["message"] = {"content": content, "thinking": thinking}
    final["_wall_s"] = time.perf_counter() - started
    final["_ttft_s"] = first
    return final


def warmup(model: str, think: bool | None = None) -> float | None:
    """Charge le modele en memoire pour que load_duration ne fausse pas la 1re mesure.

    Renvoie le temps de chargement (s), None si l'appel a echoue.
    """
    data = chat(model, [{"role": "user", "content": "ok"}], think=think, timeout=180)
    return None if "_error" in data else metrics(data)["load_s"]


# --------------------------------------------------------------------------
# Lecture des reponses
# --------------------------------------------------------------------------

def split_thinking(data: dict) -> tuple[str, str]:
    """Separe le raisonnement affiche de la reponse utile.

    Certains modeles (qwen3, deepseek-r1...) renvoient un champ 'thinking',
    d'autres encadrent leur reflexion par <think></think> dans le contenu.
    Sans cette separation, on compare un modele qui reflechit a voix haute
    avec un modele qui ne le fait pas — et la latence n'a plus de sens.
    """
    msg = data.get("message", {}) or {}
    thinking = msg.get("thinking") or ""
    content = msg.get("content") or ""

    inline = _THINK_TAGS.findall(content)
    if inline:
        thinking = thinking + "\n".join(inline)
        content = _THINK_TAGS.sub("", content)

    return thinking.strip(), content.strip()


def tool_calls(data: dict) -> list[dict]:
    """Renvoie [{'name': ..., 'args': {...}}] a partir du tool-calling natif."""
    msg = data.get("message", {}) or {}
    out = []
    for call in msg.get("tool_calls") or []:
        fn = call.get("function", {}) or {}
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        out.append({"name": fn.get("name"), "args": args if isinstance(args, dict) else {}})
    return out


def extract_json(text: str):
    """Recupere le premier objet ou tableau JSON equilibre dans du texte.

    Repli pour les modeles qui n'ont pas de tool-calling natif et noient
    leur JSON dans de la prose ou un bloc markdown.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def metrics(data: dict) -> dict:
    """Extrait les compteurs natifs d'Ollama (nanosecondes -> secondes)."""
    def ns(key):
        v = data.get(key)
        return (v / 1e9) if isinstance(v, (int, float)) else None

    out_tok = data.get("eval_count")
    gen_s = ns("eval_duration")
    return {
        "total_s": ns("total_duration") or data.get("_wall_s"),
        "load_s": ns("load_duration"),
        "ttft_s": data.get("_ttft_s"),
        "output_tokens": out_tok,
        "gen_tok_s": (out_tok / gen_s) if out_tok and gen_s else None,
    }
