
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "config.json"

_REQUIRED = (
    "model",
    "ollama_host",
    "connect_timeout_s",
    "read_timeout_s",
    "max_iterations",
    "max_repeated_actions",
    "system_prompt_file",
    "tools_file",
    "schemas_file",
    "prompts_file",
    "models_file",
    "prompt_versions",
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class OutputGuardrails:
    leak_enabled: bool
    leak_shingle_words: int         # taille des suites de mots comparees aux prompts proteges
    leak_message: str
    malicious_enabled: bool
    malicious_message: str
    malicious_categories: dict      # categorie -> tuple d'expressions ("re:..." = expression reguliere)
    holdback_words: int | None      # mots gardes en attente avant affichage (None = calcule automatiquement)


@dataclass(frozen=True)
class GuardrailRule:
    enabled: bool
    action: str


@dataclass(frozen=True)
class GuardrailsConfig:
    prompt_injection: GuardrailRule
    out_of_scope: GuardrailRule
    pii_detection: GuardrailRule
    inappropriate_content: GuardrailRule
    internal_information: GuardrailRule


@dataclass(frozen=True)
class Config:
    model: str                  # "model" de config.json (dernier recours si models.yaml n'en donne pas)
    ollama_host: str
    connect_timeout_s: float
    read_timeout_s: float
    options: dict               # options Ollama (temperature...) : config.json + models.yaml
    system_prompt: str          # persona du chat simple (repli sans outils)
    latency_log: Path | None
    think: bool                 # valeur de "think" envoyee au modele d'outils
    max_iterations: int         # plafond d'appels modele par tour d'agent
    max_repeated_actions: int   # le tour s'arrete quand le meme appel d'outil revient ce nombre de fois d'affilee
    tool_model: str            # modele du role "outils"
    pii_check_model: str       # modele de detection des donnees personnelles
    response_model: str         # modele du role "reponse finale / plan"
    tools: list                 # definitions des outils (config/tools.yaml)
    schemas: dict               # schemas de sortie (config/schemas.yaml)
    tool_system_prompt: str     # prompts.yaml : tool_model
    pii_check_prompt_version: str = "v1"  # prompts.yaml : pii_check
    scope_check_prompt_version: str = "v1"  # prompts.yaml : scope_check
    warnings: tuple = ()        # a afficher au demarrage (ex. cle ignoree)
    cache_mode: str = "naif"
    interaction_log: Path | None = None  # EV3 : journal des interactions (None = desactive)
    tool_prompt_version: str = ""        # version de prompts.tool_model, notee dans chaque ligne du journal
    sessions_dir: Path | None = None     # M1 : dossier des sessions sauvegardees (None = --session refuse)
    profiles_dir: Path | None = None     # M1 : dossier des profils voyageur (None = --profile refuse)
    max_reloaded_turns: int | None = None  # M1 : tours recharges au plus au demarrage (None = tous)
    trip_db: Path | None = None            # M3 : base SQLite du voyage (None = memoire du voyage desactivee)
    shares_dir: Path | None = None
    share_ttl_days: int | None = None
    share_host: str | None = None
    share_port: int | None = None
    guardrails: GuardrailsConfig | None = None
    output_guardrails: OutputGuardrails | None = None  # garde-fous de sortie (None = desactives)


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from None
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Fichier de configuration illisible ({path}) : {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un objet YAML.")
    return data


def _prompt(prompts: dict, name: str, version: str, path: Path) -> str:
    try:
        today = datetime.now().astimezone()
        current_date = today.strftime("%Y-%m-%d")
        text = prompts["prompts"][name][version]["system"].format(current_date=current_date)
    except (KeyError, TypeError):
        raise ConfigError(f"Prompt introuvable dans {path} : prompts.{name}.{version}.system") from None
    if not isinstance(text, str) or not text.strip():
        raise ConfigError(f"Prompt vide dans {path} : prompts.{name}.{version}.system")
    return text.strip()


def _load_models(path: Path, fallback_model: str) -> tuple[str, str, str, float | None, list[str]]:
    data = _load_yaml(path)
    warnings: list[str] = []
    roles = []
    for key in ("default_tool_model", "default_response_model"):
        value = data.get(key)
        if value is None:
            roles.append(fallback_model)
        elif isinstance(value, str) and value.strip():
            roles.append(value.strip())
        else:
            raise ConfigError(f"'{key}' doit être un nom de modèle non vide ({path}).")

    pii_check_model = data.get("default_pii_check_model")
    if pii_check_model is None:
        pii_check_model = fallback_model
    elif not isinstance(pii_check_model, str) or not pii_check_model.strip():
        raise ConfigError(f"'default_pii_check_model' doit être un nom de modèle non vide ({path}).")
    pii_check_model = pii_check_model.strip()

    params = data.get("parameters") or {}
    if not isinstance(params, dict):
        raise ConfigError(f"'parameters' doit être un objet ({path}).")
    temperature = params.get("temperature")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float))):
        raise ConfigError(f"'parameters.temperature' doit être un nombre ({path}).")
    if params.get("stream") is False:
        warnings.append(
            f"{path.name} : 'parameters.stream: false' est ignoré (SensAI affiche toujours la réponse en streaming)."
        )
    return roles[0], roles[1], pii_check_model, temperature, warnings


