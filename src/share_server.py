import http.client
import socket
import threading
import time
import urllib.error
import urllib.request

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.config import active_config
from src.sharing import load_share, not_found_page, render_page

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow",
    "Cache-Control": "no-store",
}
STARTED = "started"
RUNNING = "running"
EXTERNAL = "external"
OTHER = "other"
STARTUP_TIMEOUT_S = 5.0
PROBE_TIMEOUT_S = 1.0
NOT_FOUND_TITLE = "Lien indisponible · SensAI"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_background: tuple[uvicorn.Server, threading.Thread] | None = None


class ShareServerError(Exception):
    pass


@app.get("/share/{token}", response_class=HTMLResponse)
def shared_page(token: str):
    shares_dir = active_config().shares_dir
    share = load_share(shares_dir, token) if shares_dir is not None else None
    page = None
    if share is not None:
        try:
            page = render_page(share)
        except (KeyError, TypeError, ValueError, AttributeError):
            page = None
    if page is None:
        return HTMLResponse(not_found_page(), status_code=404, headers=SECURITY_HEADERS)
    return HTMLResponse(page, headers=SECURITY_HEADERS)


def serve_in_background(host: str, port: int) -> str:
    global _background
    if _background is not None and _background[1].is_alive():
        return RUNNING
    if not _port_free(host, port):
        listener = _listener(host, port)
        if listener == EXTERNAL:
            return EXTERNAL
        if listener == OTHER:
            raise ShareServerError(
                f"le port {port} est déjà pris par un autre programme (share_port dans config/config.json)"
            )
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_config=None, log_level="critical", access_log=False)
    )
    thread = threading.Thread(target=server.run, name="sensai-share-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        raise ShareServerError(f"le serveur des liens n'a pas pu démarrer sur le port {port}")
    _background = (server, thread)
    return STARTED


def background_running() -> bool:
    return _background is not None and _background[1].is_alive()


def stop_background() -> bool:
    global _background
    if _background is None:
        return False
    server, thread = _background
    _background = None
    server.should_exit = True
    thread.join(STARTUP_TIMEOUT_S)
    return True


def _probe_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "") else host


def _port_free(host: str, port: int) -> bool:
    probe = socket.socket()
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _listener(host: str, port: int) -> str | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        opener.open(f"http://{_probe_host(host)}:{port}/share/-", timeout=PROBE_TIMEOUT_S).close()
    except urllib.error.HTTPError as response:
        page = response.read().decode("utf-8", "replace")
        return EXTERNAL if response.code == 404 and NOT_FOUND_TITLE in page else OTHER
    except http.client.HTTPException:
        return OTHER
    except (OSError, ValueError):
        return None
    return OTHER
