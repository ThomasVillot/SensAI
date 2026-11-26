"""Point d'entree unique des deux benchs.

    python bench/run.py outils --quick --repeat 1            # triage : elimine vite les mauvais
    python bench/run.py outils --models qwen3:8b!nothink mistral --repeat 3
    python bench/run.py outils --models llama3.1:8b --resume # ajoute un modele, garde les autres
    python bench/run.py reponse                              # bench de la reponse finale
    python bench/run.py outils --report-only                 # regenere les rapports, ne relance rien
    python bench/run.py garde_fous --repeat 20               # conformite des sorties structurees des garde-fous

Resultats et rapports : bench/results/<bench>/
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common.helpers import load_results
from outils import report as outils_report
from outils import runner as outils_runner
from reponse import report as reponse_report
from reponse import runner as reponse_runner
from garde_fous import cases as garde_cases
from garde_fous import report as garde_report
from garde_fous import runner as garde_runner

RESULTS = Path(__file__).resolve().parent / "results"


def _outils(args) -> None:
    if args.report_only:
        payload = load_results(args.out)
    else:
        payload = outils_runner.run(args.models, args.repeat, args.out,
                                    quick=args.quick, resume=args.resume)

    print("Rapport texte    : " + str(outils_report.write(payload, args.out)))
    if not args.no_charts:
        try:
            from outils import charts
        except ImportError as exc:
            print("Graphiques ignores (" + str(exc) + "). pip install matplotlib numpy")
        else:
            print("Rapport graphique: " + str(charts.build(payload, args.out)))
    print("\n" + outils_report.build(payload))


def _reponse(args) -> None:
    if args.report_only:
        payload = load_results(args.out)
    else:
        payload = reponse_runner.run(args.models, args.repeat, args.temperature, args.out)

    print("\nRapport : " + str(reponse_report.write(payload, args.out)) + "\n")
    print(reponse_report.build(payload))


def _garde_fous(args) -> None:
    if args.report_only:
        payload = load_results(args.out)
    else:
        payload = garde_runner.run(args.models or None, args.repeat, args.out, think_mode=args.think,
                                   guards=args.guards, case_ids=args.cases, threshold=args.seuil)

    print("\nRapport : " + str(garde_report.write(payload, args.out)) + "\n")
    print(garde_report.build(payload))


def _add_common(p: argparse.ArgumentParser, models: list[str], repeat: int, repeat_help: str) -> None:
    p.add_argument("--models", nargs="+", default=models,
                   help="modeles a tester (doivent etre deja pull). Defaut : le panel complet")
    p.add_argument("--repeat", type=int, default=repeat, help=repeat_help)
    p.add_argument("--out", type=Path, default=None,
                   help="dossier de sortie (defaut : results/<bench>/)")
    p.add_argument("--report-only", action="store_true",
                   help="ne relance rien, regenere les rapports depuis resultats.json")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Benchmarks des modeles Ollama pour SensAI",
        epilog="Suffixe '!nothink' pour desactiver le raisonnement d'un modele "
               "qui reflechit (qwen3, deepseek-r1...). Exemple : qwen3:8b!nothink",
    )
    sub = ap.add_subparsers(dest="bench", required=True)

    p = sub.add_parser("outils", help="le modele qui choisit les outils (tool-calling, JSON, raisonnement...)")
    _add_common(p, outils_runner.MODELS, 3,
                "essais par cas de test — 1 va vite, 3 est fiable, 5 est solide")
    p.add_argument("--quick", action="store_true",
                   help="mode triage : 14 cas au lieu de 31, pour eliminer vite les mauvais candidats")
    p.add_argument("--resume", action="store_true",
                   help="ne remesure pas les modeles deja presents dans resultats.json")
    p.add_argument("--no-charts", action="store_true", help="rapport texte uniquement")
    p.set_defaults(func=_outils)

    p = sub.add_parser("reponse", help="le modele qui redige la reponse finale (langue, delai)")
    _add_common(p, reponse_runner.MODELS, 1,
                "essais par cas ; n'a de sens qu'avec --temperature > 0")
    p.add_argument("--temperature", type=float, default=0.0,
                   help="0 = reproductible (defaut)")
    p.set_defaults(func=_reponse)

    p = sub.add_parser("garde_fous",
                       help="conformite des sorties structurees des garde-fous (donnees personnelles, perimetre)")
    p.add_argument("--models", nargs="+", default=None,
                   help="modeles a tester. Defaut : le modele de controle de la config (models.yaml)")
    p.add_argument("--repeat", type=int, default=20, help="essais par message (defaut : 20)")
    p.add_argument("--out", type=Path, default=None, help="dossier de sortie (defaut : results/garde_fous/)")
    p.add_argument("--report-only", action="store_true",
                   help="ne relance rien, regenere le rapport depuis resultats.json")
    p.add_argument("--think", choices=("prod", "off", "on"), default="prod",
                   help="prod = comme la production (think non envoye) ; off = think=false ; on = think=true")
    p.add_argument("--guards", nargs="+", choices=list(garde_cases.GUARDS), default=None,
                   help="un seul garde-fou (pii, scope). Defaut : les deux")
    p.add_argument("--cases", nargs="+", default=None, help="identifiants de messages (ex. p01-identite s09-injection)")
    p.add_argument("--seuil", type=float, default=99.0, help="taux de conformite vise, en %% (defaut : 99)")
    p.set_defaults(func=_garde_fous)

    args = ap.parse_args()
    args.out = args.out or RESULTS / args.bench
    args.func(args)


if __name__ == "__main__":
    main()
