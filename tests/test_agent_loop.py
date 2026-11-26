# A1 : Boucle d'agent (ReAct) - src/agent.py (run_agent), src/utils.py (normalize_tool_calls), src/tools/handler.py.
#
# L'agent joue un tour ainsi : le modele repond, ou demande un outil ; l'agent execute l'outil, renvoie le resultat au
# modele, et recommence jusqu'a une reponse finale. Ce fichier teste cette boucle avec un faux modele (qui rejoue des
# reponses ecrites a l'avance) et un faux client MCP. L'execution d'un outil via MCP est testee dans test_mcp.py.
#
#   1. Plafond d'iterations : la boucle s'arrete toujours, avec une explication.
#   2. Enchainement         : un tour peut appeler plusieurs outils a la suite avant de conclure.
#   3. Requete au modele    : outils, "think" et options viennent de la config.
#   4. Reponse simple       : un tour sans outil.
#   5. Pannes               : retries, tour jamais garde a moitie, Ctrl+C.
#   6. Repli texte          : un modele qui ecrit son appel d'outil en JSON dans le texte est quand meme compris.
#   7. Compteur d'etapes    : chaque passage dans la boucle est annonce (evenement "step", affiche [etape n/N] par la CLI).
#   8. Action repetee       : le meme appel d'outil, memes arguments, redemande d'affilee arrete le tour.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import json
import unittest
from dataclasses import replace
from unittest import mock

from src import agent
from src.config import set_active_config
from src.ollama_client import OllamaError
from src.session import Session
from src.tools import handler
from src.utils import normalize_tool_calls
from tests.helpers import AgentTestCase, call, reply
from tests.harness import mcp_result

WEATHER = {"weather": lambda args: mcp_result({"temp": 20, "city": args["city"]})}
ACTIVITIES = {"search_activities": lambda args: mcp_result({"activities": ["Colisee"]})}


def kinds(events):
    return [event["type"] for event in events]


# Appels meteo a des villes toutes differentes : le plafond d'iterations est atteint sans que l'arret sur action repetee
# n'intervienne (trois appels identiques d'affilee l'arreteraient avant).
def distinct_weather_calls(count):
    cities = ("Paris", "Rome", "Lisbonne", "Madrid", "Berlin", "Vienne")
    return [call("weather", {"city": city}) for city in cities[:count]]


# 1. Plafond d'iterations -------------------------------------------------------------------------------------------

class IterationCapTests(AgentTestCase):
    # Teste : si le modele enchaine les appels d'outils sans jamais conclure, la boucle s'arrete apres max_iterations
    # appels au modele, avec un message d'erreur qui annonce le plafond.
    def test_loop_stops_at_the_cap_with_an_explanation(self):
        self.use(distinct_weather_calls(3))
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("Plafond de 3 itérations", events[-1]["message"])
        self.assertEqual(len(self.client.requests), 3)

    # Teste : le message d'erreur du plafond dit comment le changer (la cle max_iterations de config/config.json).
    def test_cap_message_tells_how_to_raise_it(self):
        self.use(distinct_weather_calls(3))
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertIn("max_iterations", events[-1]["message"])

    # Teste : un tour arrete par le plafond n'est pas conserve : la conversation reste exactement comme avant.
    def test_a_turn_stopped_by_the_cap_is_not_kept(self):
        session = Session("sys")
        self.use(distinct_weather_calls(3))
        with self.mcp(WEATHER):
            self.play("meteo ?", history=session)
        self.assertEqual(session.messages, [{"role": "system", "content": "sys"}])
        self.assertEqual(session.turns, 0)

    # Teste : un tour qui conclut pile au plafond (2 outils puis la reponse, soit 3 appels pour un plafond de 3) reussit.
    def test_a_turn_that_ends_exactly_at_the_cap_succeeds(self):
        self.use([call("weather", {"city": "Paris"}), call("weather", {"city": "Rome"}), reply("Fini.")])
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Fini."})


# 2. Enchainement d'outils ------------------------------------------------------------------------------------------

