import asyncio
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import agent
from src.agent import close_mcp, connect_mcp, run_agent
from src.config import active_config, set_active_config
from src.export import document_to_json, to_json, to_markdown
from src.injection_patterns import PromptValidationError, validate_prompt
from src.latency import combine_results
from src.model_funcs import get_client, sanitize_user_message, validate_message_scope
from src.ollama_client import OllamaError
from src.pdf_export import PdfError, to_pdf
from src.session import Session, SessionError, new_session_name
from src.share_server import ShareServerError, serve_in_background, stop_background
from src.sharing import ShareError, create_document_share, create_share, format_expiry, revoke_share, share_url
from src.tools.trip_db import TripError, clear_all
from src.tools.trip_document import read_document, render_markdown
from src.user_profile import ProfileError, available_profiles, load_profile, render_profile

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
RECOMMENDED_MODELS = ["qwen3:8b"]
SUGGESTED_MODELS = ["mistral-nemo:12b", "qwen2.5:7b", "mistral:latest", "granite3.1-moe:latest", "llama3.1:8b"]
CACHE_MODES = ["naif", "semantic"]


tools_ready = asyncio.Event()
restart_tools = asyncio.Event()


async def keep_tools_server():
    try:
        while True:
            await connect_mcp()
            tools_ready.set()
            await restart_tools.wait()
            restart_tools.clear()
            await close_mcp()
            agent.server = None
    finally:
        await close_mcp()


@asynccontextmanager
async def lifespan(app):
    tools_task = asyncio.create_task(keep_tools_server())
    await tools_ready.wait()
    yield
    tools_task.cancel()
    await asyncio.gather(tools_task, return_exceptions=True)
    stop_background()


app = FastAPI(lifespan=lifespan)
conversations = {}
generation_lock = threading.Lock()


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    model: str
    profile: str


class InstallRequest(BaseModel):
    model: str


class CacheRequest(BaseModel):
    mode: str


def find_session(conversation_id):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return conversations[conversation_id]


def to_sse(event):
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/warnings")
def warnings():
    return {"warnings": list(active_config().warnings)}


@app.get("/api/models")
def models():
    try:
        installed = get_client().list_models()
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {
        "default": active_config().tool_model,
        "recommended": RECOMMENDED_MODELS,
        "suggested": SUGGESTED_MODELS,
        "installed": installed,
    }


def pull_model(model):
    sizes = {}
    downloaded = {}
    percent = 0
    try:
        response = requests.post(f"{get_client().host}/api/pull", json={"model": model}, stream=True, timeout=(5, 300))
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            status = json.loads(line)
            if "error" in status:
                yield to_sse({"type": "error", "message": status["error"]})
                return
            if status.get("total"):
                sizes[status["status"]] = status["total"]
                downloaded[status["status"]] = status.get("completed", 0)
                percent = max(percent, round(100 * sum(downloaded.values()) / sum(sizes.values())))
                yield to_sse({"type": "progress", "percent": percent})
        yield to_sse({"type": "done"})
    except requests.RequestException as exc:
        yield to_sse({"type": "error", "message": f"Installation impossible ({exc})"})


@app.post("/api/models/install")
def install_model(request: InstallRequest):
    if request.model not in RECOMMENDED_MODELS + SUGGESTED_MODELS:
        raise HTTPException(status_code=400, detail="Ce modèle ne fait pas partie des modèles proposés")
    return StreamingResponse(pull_model(request.model), media_type="text/event-stream")


@app.get("/api/cache")
def cache():
    return {"mode": active_config().cache_mode}


@app.post("/api/cache")
async def change_cache(request: CacheRequest):
    if request.mode not in CACHE_MODES:
        raise HTTPException(status_code=400, detail="Le mode de cache doit être 'naif' ou 'semantic'")
    if not generation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Une réponse est déjà en cours")
    try:
        set_active_config(replace(active_config(), cache_mode=request.mode))
        tools_ready.clear()
        restart_tools.set()
        await tools_ready.wait()
    finally:
        generation_lock.release()
    return {"mode": request.mode}


@app.get("/api/profiles")
def profiles():
    profiles_dir = active_config().profiles_dir
    if profiles_dir is None:
        return {"profiles": []}
    return {"profiles": available_profiles(profiles_dir)}


def system_prompt_with_profile(profile):
    config = active_config()
    if not profile:
        return config.tool_system_prompt
    return config.tool_system_prompt + render_profile(load_profile(config.profiles_dir, profile))


@app.get("/api/prompt")
def prompt(profile: str = ""):
    try:
        return {"prompt": system_prompt_with_profile(profile)}
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/conversations")
def create_conversation():
    config = active_config()
    if config.sessions_dir is None:
        conversation_id = uuid.uuid4().hex
        path = None
    else:
        conversation_id = new_session_name(config.sessions_dir, reserved=conversations)
        path = config.sessions_dir / f"{conversation_id}.json"
    conversations[conversation_id] = Session(config.tool_system_prompt, path)
    return {"conversation_id": conversation_id}


def saved_session_paths():
    sessions_dir = active_config().sessions_dir
    if sessions_dir is None or not sessions_dir.is_dir():
        return []
    return sorted(sessions_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)


def first_user_message(messages):
    return next((message["content"] for message in messages if message["role"] == "user"), "")


@app.get("/api/conversations")
def list_conversations():
    saved = []
    for path in saved_session_paths():
        try:
            session = Session.load(path, "")
        except SessionError:
            continue
        saved.append({
            "id": path.stem,
            "title": first_user_message(session.messages),
            "updated": time.strftime("%d/%m %H:%M", time.localtime(path.stat().st_mtime)),
        })
    return {"conversations": saved}


