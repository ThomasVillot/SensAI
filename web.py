import sys
from dataclasses import replace

import uvicorn

from src.cli import build_parser, fail
from src.config import ConfigError, load_config, set_active_config
from src.model_funcs import set_client
from src.ollama_client import OllamaClient, OllamaError
from src.web.server import app


def main():
    parser = build_parser()
    parser.prog = "web.py"
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        return fail(str(exc))

    if args.model is not None:
        model = args.model.strip()
        if not model:
            return fail("Le nom du modèle (--model) est vide.")
        config = replace(config, tool_model=model, response_model=model)
    if args.cache is not None:
        cache = args.cache.strip()
        if cache not in ["naif", "semantic"]:
            return fail("Le mode de cache (--cache) doit être 'naif' ou 'semantic'")
        config = replace(config, cache_mode=cache)
    set_active_config(config)

    client = OllamaClient(config.ollama_host, config.connect_timeout_s, config.read_timeout_s)
    set_client(client)
    try:
        for model_name in {config.tool_model, config.response_model}:
            client.ensure_model(model_name)
    except OllamaError as exc:
        return fail(str(exc))

    uvicorn.run(app, host="127.0.0.1", port=8765)
    return 0


if __name__ == "__main__":
    sys.exit(main())