class ToolChainTests(AgentTestCase):
    # Teste : le modele peut appeler un outil, lire son resultat, puis en appeler un autre avant de conclure : trois
    # appels au modele, et chaque appel suivant voit les resultats precedents.
    def test_model_can_chain_two_tools_before_answering(self):
        self.use([call("weather", {"city": "Rome"}), call("search_activities", {"city": "Rome", "month": "mai"}), reply("Voila.")])
        with self.mcp({**WEATHER, **ACTIVITIES}):
            events = self.play("Rome en mai ?", history=Session("sys"))
        self.assertEqual(len(self.client.requests), 3)
        tool_names_seen = [[m["tool_name"] for m in r["messages"] if m["role"] == "tool"] for r in self.client.requests]
        self.assertEqual(tool_names_seen, [[], ["weather"], ["weather", "search_activities"]])
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Voila."})

    # Teste : les evenements d'un tour avec outil arrivent dans l'ordre : etape 1, appel, resultat, etape 2, puis la fin.
    def test_events_come_in_order_call_result_done(self):
        self.use([call("weather", {"city": "Paris"}), reply("Beau.")])
        with self.mcp(WEATHER):
            events = self.play("meteo", history=Session("sys"))
        useful = [e for e in events if e["type"] != "token"]
        self.assertEqual(kinds(useful), ["step", "tool_call", "tool_result", "step", "done_tool"])
        self.assertEqual(useful[1]["name"], "weather")
        self.assertEqual(useful[1]["arguments"], {"city": "Paris"})
        self.assertEqual(useful[2]["result"], {"temp": 20, "city": "Paris"})

    # Teste : un tour reussi entre en entier dans la conversation : question, appel d'outil, resultat, reponse finale.
    def test_a_successful_turn_is_kept_in_full_in_the_session(self):
        session = Session("sys")
        self.use([call("weather", {"city": "Paris"}), reply("Beau.")])
        with self.mcp(WEATHER):
            self.play("meteo", history=session)
        self.assertEqual([m["role"] for m in session.messages], ["system", "user", "assistant", "tool", "assistant"])
        self.assertEqual(session.turns, 1)

    # Teste : chaque message du tour est enregistre avec le modele "outils" qui l'a joue ; le prompt systeme n'est pas un
    # message du tour et ne porte pas de modele.
    def test_the_finished_turn_is_recorded_with_the_tool_model(self):
        session = Session("sys")
        self.use([call("weather", {"city": "Paris"}), reply("Beau.")])
        with self.mcp(WEATHER):
            self.play("meteo", history=session)
        turn = session.messages[1:]
        self.assertEqual([m["role"] for m in turn], ["user", "assistant", "tool", "assistant"])
        self.assertEqual({m["model"] for m in turn}, {self.config.tool_model})
        self.assertNotIn("model", session.messages[0])


# 3. Requete au modele ----------------------------------------------------------------------------------------------

class ModelRequestTests(AgentTestCase):
    def first_request(self):
        self.use([reply("ok")])
        self.play("salut", history=Session("sys"))
        return self.client.requests[0]

    # Teste : le modele recoit les outils de config/tools.yaml, le parametre "think" et les options de la config.
    def test_model_gets_tools_think_and_options_from_the_config(self):
        request = self.first_request()
        self.assertEqual(request["tools"], self.config.tools)
        self.assertEqual(request["think"], self.config.think)
        self.assertEqual(request["options"], self.config.options)

    # Teste : le tour est joue avec le modele "outils" de la config.
    def test_the_tool_model_of_the_config_is_used(self):
        self.assertEqual(self.first_request()["model"], self.config.tool_model)

    # Teste : le modele recoit le prompt systeme de la session, puis la question de l'utilisateur.
    def test_model_gets_the_system_prompt_then_the_question(self):
        messages = self.first_request()["messages"]
        self.assertEqual(messages, [{"role": "system", "content": "sys"}, {"role": "user", "content": "salut"}])


# 4. Reponse simple -------------------------------------------------------------------------------------------------

class PlainAnswerTests(AgentTestCase):
    # Teste : une question qui n'appelle aucun outil donne l'etape 1, le texte en morceaux ("token") puis "done_tool" avec
    # la reponse.
    def test_answer_without_tool(self):
        self.use([reply("Bonjour")])
        events = self.play("salut", history=Session("sys"))
        self.assertEqual(kinds(events), ["step", "token", "done_tool"])
        self.assertEqual("".join(e["content"] for e in events if e["type"] == "token"), "Bonjour")
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Bonjour"})

    # Teste : un tour sans outil est conserve dans la conversation (question + reponse).
    def test_plain_turn_is_kept_in_the_session(self):
        session = Session("sys")
        self.use([reply("Bonjour")])
        self.play("salut", history=session)
        self.assertEqual([m["role"] for m in session.messages], ["system", "user", "assistant"])
        self.assertEqual(session.turns, 1)


