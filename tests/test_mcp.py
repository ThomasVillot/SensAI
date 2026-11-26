# T1 : MCP. Ce fichier teste le lien entre l'agent et ses outils, a travers le protocole MCP.
#
#   1. Declaration : les outils que voit le modele (config/tools.yaml) sont ceux du serveur MCP de SensAI.
#   2. Appel       : l'agent execute l'outil demande par le modele, puis lui renvoie le resultat.
#   3. Resultat    : un resultat MCP est converti en dict, en texte ou en liste avant d'etre rendu au modele.
#   4. Erreurs     : outil inconnu, arguments invalides, outil en panne, serveur arrete : jamais de plantage.
#   5. Serveur tiers : l'agent se branche sur un serveur MCP qui n'est pas celui de SensAI.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.
#
# Non teste ici : le choix du bon outil par le vrai modele (mesure dans bench/), l'appel reel des outils
# (reseau et cles API) et demo_external_mcp.py (necessite npx et le reseau).
#
# Deux sortes de tests : la plupart utilisent un faux modele et un faux client MCP (rapides) ; les classes
# RealServerTests et ThirdPartyServerTests lancent de vrais serveurs MCP dans un sous-processus.

import ast
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from mcp import StdioServerParameters
from mcp.types import CallToolResult, TextContent

from src import agent
from src.config import ROOT
from src.interaction_log import tool_failure
from src.session import Session
from tests.helpers import AgentTestCase, call, reply, run_async

THIRD_PARTY_SERVER = Path(__file__).with_name("third_party_mcp_server.py")

# Schemas (au format de config/tools.yaml) des outils du serveur tiers : le modele ne les voit que si on les declare.
THIRD_PARTY_TOOLS = {
    "echo": {"properties": {"text": {"type": "string"}}, "required": ["text"]},
    "shout": {"properties": {"text": {"type": "string"}}, "required": ["text"]},
    "boom": {"properties": {}, "required": []},
}

FLIGHT_ARGUMENTS = {
    "airport_departure_id": "CDG",
    "airport_arrival_id": "LIS",
    "outbound_date": "2026-12-01",
    "currency": "EUR",
    "round_trip": False,
    "sort_by": "price",
}


# Resultat d'outil tel que le renvoie un vrai serveur MCP : un CallToolResult avec un bloc de texte.
def mcp_result(value, is_error: bool = False) -> CallToolResult:
    text = value if isinstance(value, str) else json.dumps(value)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


def declared_tools(config) -> dict:
    return {tool["function"]["name"]: tool["function"] for tool in config.tools}


# 1. Declaration ------------------------------------------------------------------------------------------------

class ToolDeclarationTests(AgentTestCase):
    # Teste : chaque outil de config/tools.yaml (celui que le modele voit) a un nom, une description et des
    # parametres de type objet. Sans cela, le modele ne saurait pas quand ni comment appeler l'outil.
    def test_every_declared_tool_has_a_name_a_description_and_object_parameters(self):
        for tool in self.config.tools:
            function = tool["function"]
            with self.subTest(tool=function.get("name")):
                self.assertEqual(tool["type"], "function")
                self.assertTrue(function["name"])
                self.assertTrue(function["description"].strip())
                self.assertEqual(function["parameters"]["type"], "object")

    # Teste : deux outils ne portent jamais le meme nom dans config/tools.yaml (le modele appelle par le nom).
    def test_tool_names_are_unique(self):
        names = [tool["function"]["name"] for tool in self.config.tools]
        self.assertEqual(len(names), len(set(names)))

    # Teste : le serveur MCP de SensAI (src/tools/mcp_server.py) enregistre exactement les outils declares dans
    # config/tools.yaml : aucun outil declare mais absent du serveur, aucun outil du serveur cache au modele.
    # Lecture du code source uniquement : rien n'est lance.
    def test_mcp_server_source_registers_exactly_the_declared_tools(self):
        source = (ROOT / "src" / "tools" / "mcp_server.py").read_text(encoding="utf-8")
        registered = {
            node.args[0].id
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and node.args and isinstance(node.args[0], ast.Name)
        }
        self.assertEqual(registered, set(declared_tools(self.config)))


