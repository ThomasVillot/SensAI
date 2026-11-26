import argparse
import sys
from pathlib import Path

import uvicorn

from src.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, set_active_config
from src.share_server import app
from src.sharing import public_host


def fail(message):
    print(f"[erreur] {message}", file=sys.stderr)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="share.py",
        description="SensAI : serveur des liens de partage, en lecture seule.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="fichier de configuration JSON (défaut : config/config.json)",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        return fail(str(exc))
    if config.shares_dir is None:
        return fail("Partage désactivé : 'shares_dir' est absent de config/config.json.")
    set_active_config(config)

    print(f"Serveur de partage : http://{public_host(config.share_host)}:{config.share_port}/share/<lien> (Ctrl+C pour arrêter)")
    uvicorn.run(app, host=config.share_host, port=config.share_port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