# 5. Pannes ---------------------------------------------------------------------------------------------------------

class FailureTests(AgentTestCase):
    # Teste : si Ollama echoue, l'agent reessaie ; apres 3 tentatives au total, il rend une erreur claire.
    def test_three_failed_attempts_end_with_an_error(self):
        self.use([OllamaError("Ollama est tombe.")] * 3)
        events = self.play("salut", history=Session("sys"))
        self.assertEqual(events[-1], {"type": "error", "message": "Ollama est tombe."})
        self.assertEqual(len(self.client.requests), 3)

    # Teste : une panne passagere est absorbee : apres deux echecs puis une reponse, le tour reussit.
    def test_two_failures_then_an_answer_succeeds(self):
        self.use([OllamaError("coupure"), OllamaError("coupure"), reply("Ouf.")])
        events = self.play("salut", history=Session("sys"))
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Ouf."})

    # Teste : une panne apres un appel d'outil n'ajoute rien a la conversation : jamais de tour a moitie enregistre.
    def test_a_failure_after_a_tool_call_leaves_the_session_untouched(self):
        session = Session("sys")
        self.use([call("weather", {"city": "Paris"})] + [OllamaError("coupure")] * 3)
        with self.mcp(WEATHER):
            events = self.play("meteo", history=session)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(session.messages, [{"role": "system", "content": "sys"}])
        self.assertEqual(session.turns, 0)

    # Teste : Ctrl+C n'est pas avale par l'agent (il remonte a la CLI qui l'affiche), et la conversation reste intacte.
    def test_ctrl_c_propagates_and_leaves_the_session_untouched(self):
        session = Session("sys")
        self.use([KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.play("salut", history=session)
        self.assertEqual(session.messages, [{"role": "system", "content": "sys"}])


# 6. Repli texte ----------------------------------------------------------------------------------------------------

class TextFallbackTests(AgentTestCase):
    # Teste : un modele qui ecrit son appel d'outil en JSON dans le texte (au lieu du champ prevu) est compris : l'outil
    # est execute avec les arguments ecrits.
    def test_json_tool_call_in_the_text_is_executed(self):
        recorder = mock.Mock(return_value=mcp_result({"temp": 15}))
        self.use([reply('{"name": "weather", "arguments": {"city": "Lille"}}'), reply("15 degres")])
        with self.mcp({"weather": recorder}):
            events = self.play("meteo", history=Session("sys"))
        recorder.assert_called_once_with({"city": "Lille"})
        self.assertEqual(events[-1]["content"], "15 degres")

    # Teste : le repli comprend aussi une liste d'appels au format {"function": {"name", "arguments"}}.
    def test_json_list_in_function_format_is_executed(self):
        recorder = mock.Mock(return_value=mcp_result({"temp": 15}))
        content = json.dumps([{"function": {"name": "weather", "arguments": {"city": "Lille"}}}])
        self.use([reply(content), reply("fini")])
        with self.mcp({"weather": recorder}):
            self.play("meteo", history=Session("sys"))
        recorder.assert_called_once_with({"city": "Lille"})

    # Teste : le repli fonctionne aussi pour les outils de memoire du voyage et la recherche web, pas seulement la meteo.
    def test_fallback_covers_the_memory_and_web_search_tools(self):
        for name, arguments in (("trip_get", {}), ("web_search_basic", {"query": "musees a Rome"})):
            with self.subTest(tool=name):
                recorder = mock.Mock(return_value=mcp_result({"ok": True}))
                self.use([reply(json.dumps({"name": name, "arguments": arguments})), reply("fini")])
                with self.mcp({name: recorder}):
                    self.play("rome", history=Session("sys"))
                recorder.assert_called_once_with(arguments)

    # Teste : un JSON qui n'est pas un appel d'outil (un plan, par exemple) est une reponse ordinaire, sans erreur.
    def test_unrelated_json_is_a_plain_answer(self):
        self.use([reply('{"destination": "Rome", "jours": 3}')])
        events = self.play("plan", history=Session("sys"))
        self.assertNotIn("tool_call", kinds(events))
        self.assertEqual(events[-1]["type"], "done_tool")

    # Teste : un JSON qui nomme un outil inexistant (format_disk) n'est jamais execute : c'est une reponse ordinaire.
    def test_json_naming_an_unknown_tool_is_a_plain_answer(self):
        self.use([reply('{"name": "format_disk", "arguments": {}}')])
        events = self.play("plan", history=Session("sys"))
        self.assertNotIn("tool_call", kinds(events))
        self.assertEqual(events[-1]["type"], "done_tool")

    # Teste : un JSON qui est une liste de nombres est une reponse ordinaire.
    def test_json_list_of_numbers_is_a_plain_answer(self):
        self.use([reply("[1, 2, 3]")])
        events = self.play("plan", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "done_tool")

    # Teste : la liste des noms connus du repli (src/tools/handler.py) est exactement la liste des outils de
    # config/tools.yaml : un outil absent d'ici serait ignore quand le modele l'ecrit en JSON dans le texte.
    def test_the_fallback_knows_exactly_the_declared_tools(self):
        declared = {tool["function"]["name"] for tool in self.config.tools}
        self.assertEqual(declared, set(handler.tool_handler))
        self.assertEqual(len(handler.TOOL_NAMES), len(set(handler.TOOL_NAMES)))


class NormalizeToolCallsTests(unittest.TestCase):
    KNOWN = {"weather": object()}

    # Teste : un objet {"name", "arguments"} est normalise au format {"function": {"name", "arguments"}}.
    def test_name_and_arguments_object(self):
        parsed = normalize_tool_calls({"name": "weather", "arguments": {"city": "Paris"}}, self.KNOWN)
        self.assertEqual(parsed, [{"function": {"name": "weather", "arguments": {"city": "Paris"}}}])

    # Teste : la cle "parameters" est acceptee a la place de "arguments".
    def test_parameters_key_is_accepted(self):
        parsed = normalize_tool_calls({"name": "weather", "parameters": {"city": "Paris"}}, self.KNOWN)
        self.assertEqual(parsed[0]["function"]["arguments"], {"city": "Paris"})

    # Teste : des arguments donnes sous forme de chaine JSON sont decodes en objet.
    def test_arguments_as_a_json_string_are_decoded(self):
        parsed = normalize_tool_calls({"name": "weather", "arguments": '{"city": "Paris"}'}, self.KNOWN)
        self.assertEqual(parsed[0]["function"]["arguments"], {"city": "Paris"})

    # Teste : un nom d'outil inconnu fait tout rejeter (None), meme si les autres appels sont valides.
    def test_unknown_tool_name_rejects_everything(self):
        calls = [{"name": "weather", "arguments": {}}, {"name": "format_disk", "arguments": {}}]
        self.assertIsNone(normalize_tool_calls(calls, self.KNOWN))

    # Teste : ce qui n'est ni un objet ni une liste d'objets (nombre, texte, None) donne None.
    def test_non_object_inputs_give_none(self):
        for parsed in (3, "weather", None, [1, 2]):
            with self.subTest(parsed=parsed):
                self.assertIsNone(normalize_tool_calls(parsed, self.KNOWN))

    # Teste : des arguments qui ne forment pas un objet JSON (du texte quelconque) font rejeter l'appel.
    def test_arguments_that_are_not_an_object_reject_the_call(self):
        self.assertIsNone(normalize_tool_calls({"name": "weather", "arguments": "pas du json"}, self.KNOWN))


class LastAssistantContentTests(unittest.TestCase):
    # Teste : la reponse finale retenue est le texte du dernier message de l'assistant, meme apres des messages d'outils.
    def test_picks_the_last_assistant_message(self):
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "premiere"},
            {"role": "tool", "content": "{}"},
            {"role": "assistant", "content": "derniere"},
        ]
        self.assertEqual(agent.last_assistant_content(messages), "derniere")

    # Teste : sans message de l'assistant, le resultat est une chaine vide (pas d'erreur).
    def test_empty_when_there_is_no_assistant_message(self):
        self.assertEqual(agent.last_assistant_content([{"role": "user", "content": "q"}]), "")


