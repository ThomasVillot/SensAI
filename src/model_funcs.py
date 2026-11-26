import re
import yaml
from .config import ROOT, active_config
from .injection_patterns import detect_prompt_injection
from .ollama_client import OllamaClient, OllamaError
from .utils import extract_json_in_content, schema_problems

_client: OllamaClient | None = None


class InvalidResponseError(OllamaError):
    pass


INVALID_RESPONSE_PATTERNS = (
    re.compile(r"\{function"),
    re.compile(r"\{object"),
    re.compile(r"\<nil\>"),
)

DEFAULT_PII_PROMPT = """
Tu es un système spécialisé dans la détection des informations personnelles (PII).

Ta mission est d'extraire toutes les informations personnelles présentes dans le message de l'utilisateur.
Retourne uniquement un objet JSON au format demandé, sans explication.

Types autorisés : FIRST_NAME, LAST_NAME, EMAIL, PHONE_NUMBER, POSTAL_ADDRESS, DATE_OF_BIRTH,
PASSPORT_NUMBER, CREDIT_CARD, BANK_ACCOUNT, IP_ADDRESS, OTHER.

Règles :
- Ne jamais inventer d'informations.
- Extraire uniquement les informations présentes dans le texte original.
- Ne pas considérer les destinations de voyage comme des adresses personnelles.
- Ne pas considérer les dates de voyage comme des dates de naissance.
- Identifier uniquement les informations personnelles réellement identifiables.
- Si aucune information n'est détectée, retourne {"personal_information": []}.
"""


def _load_prompt(name: str, version: str) -> str:
    prompts_path = ROOT / "config" / "prompts.yaml"
    try:
        data = yaml.safe_load(prompts_path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError):
        return DEFAULT_PII_PROMPT
    try:
        return (data.get("prompts", {}).get(name, {}).get(version, {}).get("system") or DEFAULT_PII_PROMPT).strip()
    except AttributeError:
        return DEFAULT_PII_PROMPT


def contains_invalid_response_pattern(text: str | None) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in INVALID_RESPONSE_PATTERNS)


def set_client(client: OllamaClient | None) -> None:
    global _client
    _client = client


def get_client() -> OllamaClient:
    global _client
    if _client is None:
        config = active_config()
        _client = OllamaClient(config.ollama_host, config.connect_timeout_s, config.read_timeout_s)
    return _client


def extract_params(data_response: dict):
    tool_calls = (data_response.get("message", {}) or {}).get("tool_calls") or []
    params = [
        tool_call.get("function", {}).get("arguments", {})
        for tool_call in tool_calls
    ]

    if not params:
        params = extract_json_in_content(data_response.get("message", {}).get("content", ""))

    return params


def detect_sensitive_information(model: str, text: str | None) -> list[dict]:
    if not text or not text.strip():
        return []

    schema = active_config().schemas.get("personal_information")
    if not schema:
        return []

    system_prompt = _load_prompt("pii_check", active_config().pii_check_prompt_version)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    response = request_response_model(model=model, messages=messages, format=schema)
    if response is None:
        return []

    parsed = extract_json_in_content(response)
    if isinstance(parsed, dict):
        items = parsed.get("personal_information", [])
    elif isinstance(parsed, list):
        items = parsed
    else:
        return []

    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def detect_sensitive_information_with_regex(text: str | None) -> list[dict]:
    if not text or not text.strip():
        return []

    patterns = (
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "EMAIL"),
        (re.compile(r"(?:\+33|0)[ .-]?(?:[1-9](?:[ .-]?[0-9]){8})"), "PHONE_NUMBER"),
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IP_ADDRESS"),
    )

    found: list[dict] = []
    for pattern, pii_type in patterns:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            if not value:
                continue
            found.append({"type": pii_type, "value": value})

    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in found:
        key = (str(item.get("type", "OTHER")).upper(), str(item.get("value", "")).strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _normalize_pii_detections(detections: list[dict]) -> set[tuple[str, str]]:
    normalized: set[tuple[str, str]] = set()
    for item in detections:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        pii_type = str(item.get("type", "OTHER")).upper()
        normalized.add((pii_type, value))
    return normalized


def _guardrail_rule(name: str):
    config = active_config()
    guardrails = getattr(config, "guardrails", None)
    if guardrails is None:
        return None
    mapping = {
        "pii_detection": getattr(guardrails, "pii_detection", None),
        "prompt_injection": getattr(guardrails, "prompt_injection", None),
        "out_of_scope": getattr(guardrails, "out_of_scope", None),
    }
    return mapping.get(name)


def sanitize_user_message(text: str, model: str | None = None) -> str:
    if not text:
        return text

    pii_rule = _guardrail_rule("pii_detection")
    if pii_rule is not None and not pii_rule.enabled:
        return text

    config = active_config()
    model_name = model or getattr(config, "pii_check_model", config.tool_model)
    llm_detections = detect_sensitive_information(model_name, text)
    regex_detections = detect_sensitive_information_with_regex(text)

    detections = llm_detections
    if regex_detections:
        if not llm_detections or _normalize_pii_detections(llm_detections) != _normalize_pii_detections(regex_detections):
            detections = regex_detections

    if not detections:
        return text

    if pii_rule is not None and pii_rule.action == "refuse":
        raise ValueError("Demande refusée : information personnelle détectée.")

    sanitized = text
    for item in sorted(detections, key=lambda entry: len(str(entry.get("value", ""))), reverse=True):
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        label = str(item.get("type", "OTHER")).upper()
        sanitized = re.sub(re.escape(value), f"[{label}]", sanitized)
    return sanitized


def detect_message_scope(model: str, text: str) -> str:
    if not text.strip():
        return "IN_SCOPE"

    schema = active_config().schemas.get("message_scope")
    if not schema:
        return "IN_SCOPE"

    system_prompt = _load_prompt("scope_check", active_config().scope_check_prompt_version)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]

    response = request_response_model(model=model, messages=messages, format=schema, raise_unavailable=True)
    if response is None:
        return "OUT_OF_SCOPE"

    parsed = extract_json_in_content(response)
    if not isinstance(parsed, dict):
        return "OUT_OF_SCOPE"

    classification = str(parsed.get("classification", "IN_SCOPE")).upper()
    valid = {"IN_SCOPE", "OUT_OF_SCOPE", "AMBIGUOUS", "CONTEXT_AMBIGUOUS", "INJECTION"}
    return classification if classification in valid else "IN_SCOPE"


