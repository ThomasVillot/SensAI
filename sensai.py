# Point d'entree : python sensai.py [--model MODELE] [--config FICHIER]

import sys
import asyncio

if sys.version_info < (3, 10):
    sys.exit("[erreur] SensAI nécessite Python 3.10 ou plus.")

try:
    from src.cli import main
except ImportError as exc:
    sys.exit(f"[erreur] Dépendance manquante ({exc}). Lance : pip install -r requirements.txt")

if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
