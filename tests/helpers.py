# Outils de test partages : faux client Ollama (aucun Ollama n'est necessaire) et mise en place
# de la config active. Les tests utilisent unittest (stdlib uniquement).

from __future__ import annotations

import asyncio
import contextlib
import io
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from src import agent, model_funcs
from src.config import load_config, set_active_config
from src.ollama_client import ChatResult


# Reponse scriptee : texte simple, ou appel(s) d'outil natif(s).
def reply(content: str = "", calls: list[tuple[str, object]] | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {"function": {"name": name, "arguments": arguments}} for name, arguments in calls
        ]
    return message


def call(name: str, arguments: object) -> dict:
    return reply("", [(name, arguments)])


# Faux OllamaClient : rejoue une liste de reponses (un element par appel au modele).
# Un element peut etre une exception : elle est levee a l'appel (OllamaError, KeyboardInterrupt...).
class FakeClient:
    def __init__(self, script: list, drop_tools_on_first_call: bool = False) -> None:
        self.script = list(script)
        self.requests: list[dict] = []
        self.on_call = None
        self.tools_unsupported: set[str] = set()
        self.think_unsupported: set[str] = set()
        self.installed: list[str] | None = None  # None : tous les modeles sont "installes"
        self.checked_models: list[str] = []
        self._drop_tools = drop_tools_on_first_call

    def ensure_model(self, model: str) -> None:
        from src.ollama_client import OllamaError

        self.checked_models.append(model)
        if self.installed is not None and model not in self.installed:
            raise OllamaError(f"Modèle '{model}' introuvable dans Ollama.")

    def chat_events(self, model, messages, options=None, tools=None, think=None, format=None):
        self.requests.append(
            {
                "model": model,
                "messages": [dict(m) for m in messages],
                "options": options,
                "tools": None if model in self.tools_unsupported else tools,
                "think": think,
                "format": format,
            }
        )
        if self._drop_tools and tools:
            self._drop_tools = False
            self.tools_unsupported.add(model)
            self.requests[-1]["tools"] = None
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        content = step.get("content", "")
        half = len(content) // 2
        for chunk in (content[:half], content[half:]):
            if chunk:
                yield {"type": "token", "content": chunk}
        result = ChatResult(content=content, total_s=0.5, ttft_s=0.1, eval_count=10, load_s=None)
        if self.on_call is not None:
            self.on_call(result)
        yield {"type": "done", "message": step, "result": result}

    def chat_stream(self, model, messages, options, on_token):
        for event in self.chat_events(model, messages, options):
            if event["type"] == "token":
                on_token(event["content"])
            else:
                return event["result"]


# Objet qui se comporte comme un module d'outil (expose tool(args) -> dict).
def fake_tool(function):
    return SimpleNamespace(tool=function)


# Faux client MCP : agent.execute_tool ouvre un Client MCP par appel d'outil (list_tools puis
# call_tool). tools : {nom d'outil: fonction(args) -> resultat}.
class FakeMcpClient:
    def __init__(self, tools: dict) -> None:
        self.tools = tools

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.tools])

    async def call_tool(self, name, arguments):
        return self.tools[name](arguments)


# Le client MCP de l'agent est partage entre les appels (agent.connect_mcp le garde en memoire) :
# sans remise a zero, le faux client d'un test resterait actif dans le test suivant.
def reset_mcp() -> None:
    agent.mcp_client = None
    agent.mcp_client_context = None
    agent.server = None


# Remplace le client MCP de l'agent par un faux qui expose `tools` (aucun serveur n'est lance).
@contextlib.contextmanager
def patch_mcp(tools: dict):
    reset_mcp()
    try:
        with mock.patch.object(agent, "Client", side_effect=lambda server: FakeMcpClient(tools)):
            yield
    finally:
        reset_mcp()


# Joue une coroutine jusqu'au bout (run_agent et cli.main sont asynchrones).
def run_async(coroutine):
    return asyncio.run(coroutine)


# Base des tests de l'agent : config reelle (config/), plafond d'iterations bas, faux client.
class AgentTestCase(unittest.TestCase):
    max_iterations = 3

    def setUp(self) -> None:
        self.config = replace(load_config(), max_iterations=self.max_iterations)
        set_active_config(self.config)
        agent.HISTORY.clear()
        self.client: FakeClient | None = None

    def tearDown(self) -> None:
        set_active_config(None)
        model_funcs.set_client(None)
        agent.HISTORY.clear()
        reset_mcp()

    def use(self, script: list, **kwargs) -> FakeClient:
        self.client = FakeClient(script, **kwargs)
        model_funcs.set_client(self.client)
        return self.client

    # Outils MCP simules pour la duree d'un `with` : self.mcp({"weather": fonction}).
    def mcp(self, tools: dict):
        return patch_mcp(tools)

    # Joue run_agent jusqu'au bout en masquant le message de retry affiche sur stdout.
    def play(self, *args, **kwargs) -> list[dict]:
        async def collect() -> list[dict]:
            return [
                event
                async for event in agent.run_agent(self.config.tool_model, *args, **kwargs)
            ]

        with contextlib.redirect_stdout(io.StringIO()):
            return run_async(collect())
