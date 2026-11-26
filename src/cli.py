
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

from . import metrics
from .agent import close_mcp, run_agent
from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config, set_active_config
from .export import document_to_json, to_json, to_markdown
from .interaction_log import (
    KIND_AGENT,
    KIND_CHAT,
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_INTERRUPTED,
    STATUS_MAX_ITERATIONS,
    STATUS_OK,
    InteractionLog,
    TurnRecorder,
)
from .injection_patterns import MAX_PROMPT_LENGTH, PromptValidationError, validate_prompt
from .latency import LatencyTracker, combine_results
from .model_funcs import sanitize_user_message, set_client, validate_message_scope
from .ollama_client import ChatResult, OllamaClient, OllamaError
from .output_guard import GuardrailBlocked, build_output_guard, event_reason
from .session import Session, SessionError, latest_session_name, new_session_name
from .pdf_export import PdfError, to_pdf
from .share_server import STARTED, ShareServerError, background_running, serve_in_background, stop_background
from .sharing import (
    ShareError,
    create_document_share,
    create_share,
    format_expiry,
    public_host,
    revoke_conversation,
    revoke_share,
    share_url,
    token_from,
)
from .tools.trip_db import TripError, clear_all
from .tools.trip_document import read_document, render_markdown
from .user_profile import ProfileError, load_profile, render_profile
from .utils import NAME_PATTERN

EXIT_COMMANDS = {"/exit", "/quit"}
NEW_COMMANDS = {"/new", "/nouveau", "/new_chat"}
DOC_COMMAND = "/doc"
CLEAR_COMMAND = "/clear"
EXPORT_COMMAND = "/export"
SHARE_COMMAND = "/share"
SHARE_DOCUMENT = "doc"
UNSHARE_COMMAND = "/unshare"
CONFIRM_ANSWERS = {"o", "oui", "y", "yes"}
STATS_COMMAND = "/stats"
PROMPT_COMMAND = "/prompt"
RESULT_PREVIEW_CHARS = 200

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sensai.py",
        description="SensAI : assistant de voyage CLI connecté à un modèle local via Ollama.",
    )
    parser.add_argument(
        "--model",
        help="modèle Ollama à utiliser pour tous les rôles "
        "(sinon : config/models.yaml, puis 'model' de config/config.json)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="fichier de configuration JSON (défaut : config/config.json)",
    )

    parser.add_argument(
        "--cache",
        help="type de cache à utiliser lors de la recherche web. ['naif', 'semantic']"
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="affiche les métriques du journal des interactions (EV3) puis quitte, sans lancer Ollama",
    )
    parser.add_argument(
        "--profile",
        metavar="NOM",
        help="profil voyageur (M1) : config/profiles/NOM.json, ajouté au prompt système au lancement",
    )
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument(
        "--session",
        metavar="NOM",
        help="session nommée (M1) : sessions/NOM.json, reprise si elle existe, créée sinon",
    )
    resume.add_argument(
        "--continue",
        dest="resume",
        action="store_true",
        help="reprend la dernière conversation sauvegardée (M1)",
    )
    return parser


def make_output_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def fail(message: str) -> int:
    print(f"[erreur] {message}", file=sys.stderr)
    return 1


def describe_error(exc: BaseException) -> str:
    while getattr(exc, "exceptions", None):
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def agent_mode(client: OllamaClient, config: Config) -> bool:
    return bool(config.tools) and config.tool_model not in client.tools_unsupported