class RealServerTests(AgentTestCase):
    # Teste : le vrai serveur MCP de SensAI demarre (comme le fait l'agent) et annonce les outils declares dans
    # config/tools.yaml, avec les memes parametres et les memes champs obligatoires pour chacun.
    # Test lent (environ 5 s) : il charge toutes les dependances des outils. Aucun outil n'est appele.
    def test_real_server_starts_and_exposes_the_declared_tools(self):
        async def main():
            try:
                client = await agent.connect_mcp()
                return (await client.list_tools()).tools
            finally:
                await agent.close_mcp()

        server_tools = run_async(main())
        declared = declared_tools(self.config)
        self.assertEqual({tool.name for tool in server_tools}, set(declared))
        for tool in server_tools:
            with self.subTest(tool=tool.name):
                expected = declared[tool.name]["parameters"]
                self.assertEqual(set(tool.input_schema.get("properties", {})), set(expected.get("properties", {})))
                self.assertEqual(set(tool.input_schema.get("required", [])), set(expected.get("required", [])))


# 2. Appel : du modele a l'outil, puis de l'outil au modele -------------------------------------------------------

class ToolCallRoundTripTests(AgentTestCase):
    # Teste : quand le modele demande un outil, l'agent l'execute via MCP avec exactement les arguments donnes
    # par le modele (user story : appeler un outil externe quand la question l'exige).
    def test_tool_requested_by_the_model_is_executed_with_its_arguments(self):
        recorder = mock.Mock(return_value=mcp_result({"temperature": "24.6°C"}))
        self.use([call("weather", {"city": "Lisbonne", "now": True}), reply("Il fait chaud.")])
        with self.mcp({"weather": recorder}):
            self.play("Quel temps fait-il a Lisbonne ?", history=Session("sys"))
        recorder.assert_called_once_with({"city": "Lisbonne", "now": True})

    # Teste : le resultat de l'outil est renvoye au modele (message "tool" portant le nom de l'outil et le resultat
    # en JSON), puis le modele poursuit sa generation et sa reponse finale est celle que l'on recoit
    # (user story : integrer le resultat de l'outil dans la reponse finale).
    def test_tool_result_goes_back_to_the_model_which_then_answers(self):
        weather = {"city": "Lisbonne", "temperature": "24.6°C"}
        self.use([call("weather", {"city": "Lisbonne"}), reply("Il fait 24,6 degres.")])
        with self.mcp({"weather": lambda args: mcp_result(weather)}):
            events = self.play("Meteo a Lisbonne ?", history=Session("sys"))

        kinds = [event["type"] for event in events]
        self.assertLess(kinds.index("tool_call"), kinds.index("tool_result"))
        self.assertEqual(next(e for e in events if e["type"] == "tool_result")["result"], weather)

        second_request = self.client.requests[1]["messages"]
        self.assertEqual(second_request[-1]["role"], "tool")
        self.assertEqual(second_request[-1]["tool_name"], "weather")
        self.assertEqual(json.loads(second_request[-1]["content"]), weather)
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Il fait 24,6 degres."})

    # Teste : quand le modele demande deux outils dans la meme reponse, les deux sont executes, dans l'ordre, et
    # leurs deux resultats sont renvoyes au modele.
    def test_several_tool_calls_in_one_reply_are_all_executed_in_order(self):
        weather = mock.Mock(return_value=mcp_result({"temperature": "20°C"}))
        activities = mock.Mock(return_value=mcp_result({"activities": ["Colisee"]}))
        self.use([reply("", [("weather", {"city": "Rome"}), ("search_activities", {"city": "Rome", "month": "mai"})]),
                  reply("Voila.")])
        with self.mcp({"weather": weather, "search_activities": activities}):
            self.play("Rome en mai ?", history=Session("sys"))

        weather.assert_called_once_with({"city": "Rome"})
        activities.assert_called_once_with({"city": "Rome", "month": "mai"})
        tool_messages = [m for m in self.client.requests[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_name"] for m in tool_messages], ["weather", "search_activities"])

    # Teste : parmi plusieurs outils disponibles, l'agent envoie l'appel a l'outil nomme par le modele et a lui
    # seul. (Le choix du bon outil par le vrai modele n'est pas teste ici : il est mesure dans bench/.)
    def test_call_is_routed_to_the_named_tool_only(self):
        weather = mock.Mock(return_value=mcp_result({}))
        flights = mock.Mock(return_value=mcp_result({"flights": []}))
        activities = mock.Mock(return_value=mcp_result({}))
        self.use([call("search_flights", FLIGHT_ARGUMENTS), reply("Pas de vol.")])
        with self.mcp({"weather": weather, "search_flights": flights, "search_activities": activities}):
            self.play("Un vol Paris Lisbonne ?", history=Session("sys"))

        flights.assert_called_once_with(FLIGHT_ARGUMENTS)
        weather.assert_not_called()
        activities.assert_not_called()


# 3. Resultat : conversion de ce que renvoie MCP --------------------------------------------------------------------

class ToolResultConversionTests(AgentTestCase):
    def execute(self, tool_result):
        with self.mcp({"weather": lambda args: tool_result}):
            return run_async(agent.execute_tool("weather", {"city": "Paris"}))

    # Teste : un resultat MCP dont le texte est du JSON est decode : le modele recoit un dict, pas une chaine.
    def test_json_text_is_decoded_to_a_dict(self):
        self.assertEqual(self.execute(mcp_result({"temperature": 18})), {"temperature": 18})

    # Teste : un resultat MCP en texte brut (pas du JSON) est rendu tel quel, sous forme de chaine.
    def test_plain_text_stays_text(self):
        self.assertEqual(self.execute(mcp_result("Il pleut a Paris.")), "Il pleut a Paris.")

    # Teste : un resultat MCP en plusieurs blocs de texte est rendu sous forme de liste de textes.
    def test_several_text_blocks_become_a_list(self):
        result = CallToolResult(content=[TextContent(type="text", text="a"), TextContent(type="text", text="b")])
        self.assertEqual(self.execute(result), ["a", "b"])

    # Teste : un resultat MCP sans aucun texte n'est pas perdu : il est rendu sous la cle "result".
    def test_result_without_text_is_kept_under_the_result_key(self):
        result = self.execute(CallToolResult(content=[]))
        self.assertEqual(list(result), ["result"])


# 4. Erreurs : l'agent ne plante jamais ------------------------------------------------------------------------------

class ToolErrorTests(AgentTestCase):
    def tool_result_of(self, events):
        return next(event for event in events if event["type"] == "tool_result")["result"]

    # Teste : si le modele demande un outil que le serveur n'a pas, le modele recoit une erreur claire ("tool name
    # invalide"), l'appel n'a lieu nulle part et la conversation continue jusqu'a la reponse finale.
    def test_unknown_tool_returns_an_error_and_the_turn_continues(self):
        recorder = mock.Mock()
        self.use([call("nope", {}), reply("Je ne peux pas.")])
        with self.mcp({"weather": recorder}):
            events = self.play("salut", history=Session("sys"))
        self.assertIn("invalide", self.tool_result_of(events)["error"])
        recorder.assert_not_called()
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Je ne peux pas."})

    # Teste : un outil que le serveur propose mais qui n'est pas declare dans config/tools.yaml est refuse
    # ("tool inexistant") : l'agent n'execute que les outils que le modele connait.
    def test_tool_missing_from_tools_yaml_is_refused(self):
        recorder = mock.Mock()
        self.use([call("outil_secret", {}), reply("fini")])
        with self.mcp({"outil_secret": recorder}):
            events = self.play("salut", history=Session("sys"))
        self.assertEqual(self.tool_result_of(events), {"error": "tool inexistant"})
        recorder.assert_not_called()

    # Teste : un argument obligatoire manquant est refuse avant l'appel, et l'erreur nomme le champ manquant.
    def test_missing_required_argument_is_rejected_before_the_call(self):
        recorder = mock.Mock()
        self.use([call("weather", {}), reply("fini")])
        with self.mcp({"weather": recorder}):
            events = self.play("meteo", history=Session("sys"))
        recorder.assert_not_called()
        self.assertIn("champs manquants", self.tool_result_of(events)["error"])
        self.assertIn("city", self.tool_result_of(events)["error"])

    # Teste : un argument du mauvais type (un nombre au lieu d'une chaine) est refuse avant l'appel.
    def test_wrong_argument_type_is_rejected_before_the_call(self):
        recorder = mock.Mock()
        self.use([call("weather", {"city": 42}), reply("fini")])
        with self.mcp({"weather": recorder}):
            events = self.play("meteo", history=Session("sys"))
        recorder.assert_not_called()
        self.assertIn("attendu string", self.tool_result_of(events)["error"])

    # Teste : des arguments donnes sous forme de chaine JSON (certains modeles le font) sont decodes et acceptes.
    def test_arguments_given_as_a_json_string_are_accepted(self):
        recorder = mock.Mock(return_value=mcp_result({"temperature": 18}))
        self.use([call("weather", '{"city": "Paris"}'), reply("18 degres")])
        with self.mcp({"weather": recorder}):
            self.play("meteo", history=Session("sys"))
        recorder.assert_called_once_with({"city": "Paris"})

    # Teste : des arguments qui ne sont pas un objet JSON (du texte quelconque) sont refuses avec un message clair.
    def test_arguments_that_are_not_a_json_object_are_rejected(self):
        recorder = mock.Mock()
        self.use([call("weather", "pas du json"), reply("fini")])
        with self.mcp({"weather": recorder}):
            events = self.play("meteo", history=Session("sys"))
        recorder.assert_not_called()
        self.assertIn("arguments invalides", self.tool_result_of(events)["error"])

    # Teste : si l'appel a l'outil leve une exception cote client (connexion coupee en plein appel), le modele recoit
    # une erreur qui nomme l'outil et la cause, et le tour se termine normalement.
    def test_exception_during_the_call_is_returned_to_the_model(self):
        def broken(args):
            raise ConnectionError("serveur coupe")

        self.use([call("weather", {"city": "Paris"}), reply("Service indisponible.")])
        with self.mcp({"weather": broken}):
            events = self.play("meteo", history=Session("sys"))
        error = self.tool_result_of(events)["error"]
        self.assertIn("'weather' a échoué", error)
        self.assertIn("serveur coupe", error)
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Service indisponible."})

    # Teste : un outil qui echoue cote serveur (resultat MCP marque en erreur) est rendu sous la forme
    # {"error": <message du serveur>}, comme les autres echecs, et non comme un texte ordinaire.
    def test_result_flagged_as_error_by_the_server_becomes_an_error_dict(self):
        failing = mcp_result("Error executing tool weather", is_error=True)
        with self.mcp({"weather": lambda args: failing}):
            result = run_async(agent.execute_tool("weather", {"city": "Paris"}))
        self.assertEqual(result, {"error": "Error executing tool weather"})

    # Teste : meme sans message, un resultat marque en erreur reste une erreur, et elle nomme l'outil.
    def test_result_flagged_as_error_without_a_message_names_the_tool(self):
        failing = CallToolResult(content=[], is_error=True)
        with self.mcp({"weather": lambda args: failing}):
            result = run_async(agent.execute_tool("weather", {"city": "Paris"}))
        self.assertIn("weather", result["error"])

    # Teste : de bout en bout, l'echec d'un outil cote serveur arrive au modele comme une erreur (message "tool"
    # en JSON avec la cle "error"), est reconnu comme un echec par le journal des interactions, et le tour
    # se termine normalement.
    def test_server_side_failure_reaches_the_model_and_the_journal_as_an_error(self):
        failing = mcp_result("Error executing tool weather", is_error=True)
        self.use([call("weather", {"city": "Paris"}), reply("Service indisponible.")])
        with self.mcp({"weather": lambda args: failing}):
            events = self.play("meteo", history=Session("sys"))
        self.assertTrue(tool_failure(self.tool_result_of(events))[0])
        tool_message = self.client.requests[1]["messages"][-1]
        self.assertEqual(json.loads(tool_message["content"]), {"error": "Error executing tool weather"})
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Service indisponible."})

    # Teste : un serveur MCP qui ne peut pas demarrer fait echouer la connexion tout de suite, avec une exception,
    # sans blocage, et l'agent reste sans client (une nouvelle tentative reste possible).
    def test_server_that_cannot_start_fails_fast(self):
        agent.server = StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(3)"])
        with self.assertRaises((OSError, BaseExceptionGroup)):
            run_async(asyncio.wait_for(agent.connect_mcp(), timeout=20))
        self.assertIsNone(agent.mcp_client)

    # Teste : une commande de serveur introuvable fait aussi echouer la connexion tout de suite.
    def test_server_command_not_found_fails_fast(self):
        agent.server = StdioServerParameters(command="/commande/qui/n/existe/pas", args=[])
        with self.assertRaises(FileNotFoundError):
            run_async(asyncio.wait_for(agent.connect_mcp(), timeout=20))
        self.assertIsNone(agent.mcp_client)


# 5. Serveur tiers : un vrai serveur MCP qui n'est pas celui de SensAI ----------------------------------------------

class ThirdPartyServerTests(AgentTestCase):
    # Lance le serveur tiers (tests/third_party_mcp_server.py), declare ses outils dans la config comme le fait
    # demo_external_mcp.py, joue `scenario` (une coroutine) puis arrete le serveur.
    def with_third_party_server(self, scenario, declared=("echo", "shout", "boom")):
        for name in declared:
            self.config.tools.append({"type": "function", "function": {
                "name": name, "description": "outil du serveur tiers", "parameters": {"type": "object", **THIRD_PARTY_TOOLS[name]}}})
        agent.server = StdioServerParameters(command=sys.executable, args=[str(THIRD_PARTY_SERVER)])

        async def main():
            try:
                await agent.connect_mcp()
                return await scenario()
            finally:
                await agent.close_mcp()

        return run_async(main())

    # Teste : l'agent se connecte a un serveur MCP qui n'est pas celui de SensAI et decouvre ses outils.
    def test_agent_discovers_the_tools_of_a_third_party_server(self):
        async def scenario():
            return {tool.name for tool in (await agent.mcp_client.list_tools()).tools}

        self.assertEqual(self.with_third_party_server(scenario), {"echo", "shout", "boom"})

    # Teste : un outil du serveur tiers, une fois declare dans la config, est execute par l'agent et son resultat
    # JSON est decode (un dict, pas une chaine).
    def test_declared_third_party_tool_is_called_and_its_json_result_decoded(self):
        async def scenario():
            return await agent.execute_tool("echo", {"text": "salut"})

        self.assertEqual(self.with_third_party_server(scenario), {"echo": "salut"})

    # Teste : le resultat en texte brut d'un outil tiers reste du texte.
    def test_third_party_plain_text_result_stays_text(self):
        async def scenario():
            return await agent.execute_tool("shout", {"text": "salut"})

        self.assertEqual(self.with_third_party_server(scenario), "SALUT")

    # Teste : un outil du serveur tiers qui n'est PAS declare dans la config est refuse, meme si le serveur le propose.
    def test_third_party_tool_not_declared_in_the_config_is_refused(self):
        async def scenario():
            return await agent.execute_tool("echo", {"text": "salut"})

        self.assertEqual(self.with_third_party_server(scenario, declared=()), {"error": "tool inexistant"})

    # Teste : les arguments d'un outil tiers sont verifies avec le schema declare dans la config, avant l'appel.
    def test_third_party_tool_arguments_are_validated(self):
        async def scenario():
            return await agent.execute_tool("echo", {})

        self.assertEqual(self.with_third_party_server(scenario), {"error": "champs manquants : ['text']"})

    # Teste : un outil tiers qui plante cote serveur ne fait pas planter l'agent : le modele recoit une erreur
    # {"error": <message du serveur>}.
    def test_third_party_tool_that_crashes_does_not_crash_the_agent(self):
        async def scenario():
            return await agent.execute_tool("boom", {})

        result = self.with_third_party_server(scenario)
        self.assertIn("boom", result["error"])

    # Teste : un outil qui plante cote serveur est signale comme un echec au journal des interactions (EV3), donc
    # compte dans les "echecs par outil" du tableau de bord.
    def test_third_party_tool_that_crashes_is_flagged_as_a_failure(self):
        async def scenario():
            return await agent.execute_tool("boom", {})

        failed, _message = tool_failure(self.with_third_party_server(scenario))
        self.assertTrue(failed)


if __name__ == "__main__":
    unittest.main()