# 7. Compteur d'etapes ----------------------------------------------------------------------------------------------

class StepCounterTests(AgentTestCase):
    # Teste : chaque passage dans la boucle (un appel au modele) annonce son numero et le plafond avant le reste : deux
    # outils puis la reponse donnent les etapes 1, 2 et 3 sur 3, chacune avant l'appel d'outil qui la suit.
    def test_each_pass_through_the_loop_announces_its_number(self):
        self.use([call("weather", {"city": "Paris"}), call("weather", {"city": "Rome"}), reply("Fini.")])
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        useful = [e for e in events if e["type"] != "token"]
        self.assertEqual(
            kinds(useful),
            ["step", "tool_call", "tool_result", "step", "tool_call", "tool_result", "step", "done_tool"],
        )
        self.assertEqual([(e["n"], e["max"]) for e in events if e["type"] == "step"], [(1, 3), (2, 3), (3, 3)])

    # Teste : une reponse sans outil est une seule etape, annoncee avant le premier morceau de texte.
    def test_an_answer_without_tool_is_a_single_step(self):
        self.use([reply("Bonjour")])
        events = self.play("salut", history=Session("sys"))
        self.assertEqual(events[0], {"type": "step", "n": 1, "max": 3})
        self.assertEqual(kinds(events).count("step"), 1)


