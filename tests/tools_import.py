# Importe un module de src/tools comme le fait le serveur MCP, qui tourne avec src/ comme dossier courant.
#
# Ces modules (web_search, web_cache, search_activities...) font "from config import active_config" : cela ne marche que
# si src/ est dans le chemin de recherche de Python, et ils se chargent alors sous le nom "tools.<module>" (et non
# "src.tools.<module>"). Sans cette aide, un test qui les importe echouerait avec "cannot import name active_config".
# Ce n'est pas un test : unittest ne le decouvre pas (le nom ne commence pas par test_).
#
# IMPORTANT : l'import est isole. Une fois le module charge, src/ est retire du chemin de recherche et le nom "config" est
# remis dans son etat d'avant. Sinon, tout le reste de la suite verrait un module "config" different de src.config, et des
# outils comme trip_db (qui essaie "from config import active_config" d'abord) liraient la configuration du serveur, donc
# le vrai chemin data/trips.db : un test aurait alors ecrit dans la vraie base de voyage de l'utilisateur.

import importlib
import sys

from src.config import ROOT

SRC = str(ROOT / "src")

# Le module src/config.py tel que le voit le serveur d'outils (nom "config"), garde ici apres l'import.
_server_config = None


def import_server_side(name: str):
    global _server_config
    inserted = SRC not in sys.path
    if inserted:
        sys.path.insert(0, SRC)
    # Ce que "config" designait avant (souvent le simple dossier config/ de la racine) : on le met de cote pour le remettre.
    previous = sys.modules.pop("config", None)
    if _server_config is not None:
        sys.modules["config"] = _server_config
    try:
        module = importlib.import_module(f"tools.{name}")
        if _server_config is None:
            _server_config = sys.modules.get("config")
    finally:
        if previous is not None:
            sys.modules["config"] = previous
        else:
            sys.modules.pop("config", None)
        if inserted:
            sys.path.remove(SRC)
    return module


# La configuration que voit le serveur d'outils (module "config" de src/), distincte de src.config cote agent.
def server_config():
    import_server_side("web_search")
    return _server_config
