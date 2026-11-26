# Harnais partage par les tests qui pilotent le programme complet (cli.main) : faux Ollama, saisies scriptees,
# faux serveur MCP au format reel. Ce n'est pas un test : unittest ne le decouvre pas (le nom ne commence pas par test_).

from __future__ import annotations

import contextlib
import io
import json
import unittest
from dataclasses import replace
from unittest import mock

from mcp.types import CallToolResult, TextContent

from src import agent, cli, model_funcs
from src.config import load_config as real_load_config
from src.config import set_active_config
from tests.helpers import FakeClient, patch_mcp, reset_mcp, run_async


# Resultat d'outil tel que le renvoie un vrai serveur MCP : un CallToolResult avec un bloc de texte.
def mcp_result(value, is_error: bool = False) -> CallToolResult:
    text = value if isinstance(value, str) else json.dumps(value)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


# Faux outil meteo, au format reel.
WEATHER_TOOL = {"weather": lambda args: mcp_result({"temp": 20, "city": args["city"]})}


class CliTestCase(unittest.TestCase):
    scope_check = {"return_value": (True, "IN_SCOPE")}

    def tearDown(self) -> None:
        set_active_config(None)
        model_funcs.set_client(None)
        agent.HISTORY.clear()
        reset_mcp()

    # Lance cli.main avec un faux Ollama et des saisies scriptees ; renvoie (code, stdout, stderr, faux client).
    #   inputs : ce que l'utilisateur tape (la fin de saisie, Ctrl+D, est ajoutee automatiquement)
    #   script : les reponses successives du faux modele (voir tests/helpers.py : reply, call)
    #   config_overrides : champs de Config a remplacer (sessions_dir, profiles_dir, max_iterations...)
    # Par defaut, aucun journal ni session n'est ecrit : les tests n'ecrivent rien dans logs/ ni dans sessions/.
    def run_cli(self, inputs, script, argv=(), interaction_log=None, latency_log=None, mcp=None,
                config_overrides=None, **fake_kwargs):
        fake = FakeClient(script, **fake_kwargs)
        return self.run_cli_with(fake, inputs, argv, interaction_log, latency_log, mcp, config_overrides)

    def run_cli_with(self, fake, inputs, argv=(), interaction_log=None, latency_log=None, mcp=None,
                     config_overrides=None):
        out, err = io.StringIO(), io.StringIO()
        reset_mcp()

        def load(path):
            return replace(
                real_load_config(path),
                latency_log=latency_log,
                interaction_log=interaction_log,
                **{"sessions_dir": None, **(config_overrides or {})},
            )

        with mock.patch("src.cli.OllamaClient", return_value=fake), \
                mock.patch("src.cli.sanitize_user_message", side_effect=lambda text, model=None: text), \
                mock.patch("src.cli.validate_message_scope", **self.scope_check), \
                mock.patch("src.cli.load_config", side_effect=load), \
                mock.patch("builtins.input", side_effect=[*inputs, EOFError()]), \
                (patch_mcp(WEATHER_TOOL) if mcp is None else mcp), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_async(cli.main(list(argv)))
        self.assertNotIn("Traceback", out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue(), fake