# 8. Action repetee -------------------------------------------------------------------------------------------------

class RepeatedActionTests(AgentTestCase):
    max_iterations = 8

    # Teste : le meme appel d'outil (memes arguments) redemande 3 fois d'affilee arrete le tour, bien avant le plafond :
    # le message nomme l'outil et la cle a regler, et le 3e appel n'est ni annonce ni execute.
    def test_the_third_identical_call_in_a_row_stops_the_turn(self):
        executed = []
        tools = {"weather": lambda args: executed.append(args) or mcp_result({"temp": 20})}
        self.use([call("weather", {"city": "Paris"})] * 3)
        with self.mcp(tools):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "error")
        for fragment in ("3 fois de suite", "weather", "Paris", "max_repeated_actions"):
            self.assertIn(fragment, events[-1]["message"])
        self.assertEqual(kinds(events).count("tool_call"), 2)
        self.assertEqual(len(executed), 2)
        self.assertEqual(len(self.client.requests), 3)

    # Teste : un tour arrete pour repetition n'est pas conserve : la conversation reste exactement comme avant.
    def test_a_turn_stopped_for_repetition_is_not_kept(self):
        session = Session("sys")
        self.use([call("weather", {"city": "Paris"})] * 3)
        with self.mcp(WEATHER):
            self.play("meteo ?", history=session)
        self.assertEqual(session.messages, [{"role": "system", "content": "sys"}])
        self.assertEqual(session.turns, 0)

    # Teste : le meme outil avec d'autres arguments n'est pas une repetition : trois villes differentes a la suite.
    def test_the_same_tool_with_other_arguments_is_not_a_repetition(self):
        self.use(distinct_weather_calls(3) + [reply("Fini.")])
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Fini."})

    # Teste : l'ordre des arguments ne change rien : le meme appel ecrit avec les cles dans un autre ordre est repete.
    def test_the_order_of_the_arguments_does_not_matter(self):
        self.use([
            call("search_activities", {"city": "Rome", "month": "mai"}),
            call("search_activities", {"month": "mai", "city": "Rome"}),
            call("search_activities", {"city": "Rome", "month": "mai"}),
        ])
        with self.mcp(ACTIVITIES):
            events = self.play("activites ?", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("3 fois de suite", events[-1]["message"])

    # Teste : un autre appel entre deux appels identiques remet le compte a zero : A, A, B, A, A ne s'arrete pas.
    def test_a_different_call_in_between_resets_the_count(self):
        paris, rome = call("weather", {"city": "Paris"}), call("weather", {"city": "Rome"})
        self.use([paris, paris, rome, paris, paris, reply("Fini.")])
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Fini."})

    # Teste : la repetition est aussi detectee dans une seule reponse du modele qui demande 3 fois le meme appel.
    def test_the_same_call_asked_three_times_in_one_reply_is_stopped(self):
        self.use([reply("", [("weather", {"city": "Paris"})] * 3)])
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(kinds(events).count("tool_call"), 2)

    # Teste : le nombre d'appels identiques tolere vient de la config (max_repeated_actions) : avec 2, le 2e suffit.
    def test_the_threshold_comes_from_the_config(self):
        self.config = replace(self.config, max_repeated_actions=2)
        set_active_config(self.config)
        self.use([call("weather", {"city": "Paris"})] * 2)
        with self.mcp(WEATHER):
            events = self.play("meteo ?", history=Session("sys"))
        self.assertIn("2 fois de suite", events[-1]["message"])
        self.assertEqual(kinds(events).count("tool_call"), 1)


if __name__ == "__main__":
    unittest.main()
