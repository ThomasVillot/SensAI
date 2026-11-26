
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterator

import requests

from .session import MESSAGE_ANNOTATIONS


class OllamaError(Exception):
    pass


@dataclass
class ChatResult:
    content: str
    total_s: float
    ttft_s: float | None
    eval_count: int | None
    load_s: float | None


def _warn_stderr(message: str) -> None:
    print(f"[avertissement] {message}", file=sys.stderr)


class OllamaClient:
    def __init__(
        self,
        host: str,
        connect_timeout_s: float,
        read_timeout_s: float,
        warn: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.read_timeout_s = read_timeout_s
        self.timeout = (connect_timeout_s, read_timeout_s)
        self.warn = warn or _warn_stderr
        self.tools_unsupported: set[str] = set()
        self.think_unsupported: set[str] = set()
        self.on_call: Callable[[ChatResult], None] | None = None

    def _unreachable(self) -> OllamaError:
        return OllamaError(
            f"Impossible de joindre Ollama sur {self.host}. "
            "Vérifie qu'il est lancé (ollama serve)."
        )

    def _model_missing(self, model: str, installed: list[str] | None = None) -> OllamaError:
        message = f"Modèle '{model}' introuvable dans Ollama. Installe-le avec : ollama pull {model}"
        if installed:
            message += f"\nModèles installés : {', '.join(installed)}"
        return OllamaError(message)

    def list_models(self) -> list[str]:
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=self.timeout)
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except requests.ReadTimeout:
            raise OllamaError(
                f"Ollama n'a pas répondu dans le délai imparti ({self.read_timeout_s:g} s)."
            ) from None
        except (requests.RequestException, OSError):
            raise self._unreachable() from None
        except (ValueError, KeyError, TypeError, AttributeError):
            raise OllamaError("Réponse illisible reçue d'Ollama (/api/tags).") from None

    def ensure_model(self, model: str) -> None:
        installed = self.list_models()
        if model not in installed and f"{model}:latest" not in installed:
            raise self._model_missing(model, installed)

    def _http_error(self, response: requests.Response, model: str) -> OllamaError:
        if response.status_code == 404:
            return self._model_missing(model)
        detail = self._error_detail(response)
        return OllamaError(
            f"Ollama a répondu HTTP {response.status_code} : {detail or 'erreur inconnue'}"
        )

    def _error_detail(self, response: requests.Response) -> str:
        try:
            body = response.json()
            if isinstance(body, dict):
                return str(body.get("error", ""))
        except ValueError:
            return response.text[:200]
        return ""

    def _drop_rejected_feature(self, detail: str, payload: dict, model: str) -> bool:
        text = detail.lower()
        if "tools" in payload and "does not support tools" in text:
            self.tools_unsupported.add(model)
            self.warn(f"le modèle '{model}' ne gère pas les outils : réponse en chat simple, sans outils.")
            del payload["tools"]
            return True
        if "think" in payload and "does not support thinking" in text:
            self.think_unsupported.add(model)
            self.warn(f"le modèle '{model}' n'accepte pas le paramètre 'think' : appel sans ce paramètre.")
            del payload["think"]
            return True
        return False

    def chat_events(
        self,
        model: str,
        messages: list[dict],
        options: dict | None = None,
        tools: list[dict] | None = None,
        think: bool | None = None,
        format: dict | None = None,
    ) -> Iterator[dict]:
        sent = [
            {key: value for key, value in message.items() if key not in MESSAGE_ANNOTATIONS}
            for message in messages
        ]
        payload: dict = {"model": model, "messages": sent, "stream": True}
        if options:
            payload["options"] = options
        if tools and model not in self.tools_unsupported:
            payload["tools"] = tools
        if think is not None and model not in self.think_unsupported:
            payload["think"] = think
        if format:
            payload["format"] = format

        started = time.perf_counter()
        first_token_s: float | None = None
        parts: list[str] = []
        tool_calls: list[dict] = []
        final: dict | None = None
        received = False

        try:
            for _ in range(3):
                with requests.post(
                    f"{self.host}/api/chat", json=payload, stream=True, timeout=self.timeout
                ) as response:
                    if response.status_code != 200:
                        if response.status_code == 400 and self._drop_rejected_feature(
                            self._error_detail(response), payload, model
                        ):
                            continue
                        raise self._http_error(response, model)
                    for line in response.iter_lines():
                        if not line:
                            continue
                        received = True
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise OllamaError(f"Erreur renvoyée par Ollama : {chunk['error']}")
                        message = chunk.get("message") or {}
                        token = message.get("content") or ""
                        if token:
                            if first_token_s is None:
                                first_token_s = time.perf_counter() - started
                            parts.append(token)
                            yield {"type": "token", "content": token}
                        if message.get("tool_calls"):
                            tool_calls.extend(message["tool_calls"])
                        if chunk.get("done"):
                            final = chunk
                            break
                break
        except requests.ReadTimeout:
            raise OllamaError(
                f"Ollama n'a pas répondu dans le délai imparti ({self.read_timeout_s:g} s)."
            ) from None
        except (requests.RequestException, OSError):
            if received:
                raise OllamaError(
                    "Connexion avec Ollama perdue pendant la réponse."
                ) from None
            raise self._unreachable() from None
        except (ValueError, AttributeError):
            raise OllamaError("Réponse illisible reçue d'Ollama.") from None

        if final is None:
            raise OllamaError("Réponse interrompue avant la fin (connexion coupée ?).")

        total_s = time.perf_counter() - started
        load_ns = final.get("load_duration")
        result = ChatResult(
            content="".join(parts),
            total_s=total_s,
            ttft_s=first_token_s,
            eval_count=final.get("eval_count"),
            load_s=(load_ns / 1e9) if isinstance(load_ns, (int, float)) else None,
        )
        reply: dict = {"role": "assistant", "content": result.content}
        if tool_calls:
            reply["tool_calls"] = tool_calls
        if self.on_call is not None:
            self.on_call(result)
        yield {"type": "done", "message": reply, "result": result}

    def chat_stream(
        self,
        model: str,
        messages: list[dict],
        options: dict,
        on_token: Callable[[str], None],
    ) -> ChatResult:
        for event in self.chat_events(model, messages, options):
            if event["type"] == "token":
                on_token(event["content"])
            else:
                return event["result"]
        raise OllamaError("Réponse interrompue avant la fin (connexion coupée ?).")