def _share_settings(raw: dict, path: Path) -> tuple[Path | None, int | None, str | None, int | None]:
    shares_dir = raw.get("shares_dir")
    if not shares_dir:
        return None, None, None, None
    ttl_days = raw.get("share_ttl_days")
    if isinstance(ttl_days, bool) or not isinstance(ttl_days, int) or ttl_days < 1:
        raise ConfigError(f"'share_ttl_days' doit être un entier >= 1 ({path}).")
    host = raw.get("share_host")
    if not isinstance(host, str) or not host.strip():
        raise ConfigError(f"'share_host' doit être une adresse non vide ({path}).")
    port = raw.get("share_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError(f"'share_port' doit être un port entre 1 et 65535 ({path}).")
    return ROOT / str(shares_dir), ttl_days, host.strip(), port


def _guardrail_text(section: dict, key: str, path: Path) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{key}' doit être un texte non vide ({path}).")
    return value.strip()


def _load_guardrail_rule(section: dict, name: str, path: Path) -> GuardrailRule:
    if not isinstance(section, dict):
        raise ConfigError(f"La règle '{name}' doit être un objet dans {path}.")
    enabled = section.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"'guardrails.{name}.enabled' doit être true ou false ({path}).")

    allowed_actions = {
        "input.prompt_injection": {"refuse"},
        "input.out_of_scope": {"refuse"},
        "input.pii_detection": {"refuse", "anonymize"},
        "output.inappropriate_content": {"refuse"},
        "output.internal_information": {"refuse"},
    }
    action = str(section.get("action", "refuse")).strip().lower()
    valid_actions = allowed_actions.get(name, {"refuse", "anonymize"})
    if action not in valid_actions:
        expected = ", ".join(sorted(valid_actions))
        raise ConfigError(f"'guardrails.{name}.action' doit être {expected} ({path}).")
    return GuardrailRule(enabled=enabled, action=action)


def _default_guardrails() -> GuardrailsConfig:
    return GuardrailsConfig(
        prompt_injection=GuardrailRule(True, "refuse"),
        out_of_scope=GuardrailRule(True, "refuse"),
        pii_detection=GuardrailRule(True, "anonymize"),
        inappropriate_content=GuardrailRule(True, "refuse"),
        internal_information=GuardrailRule(True, "refuse"),
    )


def _load_guardrails(path: Path) -> GuardrailsConfig:
    data = _load_yaml(path)
    guardrails = data.get("guardrails")
    if guardrails is None:
        return _default_guardrails()
    if not isinstance(guardrails, dict):
        raise ConfigError(f"{path} doit contenir une section 'guardrails'.")
    input_section = guardrails.get("input") or {}
    output_section = guardrails.get("output") or {}
    if not isinstance(input_section, dict) or not isinstance(output_section, dict):
        raise ConfigError(f"'guardrails.input' et 'guardrails.output' doivent être des objets ({path}).")
    return GuardrailsConfig(
        prompt_injection=_load_guardrail_rule(input_section.get("prompt_injection", {}), "input.prompt_injection", path),
        out_of_scope=_load_guardrail_rule(input_section.get("out_of_scope", {}), "input.out_of_scope", path),
        pii_detection=_load_guardrail_rule(input_section.get("pii_detection", {}), "input.pii_detection", path),
        inappropriate_content=_load_guardrail_rule(output_section.get("inappropriate_content", {}), "output.inappropriate_content", path),
        internal_information=_load_guardrail_rule(output_section.get("internal_information", {}), "output.internal_information", path),
    )