def run_turn(
    client: OllamaClient,
    config: Config,
    model: str,
    session: Session,
    tracker: LatencyTracker,
    journal: InteractionLog,
    text: str,
    started_at: float | None = None,
) -> None:
    guard = build_output_guard(config, session.messages)

    def show(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def on_token(token: str) -> None:
        if guard is None:
            show(token)
            return
        released, violation = guard.feed(token)
        if violation:
            raise GuardrailBlocked(violation)
        show(released)

    recorder = TurnRecorder(KIND_CHAT, model, text, started_at=started_at)
    sys.stdout.write("SensAI> ")
    sys.stdout.flush()
    try:
        result = client.chat_stream(model, session.messages_with(text), config.options, on_token)
        if guard is not None:
            released, violation = guard.finish(result.content)
            if violation:
                raise GuardrailBlocked(violation)
            show(released)
    except GuardrailBlocked as blocked:
        print(f"\n[sécurité] {blocked.violation.message}\n(ce tour n'est pas conservé dans l'historique)")
        journal.write(recorder, STATUS_BLOCKED, error=blocked.violation.reason())
        return
    except OllamaError as exc:
        print(f"\n[erreur] {exc}\n(ce tour n'est pas conservé dans l'historique)")
        journal.write(recorder, STATUS_ERROR, error=str(exc))
        return
    except KeyboardInterrupt:
        print("\n[interrompu] ce tour n'est pas conservé dans l'historique")
        journal.write(recorder, STATUS_INTERRUPTED)
        return

    print()
    journal.write(recorder, STATUS_OK, result.content, calls=[result])
    session.add_turn(text, result.content, model)
    tracker.record(result)
    print(tracker.format_turn(result))
    if started_at is not None:
        print(f"[temps total] {time.perf_counter() - started_at:.2f} s")


def format_call(name: str, arguments) -> str:
    if isinstance(arguments, dict):
        shown = ", ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in arguments.items())
    else:
        shown = str(arguments)
    return f"{name}({shown})"


def format_result(result) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > RESULT_PREVIEW_CHARS:
        text = text[:RESULT_PREVIEW_CHARS] + "…"
    return text


def report_latency(tracker: LatencyTracker, calls: list[ChatResult]) -> None:
    if not calls:
        return
    combined = combine_results(calls)
    tracker.record(combined, len(calls))
    print(tracker.format_turn(combined, len(calls)))


async def run_agent_turn(
    client: OllamaClient,
    config: Config,
    session: Session,
    tracker: LatencyTracker,
    journal: InteractionLog,
    text: str,
    started_at: float | None = None,
) -> None:
    calls: list[ChatResult] = []
    client.on_call = calls.append
    recorder = TurnRecorder(KIND_AGENT, config.tool_model, text, started_at=started_at)
    status, error, response = STATUS_OK, None, ""
    streamed: list[str] = []
    at_line_start = True
    need_prefix = True
    try:
        async for event in run_agent(config.tool_model, text, history=session):
            kind = event.get("type")
            if kind == "step":
                if not at_line_start:
                    print()
                print(f"[étape {event.get('n')}/{event.get('max')}]")
                at_line_start = True
                need_prefix = True
            elif kind == "token":
                if need_prefix:
                    sys.stdout.write("SensAI> ")
                    need_prefix = False
                content = event.get("content", "")
                sys.stdout.write(content)
                sys.stdout.flush()
                streamed.append(content)
                if content:
                    at_line_start = content.endswith("\n")
            elif kind in ("tool_call", "tool_result"):
                if not at_line_start:
                    print()
                if kind == "tool_call":
                    recorder.tool_called(event.get("name", ""), event.get("arguments", {}))
                    print(f"[outil] {format_call(event.get('name', ''), event.get('arguments', {}))}")
                else:
                    recorder.tool_returned(event.get("name", ""), event.get("result"))
                    print(f"[résultat] {format_result(event.get('result'))}")
                at_line_start = True
                need_prefix = True
            elif kind == "done_tool":
                response = event.get("content", "")
            elif kind == "guardrail":
                status, error = STATUS_BLOCKED, event_reason(event)
                if not at_line_start:
                    print()
                print(f"[sécurité] {event.get('message', 'Réponse bloquée.')}\n(ce tour n'est pas conservé dans l'historique)")
                return
            elif kind == "error":
                status = STATUS_MAX_ITERATIONS if len(calls) >= config.max_iterations else STATUS_ERROR
                error = event.get("message", "erreur inconnue")
                print(f"\n[erreur] {error}\n(ce tour n'est pas conservé dans l'historique)")
                return
    except KeyboardInterrupt:
        status = STATUS_INTERRUPTED
        print("\n[interrompu] ce tour n'est pas conservé dans l'historique")
        return
    except Exception as exc:
        status, error = STATUS_ERROR, describe_error(exc)
        print(f"\n[erreur] erreur inattendue ({error})\n(ce tour n'est pas conservé dans l'historique)")
        try:
            print("ERREUR: ", repr(exc))
        except:
            pass
        return
    finally:
        client.on_call = None
        journal.write(recorder, status, response or "".join(streamed), error, calls)

    if not at_line_start:
        print()
    report_latency(tracker, calls)
    if started_at is not None:
        print(f"[temps total] {time.perf_counter() - started_at:.2f} s")


def run_doc(target: str) -> None:
    document = current_document()
    if document is None:
        return

    markdown = render_markdown(document)
    if not target:
        print(markdown, end="")
        return
    content = file_content(target, markdown, document_to_json(document), "Document d'itinéraire", breaks=True)
    if content is not None and write_new_file(target, content):
        print(f"Document écrit dans {target}")


def current_document() -> dict | None:
    try:
        document = read_document()
    except TripError as exc:
        print(f"[erreur] {exc.message}")
        return None
    except (sqlite3.Error, OSError) as exc:
        print(f"[erreur] base du voyage inaccessible ({exc})")
        return None
    if document is None:
        print("(aucun document : décris d'abord ton voyage)")
    return document


def file_content(
    target: str, markdown: str, json_text: str, title: str, breaks: bool = False
) -> str | bytes | None:
    suffix = Path(target).suffix.lower()
    if suffix == ".json":
        return json_text
    if suffix != ".pdf":
        return markdown
    try:
        return to_pdf(markdown, title, breaks)
    except PdfError as exc:
        print(f"[erreur] PDF impossible ({exc})")
        return None


def write_new_file(target: str, content: str | bytes) -> bool:
    mode, encoding = ("xb", None) if isinstance(content, bytes) else ("x", "utf-8")
    try:
        with Path(target).open(mode, encoding=encoding) as file:
            file.write(content)
    except FileExistsError:
        print(f"[erreur] '{target}' existe déjà : choisis un autre nom de fichier")
        return False
    except OSError as exc:
        print(f"[erreur] écriture impossible ({exc})")
        return False
    return True


def run_export(session: Session, conversation: str, target: str) -> None:
    messages = session.conversation
    if not messages:
        print("(rien à exporter : la conversation est vide)")
        return
    markdown = to_markdown(messages)
    if not target:
        print(markdown, end="")
        return
    content = file_content(target, markdown, to_json(messages, conversation), "Conversation SensAI")
    if content is not None and write_new_file(target, content):
        print(f"Conversation exportée dans {target}")


def run_share(config: Config, session: Session, conversation: str, what: str) -> None:
    if config.shares_dir is None:
        print("[erreur] partage désactivé : 'shares_dir' est absent de config/config.json")
        return
    if what not in ("", SHARE_DOCUMENT):
        print(
            f"[erreur] {SHARE_COMMAND} partage la conversation, "
            f"{SHARE_COMMAND} {SHARE_DOCUMENT} le document d'itinéraire"
        )
        return
    if what == SHARE_DOCUMENT:
        document = current_document()
        if document is None:
            return
        subject, pronoun = "le document d'itinéraire", "le"
    else:
        messages = session.conversation
        if not messages:
            print("(rien à partager : la conversation est vide)")
            return
        subject, pronoun = "cette conversation", "la"
    try:
        answer = input(
            f"Créer un lien public vers {subject} ? Toute personne qui a le lien pourra {pronoun} lire pendant "
            f"{config.share_ttl_days} jour(s), sans se connecter. [o/N] "
        )
    except EOFError:
        answer = ""
    if answer.strip().lower() not in CONFIRM_ANSWERS:
        print("(annulé : aucun lien créé)")
        return
    try:
        if what == SHARE_DOCUMENT:
            share = create_document_share(config.shares_dir, conversation, document, config.share_ttl_days)
        else:
            share = create_share(config.shares_dir, conversation, messages, config.share_ttl_days)
    except ShareError as exc:
        print(f"[erreur] {exc}")
        return
    print(f"Lien de partage, valable jusqu'au {format_expiry(share)} :")
    print(share_url(config.share_host, config.share_port, share["token"]))
    serve_links(config)
    print("Pour le révoquer : /unshare LIEN")


def serve_links(config: Config) -> None:
    try:
        state = serve_in_background(config.share_host, config.share_port)
    except ShareServerError as exc:
        print(f"[avertissement] {exc} : lance python share.py pour que le lien s'ouvre")
        return
    if state == STARTED:
        print(
            f"Serveur des liens démarré sur http://{public_host(config.share_host)}:{config.share_port} : "
            "le lien s'ouvre tant que SensAI reste ouvert."
        )


def run_unshare(config: Config, conversation: str, link: str) -> None:
    if config.shares_dir is None:
        print("[erreur] partage désactivé : 'shares_dir' est absent de config/config.json")
        return
    try:
        if link:
            revoked = 1 if revoke_share(config.shares_dir, token_from(link)) else 0
        else:
            revoked = revoke_conversation(config.shares_dir, conversation)
    except ShareError as exc:
        print(f"[erreur] {exc}")
        return
    if link:
        print("Lien révoqué : il ne s'ouvre plus." if revoked else "[erreur] lien inconnu ou déjà révoqué")
    elif revoked:
        print(f"{revoked} lien(s) créé(s) dans cette conversation révoqué(s) : ils ne s'ouvrent plus.")
    else:
        print("(aucun lien actif créé dans cette conversation)")


def run_clear(session: Session) -> None:
    try:
        answer = input("Effacer toute la mémoire du voyage (voyage, dépenses, lieux, document) ? [o/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in CONFIRM_ANSWERS:
        print("(annulé : la mémoire du voyage est conservée)")
        return
    try:
        cleared = clear_all()
    except TripError as exc:
        print(f"[erreur] {exc.message}")
        return
    except (sqlite3.Error, OSError) as exc:
        print(f"[erreur] base du voyage inaccessible ({exc})")
        return
    print(f"Mémoire du voyage effacée ({cleared} voyage(s))." if cleared else "(la mémoire du voyage est déjà vide)")
    if session.turns:
        print("La conversation en cours parle encore de l'ancien voyage : /new repart d'une conversation vierge.")


def initial_prompt(client: OllamaClient, config: Config, profile_block: str) -> str:
    base_prompt = config.tool_system_prompt if agent_mode(client, config) else config.system_prompt
    return base_prompt + profile_block


def conversation_id(session_name: str | None) -> str:
    return session_name or uuid.uuid4().hex[:8]


def run_new(
    client: OllamaClient,
    config: Config,
    session: Session,
    journal: InteractionLog,
    profile_block: str,
) -> None:
    if session.turns == 0 and session.archived_turns == 0:
        print("(la conversation est déjà vierge)")
        return
    if session.path is not None:
        print(f"Conversation sauvegardée : {session.path} (pour la reprendre : --session {session.path.stem})")
    name = new_session_name(config.sessions_dir) if config.sessions_dir is not None else None
    path = config.sessions_dir / f"{name}.json" if name is not None else None
    session.restart(initial_prompt(client, config, profile_block), path)
    journal.session_id = conversation_id(name)
    print(describe_session(name, session) if name is not None else "[session] nouvelle conversation")

async def repl(
    client: OllamaClient,
    config: Config,
    session: Session,
    tracker: LatencyTracker,
    journal: InteractionLog,
    profile_block: str = "",
) -> None:
    while True:
        try:
            text = input("\nVous> ")
        except EOFError:
            print()
            return
        turn_started = time.perf_counter()
        try:
            text = validate_prompt(text, max_length=MAX_PROMPT_LENGTH)
        except PromptValidationError as exc:
            print(f"[sécurité] {exc}")
            continue
        if not text:
            print("(entrée vide : écris un message, ou /exit pour quitter)")
            continue
        if text.lower() in EXIT_COMMANDS:
            return
        if text.lower() in NEW_COMMANDS:
            run_new(client, config, session, journal, profile_block)
            continue
        command, _, argument = text.partition(" ")
        if command.lower() == DOC_COMMAND:
            run_doc(argument.strip())
            continue
        if command.lower() == EXPORT_COMMAND:
            run_export(session, journal.session_id, argument.strip())
            continue
        if command.lower() == SHARE_COMMAND:
            run_share(config, session, journal.session_id, argument.strip().lower())
            continue
        if command.lower() == UNSHARE_COMMAND:
            run_unshare(config, journal.session_id, argument.strip())
            continue
        if text.lower() == CLEAR_COMMAND:
            run_clear(session)
            continue
        if text.lower() == STATS_COMMAND:
            print(metrics.dashboard(config.interaction_log))
            continue
        if text.lower() == PROMPT_COMMAND:
            print(system_prompt_of(session))
            continue

        before_pii_check_text = text
        try:
            text = sanitize_user_message(text, config.pii_check_model)
        except ValueError as exc:
            print(f"[PII check] {exc}")
            continue
        if text != before_pii_check_text:
            print(f"[PII check detection] {text}")

        try:
            passed, scope = validate_message_scope(text, config.pii_check_model)
        except OllamaError as exc:
            print(f"[erreur] {exc}\n(ce tour n'est pas conservé dans l'historique)")
            continue
        if not passed:
            print(f"[scope check] {scope} - Demande hors sujet pour l'assistant de voyage.")
            continue

        security_elapsed = time.perf_counter() - turn_started
        print(f"[sécurité] {security_elapsed:.2f} s avant l'envoi au modèle")

        if agent_mode(client, config):
            await run_agent_turn(client, config, session, tracker, journal, text, started_at=turn_started)
            if not agent_mode(client, config):
                session.set_system_prompt(config.system_prompt + profile_block)
        else:
            run_turn(client, config, config.tool_model, session, tracker, journal, text, started_at=turn_started)


def system_prompt_of(session: Session) -> str:
    messages = session.messages
    if messages and messages[0]["role"] == "system":
        return messages[0]["content"]
    return "(aucun prompt système)"


def open_session(config: Config, name: str | None, resume: bool, system_prompt: str) -> tuple[Session, str | None]:
    if config.sessions_dir is None:
        if name is not None or resume:
            raise SessionError("Aucun dossier de sessions : 'sessions_dir' est absent de config/config.json.")
        return Session(system_prompt), None
    if name is None:
        name = (latest_session_name(config.sessions_dir) if resume else None) or new_session_name(config.sessions_dir)
    elif not NAME_PATTERN.match(name):
        raise SessionError(f"Nom de session invalide : '{name}' (lettres, chiffres, '.', '_' et '-' uniquement).")
    session = Session.load(config.sessions_dir / f"{name}.json", system_prompt, config.max_reloaded_turns)
    return session, name


def describe_session(name: str, session: Session) -> str:
    if session.turns == 0 and session.archived_turns == 0:
        return (
            f"[session] '{name}' : nouvelle session, sauvegardée dans {session.path} "
            f"(pour la reprendre : --continue ou --session {name})"
        )
    text = f"[session] '{name}' reprise : {session.turns} tour(s) rechargé(s)"
    if session.archived_turns:
        text += f", {session.archived_turns} plus ancien(s) gardé(s) dans le fichier mais non rechargé(s)"
    return text


async def main(argv: list[str] | None = None) -> int:
    make_output_safe()
    args = build_parser().parse_args(argv)

    try:
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            return fail(str(exc))

        if args.stats:
            print(metrics.dashboard(config.interaction_log))
            return 0

        if args.model is not None:
            model = args.model.strip()
            if not model:
                return fail("Le nom du modèle (--model) est vide.")
            config = replace(config, tool_model=model, response_model=model)
        if args.cache is not None:
            cache = args.cache.strip()
            if not cache:
                return fail("Le mode de cache (--cache) est vide.")
            if cache not in ["naif", "semantic"]:
                return fail("Le mode de cache (--cache) doit être 'naif' ou 'semantic'")
            config = replace(config, cache_mode=cache)
        set_active_config(config)

        profile_block = ""
        if args.profile is not None:
            try:
                profile_block = render_profile(load_profile(config.profiles_dir, args.profile.strip()))
            except ProfileError as exc:
                return fail(str(exc))

        client = OllamaClient(config.ollama_host, config.connect_timeout_s, config.read_timeout_s)
        set_client(client)
        try:
            for model_name in dict.fromkeys((config.tool_model, config.response_model)):
                client.ensure_model(model_name)
        except OllamaError as exc:
            return fail(str(exc))

        for warning in config.warnings:
            print(f"[avertissement] {warning}", file=sys.stderr)

        try:
            session, session_name = open_session(
                config,
                args.session.strip() if args.session is not None else None,
                args.resume,
                initial_prompt(client, config, profile_block),
            )
        except SessionError as exc:
            return fail(str(exc))
        if args.profile is not None:
            session.profile = args.profile.strip()
        tracker = LatencyTracker(config.tool_model, config.latency_log)
        journal = InteractionLog(config.interaction_log, conversation_id(session_name), config.tool_prompt_version)
        if config.tool_model == config.response_model:
            models = f"modèle : {config.tool_model}"
        else:
            models = f"modèles : outils {config.tool_model}, réponse {config.response_model}"
        print(
            f"SensAI | {models} | /new, /clear, /doc, /export, /share, /unshare, /stats, /prompt, /exit "
            "ou Ctrl+D pour quitter"
        )
        if args.profile is not None:
            print(f"[profil] '{args.profile.strip()}' appliqué au prompt système (/prompt pour le voir)")
        if session_name is not None:
            if args.resume and session.turns == 0 and session.archived_turns == 0:
                print("[session] aucune conversation sauvegardée à reprendre")
            print(describe_session(session_name, session))

        try:
            await repl(client, config, session, tracker, journal, profile_block)
        except KeyboardInterrupt:
            print()

        summary = tracker.summary()
        if summary:
            print(summary)
        if session.path is not None and session.turns > 0:
            print(f"Conversation sauvegardée : {session.path} (pour la reprendre : --continue)")
        if background_running():
            print("[partage] le serveur des liens s'arrête avec SensAI : lance python share.py pour qu'ils s'ouvrent encore")
        return 0
    finally:
        stop_background()
        await close_mcp()