@app.get("/api/conversations/{conversation_id}")
def open_conversation(conversation_id: str):
    path = next((path for path in saved_session_paths() if path.stem == conversation_id), None)
    if path is None:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    config = active_config()
    try:
        session = Session.load(path, config.tool_system_prompt, config.max_reloaded_turns)
    except SessionError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    conversations[conversation_id] = session
    return {
        "messages": [message for message in session.messages if message["role"] != "system"],
        "turns": session.turns,
        "archived_turns": session.archived_turns,
    }


async def let_server_notice_disconnect():
    await asyncio.sleep(0)


async def stream_agent_turn(session, message, model, started_at: float | None = None):
    if not generation_lock.acquire(blocking=False):
        yield to_sse({"type": "error", "message": "Une réponse est déjà en cours, attends qu'elle se termine."})
        return

    started_at = time.perf_counter() if started_at is None else started_at
    client = get_client()
    calls = []
    client.on_call = calls.append
    try:
        async for event in run_agent(model, message, history=session):
            yield to_sse(event)
            await let_server_notice_disconnect()
            if event.get("type") in ("error", "guardrail"):
                return
        if model in client.tools_unsupported:
            yield to_sse({"type": "no_tools"})
        if calls:
            total = combine_results(calls)
            elapsed = time.perf_counter() - started_at
            yield to_sse({
                "type": "stats",
                "latency_s": round(elapsed, 2),
                "first_token_s": None if total.ttft_s is None else round(total.ttft_s, 2),
                "tokens": total.eval_count,
                "calls": len(calls),
            })
    except Exception as exc:
        yield to_sse({"type": "error", "message": f"Erreur inattendue ({type(exc).__name__}: {exc})"})
    finally:
        client.on_call = None
        generation_lock.release()


@app.post("/api/chat")
def chat(request: ChatRequest):
    session = find_session(request.conversation_id)
    turn_started = time.perf_counter()
    try:
        message = validate_prompt(request.message)
    except PromptValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not message:
        raise HTTPException(status_code=400, detail="Message vide")
    try:
        message = sanitize_user_message(message, active_config().pii_check_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        passed, scope = validate_message_scope(message, active_config().pii_check_model)
    except OllamaError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not passed:
        raise HTTPException(status_code=400, detail=f"{scope} - Demande hors sujet pour l'assistant de voyage.")
    try:
        session.set_system_prompt(system_prompt_with_profile(request.profile))
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session.profile = request.profile or None
    return StreamingResponse(stream_agent_turn(session, message, request.model, started_at=turn_started), media_type="text/event-stream")


def current_document():
    try:
        trip_document = read_document()
    except TripError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    if trip_document is None:
        raise HTTPException(status_code=404, detail="Aucun document : décris d'abord ton voyage")
    return trip_document


def download(content, media_type, file_name):
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{file_name}"'})


def export_file(markdown, json_text, title, file_name, format, breaks=False):
    if format == "json":
        return download(json_text, "application/json", f"{file_name}.json")
    if format == "pdf":
        try:
            return download(to_pdf(markdown, title, breaks), "application/pdf", f"{file_name}.pdf")
        except PdfError as exc:
            raise HTTPException(status_code=500, detail=f"PDF impossible ({exc})")
    return download(markdown, "text/markdown; charset=utf-8", f"{file_name}.md")


@app.get("/api/document")
def document():
    return {"markdown": render_markdown(current_document())}


@app.get("/api/conversations/{conversation_id}/export")
def export_conversation(conversation_id: str, format: str = "md"):
    messages = find_session(conversation_id).conversation
    if not messages:
        raise HTTPException(status_code=400, detail="Rien à exporter : la conversation est vide")
    markdown = to_markdown(messages)
    json_text = to_json(messages, conversation_id)
    return export_file(markdown, json_text, "Conversation SensAI", f"conversation_{conversation_id}", format)


@app.get("/api/document/export")
def export_document(format: str = "md"):
    trip_document = current_document()
    markdown = render_markdown(trip_document)
    json_text = document_to_json(trip_document)
    return export_file(markdown, json_text, "Document d'itinéraire", "itineraire", format, breaks=True)


def shares_dir():
    directory = active_config().shares_dir
    if directory is None:
        raise HTTPException(status_code=400, detail="Partage désactivé : 'shares_dir' est absent de config/config.json")
    return directory


def share_link(share):
    config = active_config()
    warning = None
    try:
        serve_in_background(config.share_host, config.share_port)
    except ShareServerError as exc:
        warning = f"{exc} : lance python share.py pour que le lien s'ouvre"
    return {
        "url": share_url(config.share_host, config.share_port, share["token"]),
        "token": share["token"],
        "expires": format_expiry(share),
        "warning": warning,
    }


@app.post("/api/conversations/{conversation_id}/share")
def share_conversation(conversation_id: str):
    messages = find_session(conversation_id).conversation
    if not messages:
        raise HTTPException(status_code=400, detail="Rien à partager : la conversation est vide")
    try:
        share = create_share(shares_dir(), conversation_id, messages, active_config().share_ttl_days)
    except ShareError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return share_link(share)


@app.post("/api/document/share")
def share_document():
    trip_document = current_document()
    try:
        share = create_document_share(shares_dir(), None, trip_document, active_config().share_ttl_days)
    except ShareError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return share_link(share)


@app.delete("/api/shares/{token}")
def unshare(token: str):
    try:
        revoked = revoke_share(shares_dir(), token)
    except ShareError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not revoked:
        raise HTTPException(status_code=404, detail="Lien introuvable ou déjà révoqué")
    return {"revoked": True}


@app.delete("/api/trip")
def clear_trip():
    try:
        cleared = clear_all()
    except TripError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
    return {"cleared": cleared}


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