def _load_output_guardrails(path: Path) -> OutputGuardrails | None:
    data = _load_yaml(path)
    section = data.get("output_guardrails")
    if not isinstance(section, dict):
        raise ConfigError(f"{path} doit contenir une section 'output_guardrails'.")

    legacy_cfg = data.get("guardrails")
    guardrails_cfg = _load_guardrails(path) if isinstance(legacy_cfg, dict) else None

    if section.get("enabled") is False:
        return None

    leak = dict(section.get("system_prompt_leak") or {})
    malicious = dict(section.get("malicious_content") or {})
    if not isinstance(leak, dict) or not isinstance(malicious, dict):
        raise ConfigError(f"'system_prompt_leak' et 'malicious_content' doivent être des objets ({path}).")

    if guardrails_cfg is not None:
        leak["enabled"] = guardrails_cfg.internal_information.enabled and leak.get("enabled", True)
        malicious["enabled"] = guardrails_cfg.inappropriate_content.enabled and malicious.get("enabled", True)

    shingle_words = leak.get("shingle_words", 10)
    if isinstance(shingle_words, bool) or not isinstance(shingle_words, int) or shingle_words < 3:
        raise ConfigError(f"'system_prompt_leak.shingle_words' doit être un entier >= 3 ({path}).")

    categories: dict[str, tuple[str, ...]] = {}
    raw_categories = malicious.get("categories") or {}
    if not isinstance(raw_categories, dict):
        raise ConfigError(f"'malicious_content.categories' doit être un objet ({path}).")
    for name, entries in raw_categories.items():
        if not isinstance(entries, list) or not all(isinstance(entry, str) and entry.strip() for entry in entries):
            raise ConfigError(f"La catégorie '{name}' doit être une liste de textes non vides ({path}).")
        for entry in entries:
            if entry.startswith("re:"):
                try:
                    re.compile(entry[3:])
                except re.error as exc:
                    raise ConfigError(f"Expression régulière invalide dans '{name}' ({path}) : {entry} ({exc})") from None
        categories[str(name)] = tuple(entries)

    holdback = section.get("holdback_words")
    if holdback is not None and (isinstance(holdback, bool) or not isinstance(holdback, int) or holdback < 0):
        raise ConfigError(f"'holdback_words' doit être un entier >= 0 ou null ({path}).")

    leak_enabled = leak.get("enabled", True) is not False
    malicious_enabled = malicious.get("enabled", True) is not False
    return OutputGuardrails(
        leak_enabled=leak_enabled,
        leak_shingle_words=shingle_words,
        leak_message=_guardrail_text(leak, "message", path) if leak_enabled else "",
        malicious_enabled=malicious_enabled,
        malicious_message=_guardrail_text(malicious, "message", path) if malicious_enabled else "",
        malicious_categories=categories,
        holdback_words=holdback,
    )


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ConfigError(f"Fichier de configuration introuvable : {path}") from None
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Fichier de configuration illisible ({path}) : {exc}") from None

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} doit contenir un objet JSON.")
    missing = [key for key in _REQUIRED if key not in raw]
    if missing:
        raise ConfigError(f"Clés manquantes dans {path} : {', '.join(missing)}")

    model = raw["model"]
    if not isinstance(model, str) or not model.strip():
        raise ConfigError(f"'model' doit être un nom de modèle non vide ({path}).")

    try:
        connect_timeout = float(raw["connect_timeout_s"])
        read_timeout = float(raw["read_timeout_s"])
    except (TypeError, ValueError):
        raise ConfigError(f"Les timeouts doivent être des nombres ({path}).") from None

    max_iterations = raw["max_iterations"]
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
        raise ConfigError(f"'max_iterations' doit être un entier >= 1 ({path}).")

    max_repeated_actions = raw["max_repeated_actions"]
    if isinstance(max_repeated_actions, bool) or not isinstance(max_repeated_actions, int) or max_repeated_actions < 2:
        raise ConfigError(f"'max_repeated_actions' doit être un entier >= 2 ({path}).")

    think =raw.get("think", False)
    if not isinstance(think, bool):
        raise ConfigError(f"'think' doit être true ou false ({path}).")

    options = raw.get("options", {})
    if not isinstance(options, dict):
        raise ConfigError(f"'options' doit être un objet JSON ({path}).")

    versions = raw["prompt_versions"]
    if not isinstance(versions, dict):
        raise ConfigError(f"'prompt_versions' doit être un objet JSON ({path}).")
    try:
        tool_version = str(versions["tool_model"])
    except KeyError as exc:
        raise ConfigError(f"'prompt_versions.{exc.args[0]}' manquant ({path}).") from None

    pii_check_version = str(versions.get("pii_check", "v1")).strip() or "v1"
    scope_check_version = str(versions.get("scope_check", "v1")).strip() or "v1"

    prompt_path = ROOT / str(raw["system_prompt_file"])
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8-sig").strip()
    except OSError as exc:
        raise ConfigError(f"Prompt système illisible ({prompt_path}) : {exc}") from None

    tools_path = ROOT / str(raw["tools_file"])
    tools_data = _load_yaml(tools_path).get("tools")
    if not isinstance(tools_data, dict):
        raise ConfigError(f"{tools_path} doit contenir une section 'tools'.")
    tools = list(tools_data.values())
    for tool in tools:
        name = tool.get("function", {}).get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str) or not name:
            raise ConfigError(f"Outil sans 'function.name' dans {tools_path}.")

    schemas_path = ROOT / str(raw["schemas_file"])
    schemas = _load_yaml(schemas_path).get("schemas") or {}
    if not isinstance(schemas, dict):
        raise ConfigError(f"'schemas' doit être un objet dans {schemas_path}.")

    prompts_path = ROOT / str(raw["prompts_file"])
    prompts = _load_yaml(prompts_path)
    tool_system_prompt = _prompt(prompts, "tool_model", tool_version, prompts_path)

    models_path = ROOT / str(raw["models_file"])
    tool_model, response_model, pii_check_model, temperature, warnings = _load_models(models_path, model.strip())
    if temperature is not None:
        options = {**options, "temperature": temperature}

    max_reloaded_turns = raw.get("max_reloaded_turns")
    if max_reloaded_turns is not None and (
        isinstance(max_reloaded_turns, bool) or not isinstance(max_reloaded_turns, int) or max_reloaded_turns < 1
    ):
        raise ConfigError(f"'max_reloaded_turns' doit être un entier >= 1 ou null ({path}).")

    latency_log = raw.get("latency_log")
    interaction_log = raw.get("interaction_log")
    sessions_dir = raw.get("sessions_dir")
    profiles_dir = raw.get("profiles_dir")
    trip_db = raw.get("trip_db")
    shares_dir, share_ttl_days, share_host, share_port = _share_settings(raw, path)
    guardrails_file = raw.get("guardrails_file")
    guardrails_path = ROOT / str(guardrails_file) if guardrails_file else None
    guardrails = _load_guardrails(guardrails_path) if guardrails_path else _default_guardrails()
    output_guardrails = _load_output_guardrails(guardrails_path) if guardrails_path else None
    return Config(
        model=model.strip(),
        ollama_host=str(raw["ollama_host"]),
        connect_timeout_s=connect_timeout,
        read_timeout_s=read_timeout,
        options=options,
        system_prompt=system_prompt,
        latency_log=(ROOT / latency_log) if latency_log else None,
        think=think,
        max_iterations=max_iterations,
        max_repeated_actions=max_repeated_actions,
        tool_model=tool_model,
        pii_check_model=pii_check_model,
        response_model=response_model,
        tools=tools,
        schemas=schemas,
        tool_system_prompt=tool_system_prompt,
        pii_check_prompt_version=pii_check_version,
        scope_check_prompt_version=scope_check_version,
        warnings=tuple(warnings),
        cache_mode="naif",
        interaction_log=(ROOT / interaction_log) if interaction_log else None,
        tool_prompt_version=tool_version,
        sessions_dir=(ROOT / str(sessions_dir)) if sessions_dir else None,
        profiles_dir=(ROOT / str(profiles_dir)) if profiles_dir else None,
        max_reloaded_turns=max_reloaded_turns,
        trip_db=(ROOT / str(trip_db)) if trip_db else None,
        shares_dir=shares_dir,
        share_ttl_days=share_ttl_days,
        share_host=share_host,
        share_port=share_port,
        guardrails=guardrails,
        output_guardrails=output_guardrails,
    )


_active: Config | None = None


def set_active_config(config: Config | None) -> None:
    global _active
    _active = config


def active_config() -> Config:
    global _active
    if _active is None:
        _active = load_config()
    return _active