def validate_message_scope(text: str, model: str | None = None) -> tuple[bool, str]:
    if not text:
        return True, ""

    prompt_rule = _guardrail_rule("prompt_injection")
    if prompt_rule is not None and prompt_rule.enabled and detect_prompt_injection(text):
        if prompt_rule.action == "refuse":
            return False, "INJECTION"

    out_of_scope_rule = _guardrail_rule("out_of_scope")
    config = active_config()
    model_name = model or getattr(config, "pii_check_model", config.tool_model)
    scope = detect_message_scope(model_name, text)
    if scope in {"OUT_OF_SCOPE", "CONTEXT_AMBIGUOUS", "INJECTION"}:
        if out_of_scope_rule is not None and out_of_scope_rule.enabled and out_of_scope_rule.action == "refuse":
            return False, scope
        return True, scope
    elif scope == "AMBIGUOUS":
        sncd_scope = detect_message_scope(model_name, text)
        if sncd_scope in {"OUT_OF_SCOPE", "CONTEXT_AMBIGUOUS", "INJECTION"}:
            if out_of_scope_rule is not None and out_of_scope_rule.enabled and out_of_scope_rule.action == "refuse":
                return False, scope
            return True, scope
    return True, scope


def request_tool_model(model: str, messages: list[dict], tools: list[dict] | None = None):
    config = active_config()

    try:
        for event in get_client().chat_events(
            model, messages, config.options, tools=tools, think=config.think
        ):
            if event["type"] == "token":
                content = event.get("content", "")
                if contains_invalid_response_pattern(content):
                    raise OllamaError("Réponse invalide reçue d'Ollama : contenu technique détecté dans le flux de tokens.")
                yield {"type": "token", "content": content}

            elif event["type"] == "done":
                message = event.get("message") or {}
                if contains_invalid_response_pattern(message.get("content")):
                    raise OllamaError("Réponse invalide reçue d'Ollama : contenu technique détecté dans le message final.")
                yield {"type": "done", "message": message}

    except OllamaError as e:
        yield {"type": "error", "message": str(e)}

def request_response_model(model: str, messages: list[dict], format: dict | None = None, raise_unavailable: bool = False):
    config = active_config()

    try:
        response_message = ""

        for event in get_client().chat_events(model, messages, config.options, format=format):
            if event["type"] == "token":
                content = event.get("content", "")
                if contains_invalid_response_pattern(content):
                    raise InvalidResponseError("Réponse invalide reçue d'Ollama : contenu technique détecté dans le flux de tokens.")
                response_message += content
            elif event["type"] == "done":
                message = event.get("message") or {}
                if contains_invalid_response_pattern(message.get("content")):
                    raise InvalidResponseError("Réponse invalide reçue d'Ollama : contenu technique détecté dans le message final.")

        if format is not None:
            parsed = extract_json_in_content(response_message)
            if parsed is None:
                raise InvalidResponseError("Réponse invalide reçue d'Ollama : aucune donnée JSON valide n'a été retournée.")
            problems = schema_problems(parsed, format)
            if problems:
                raise InvalidResponseError(f"Réponse invalide reçue d'Ollama : le format ne correspond pas au schéma attendu ({problems[0]}).")

        return response_message
    except OllamaError as e:
        if raise_unavailable and not isinstance(e, InvalidResponseError):
            raise
        return None
