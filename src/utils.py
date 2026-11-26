import json, re, yaml
import jsonschema
from pathlib import Path
from .config import ROOT

NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def extract_json_in_content(content: str):

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```(?:json)?\s*(.*?)```", content, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    best, best_length, position = None, 0, 0

    while True:
        starts = [i for i in (content.find("{", position), content.find("[", position)) if i != -1]

        if not starts:
            return best

        start = min(starts)

        try:
            value, end = decoder.raw_decode(content, start)
        except ValueError:
            position = start + 1
            continue

        if end - start > best_length:
            best, best_length = value, end - start

        position = end


def schema_problems(value, schema: dict, limit: int = 5) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: [str(part) for part in error.absolute_path])

    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '(plan)'} : {error.message}"
        for error in errors
    ][:limit]


def normalize_tool_calls(parsed, known_tools):
    if isinstance(parsed, dict):
        parsed = [parsed]

    if not isinstance(parsed, list):
        return None

    calls = []
    for item in parsed:
        if not isinstance(item, dict):
            return None

        function = item.get("function") if isinstance(item.get("function"), dict) else item
        name = function.get("name")
        arguments = function.get("arguments", function.get("parameters", {}))

        if not isinstance(name, str) or name not in known_tools:
            return None

        arguments = parse_tool_arguments(arguments)
        if arguments is None:
            return None

        calls.append({"function": {"name": name, "arguments": arguments}})

    return calls or None


def parse_tool_arguments(arguments):
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None

    return arguments if isinstance(arguments, dict) else None


def is_schema_valid(current, base_schema):

    if base_schema == {}:
        return False, {"error": "tool inexistant"}

    if "function" in base_schema:
        base_schema = base_schema["function"].get("parameters") or {}

    current = parse_tool_arguments(current)

    if current is None:
        return False, {"error": "arguments invalides : un objet JSON est attendu"}

    manquants = [k for k in base_schema.get("required", []) if k not in current]

    if manquants:
        return False, {"error": f"champs manquants : {manquants}"}

    for k, spec in base_schema.get("properties", {}).items():
        if k in current:
            attendu = {"string": str, "integer": int, "number": (int, float),
                       "array": list, "object": dict}.get(spec.get("type"))

            if attendu and not isinstance(current[k], attendu):
                return False, {"error": f"champ {k} : attendu {spec['type']}, reçu {type(current[k]).__name__}"}

    return True, None

def parse_yaml(yaml_file: str):
    try:
        with open(ROOT / Path(yaml_file), "r", encoding="utf-8") as fd:
            data = yaml.safe_load(fd)

        return data
    except:
        return None

def get_tool_schema(tools: list[dict], tool_name: str):
    idx = next(
        (i for i, tool in enumerate(tools) if tool.get("function", {}).get("name", None) == tool_name),
        None
    )

    if idx is not None and len(tools) > idx:
        return tools[idx]

    return None