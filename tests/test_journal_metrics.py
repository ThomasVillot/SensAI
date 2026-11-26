# EV3 : Logging et metriques - src/interaction_log.py (journal), src/metrics.py (tableau de bord), src/latency.py (latence).
#
# Chaque tour de la CLI ajoute une ligne JSON au journal (logs/interactions.jsonl) : message, reponse, outils appeles,
# latence, tokens, statut. La vue de metriques (/stats ou --stats) resume ce journal. Seule la CLI alimente le journal :
# l'interface web n'ecrit rien dans logs/.
#
#   1. Echec d'un outil    : comment le journal reconnait qu'un outil a echoue.
#   2. Enregistreur de tour : les outils appeles pendant un tour et leur duree.
#   3. Ligne de journal    : les champs, l'ecriture, les pannes d'ecriture.
#   4. Journal dans la CLI : ce que la CLI ecrit pour chaque sorte de tour.
#   5. Metriques           : lecture du journal, agregats, regroupements, texte du tableau de bord.
#   6. /stats et --stats   : la vue dans la CLI.
#   7. Latence             : mesure, ligne affichee, log fichier, moyenne a la fermeture.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie. Les journaux sont ecrits dans des dossiers
# temporaires : rien n'est ecrit dans logs/ du projet.

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src import metrics
from src.config import load_config
from src.interaction_log import (
    KIND_AGENT, KIND_CHAT, STATUS_ERROR, STATUS_INTERRUPTED, STATUS_MAX_ITERATIONS, STATUS_OK,
    InteractionLog, TurnRecorder, tool_failure,
)
from src.latency import LatencyTracker, combine_results
from src.ollama_client import ChatResult, OllamaError
from tests.harness import CliTestCase, mcp_result
from tests.helpers import call, reply


def result(total=0.5, ttft=0.1, tokens=10, load=None, content="x"):
    return ChatResult(content=content, total_s=total, ttft_s=ttft, eval_count=tokens, load_s=load)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


# 1. Echec d'un outil -----------------------------------------------------------------------------------------------

class ToolFailureTests(unittest.TestCase):
    # Teste : un resultat d'outil de la forme {"error": ...} est un echec, avec son message.
    def test_a_dict_with_an_error_is_a_failure(self):
        self.assertEqual(tool_failure({"error": "cle manquante"}), (True, "cle manquante"))

    # Teste : le message d'erreur journalise est tronque a 300 caracteres (le journal ne doit pas gonfler).
    def test_the_error_message_is_truncated(self):
        failed, message = tool_failure({"error": "x" * 1000})
        self.assertTrue(failed)
        self.assertEqual(len(message), 300)

    # Teste : un resultat sans cle "error", ou un simple texte, n'est pas un echec.
    def test_a_normal_result_is_not_a_failure(self):
        for ok in ({"temp": 20}, {"error": ""}, "Il pleut.", ["a"], None):
            with self.subTest(result=ok):
                self.assertEqual(tool_failure(ok), (False, None))

    # Teste : une reponse MCP marquee en erreur (is_error, ou isError dans les anciennes versions) est un echec, avec le
    # texte du serveur.
    def test_an_mcp_result_flagged_as_error_is_a_failure(self):
        for flag in ("is_error", "isError"):
            with self.subTest(flag=flag):
                mcp = SimpleNamespace(**{flag: True, "content": [SimpleNamespace(text="outil plante")]})
                self.assertEqual(tool_failure(mcp), (True, "outil plante"))

    # Teste : une reponse MCP en erreur sans texte est un echec avec un message de remplacement.
    def test_an_mcp_error_without_text_still_counts(self):
        self.assertEqual(tool_failure(SimpleNamespace(is_error=True, content=[])), (True, "erreur renvoyee par l'outil"))


# 2. Enregistreur de tour -------------------------------------------------------------------------------------------

class TurnRecorderTests(unittest.TestCase):
    # Teste : un appel d'outil suivi de son resultat est note avec son nom, ses arguments, son issue et sa duree.
    def test_a_call_and_its_result_are_recorded(self):
        recorder = TurnRecorder(KIND_AGENT, "m", "salut")
        recorder.tool_called("weather", {"city": "Rome"})
        recorder.tool_returned("weather", {"temp": 20})
        (tool,) = recorder.tools
        self.assertEqual((tool["name"], tool["arguments"], tool["ok"], tool["error"]), ("weather", {"city": "Rome"}, True, None))
        self.assertGreaterEqual(tool["duration_s"], 0)

    # Teste : un resultat en erreur est note comme un echec, avec le message d'erreur.
    def test_a_failed_result_is_recorded_as_a_failure(self):
        recorder = TurnRecorder(KIND_AGENT, "m", "salut")
        recorder.tool_called("weather", {})
        recorder.tool_returned("weather", {"error": "api HS"})
        self.assertEqual((recorder.tools[0]["ok"], recorder.tools[0]["error"]), (False, "api HS"))

    # Teste : plusieurs outils dans un tour sont notes dans l'ordre.
    def test_several_tools_are_recorded_in_order(self):
        recorder = TurnRecorder(KIND_AGENT, "m", "salut")
        for name in ("weather", "search_flights"):
            recorder.tool_called(name, {})
            recorder.tool_returned(name, {})
        self.assertEqual([t["name"] for t in recorder.tools], ["weather", "search_flights"])

    # Teste : un appel reste sans resultat (le serveur d'outils a plante) est note comme un echec a la fin du tour, avec
    # l'erreur qui a interrompu le tour.
    def test_a_call_left_without_a_result_is_recorded_as_a_failure(self):
        recorder = TurnRecorder(KIND_AGENT, "m", "salut")
        recorder.tool_called("weather", {"city": "Rome"})
        recorder.close_pending("serveur MCP arrete")
        self.assertEqual((recorder.tools[0]["ok"], recorder.tools[0]["error"]), (False, "serveur MCP arrete"))

    # Teste : sans appel en attente, fermer le tour n'ajoute rien.
    def test_closing_without_a_pending_call_adds_nothing(self):
        recorder = TurnRecorder(KIND_AGENT, "m", "salut")
        recorder.close_pending("erreur")
        self.assertEqual(recorder.tools, [])


# 3. Ligne de journal -----------------------------------------------------------------------------------------------

class InteractionLogTests(TempDirTestCase):
    def entry(self, log=None, *, status=STATUS_OK, calls=None, response="ok", error=None):
        log = log or InteractionLog(None, "sess", "v1")
        return log.build_entry(TurnRecorder(KIND_AGENT, "qwen3:8b", "salut"), status, response, error, calls or [])

    # Teste : une ligne de journal contient tous les champs annonces : date, session, type, modele, version du prompt,
    # message, reponse, statut, erreur, latences, tokens, nombre d'appels et outils.
    def test_an_entry_has_every_documented_field(self):
        entry = self.entry(calls=[result()])
        self.assertEqual(set(entry), {
            "ts", "session", "kind", "model", "prompt_version", "prompt", "response", "status", "error",
            "latency_s", "wall_s", "ttft_s", "tokens", "model_calls", "tool_calls",
        })
        self.assertEqual((entry["session"], entry["kind"], entry["model"], entry["prompt_version"]), ("sess", KIND_AGENT, "qwen3:8b", "v1"))
        self.assertRegex(entry["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    # Teste : la latence, les tokens et le nombre d'appels d'un tour sont la somme de ses appels au modele, et le delai
    # avant le premier token est celui du premier appel.
    def test_figures_are_summed_over_the_model_calls(self):
        entry = self.entry(calls=[result(total=1.0, ttft=0.2, tokens=10), result(total=2.0, ttft=0.9, tokens=30)])
        self.assertEqual((entry["latency_s"], entry["ttft_s"], entry["tokens"], entry["model_calls"]), (3.0, 0.2, 40, 2))

    # Teste : un tour qui a echoue avant le premier appel abouti n'a ni latence, ni tokens, ni delai (None) et 0 appel.
    def test_a_turn_without_any_finished_call_has_no_figures(self):
        entry = self.entry(status=STATUS_ERROR, error="coupure")
        self.assertEqual((entry["latency_s"], entry["ttft_s"], entry["tokens"], entry["model_calls"]), (None, None, None, 0))
        self.assertEqual((entry["status"], entry["error"]), (STATUS_ERROR, "coupure"))

    # Teste : le journal ajoute une ligne JSON par tour a la fin du fichier, sans effacer les lignes precedentes.
    def test_each_turn_appends_one_json_line(self):
        path = self.dir / "j.jsonl"
        log = InteractionLog(path, "sess", "v1")
        for n in (1, 2):
            log.write(TurnRecorder(KIND_AGENT, "m", f"question {n}"), STATUS_OK, f"reponse {n}", calls=[result()])
        self.assertEqual([e["prompt"] for e in read_lines(path)], ["question 1", "question 2"])

    # Teste : les accents sont ecrits lisiblement (pas en é) et le dossier du journal est cree s'il manque.
    def test_accents_are_readable_and_the_folder_is_created(self):
        path = self.dir / "logs" / "j.jsonl"
        InteractionLog(path, "sess").write(TurnRecorder(KIND_AGENT, "m", "où manger ?"), STATUS_OK, "à Rome")
        self.assertIn("où manger ?", path.read_text(encoding="utf-8"))

    # Teste : avec un chemin None (journal desactive dans la config), rien n'est ecrit mais la ligne est quand meme construite.
    def test_a_disabled_journal_writes_nothing(self):
        entry = InteractionLog(None, "sess").write(TurnRecorder(KIND_AGENT, "m", "salut"), STATUS_OK, "ok")
        self.assertEqual(entry["prompt"], "salut")

    # Teste : si l'ecriture est impossible, le journal se desactive avec un seul avertissement, sans lever d'exception ni
    # interrompre la conversation.
    def test_a_write_failure_disables_the_journal_with_one_warning(self):
        (self.dir / "fichier").write_text("je ne suis pas un dossier")
        log = InteractionLog(self.dir / "fichier" / "j.jsonl", "sess")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                log.write(TurnRecorder(KIND_AGENT, "m", "salut"), STATUS_OK, "ok")
        self.assertEqual(err.getvalue().count("journal des interactions désactivé"), 1)


# 4. Journal dans la CLI --------------------------------------------------------------------------------------------

class CliJournalTests(CliTestCase):
    MODEL = ["--model", "m"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journal = Path(tmp.name) / "interactions.jsonl"

    def run_journaled(self, inputs, script, argv=(), **kwargs):
        return self.run_cli(inputs, script, [*self.MODEL, *argv], interaction_log=self.journal, **kwargs)

    # Teste : un tour avec outil ajoute une ligne qui contient le message, la reponse finale, le statut "ok", les deux appels
    # au modele, leurs tokens et latences cumules, et l'outil appele avec son issue.
    def test_an_agent_turn_is_journaled_in_full(self):
        self.run_journaled(["meteo Paris"], [call("weather", {"city": "Paris"}), reply("Beau.")])
        (entry,) = read_lines(self.journal)
        self.assertEqual((entry["kind"], entry["status"], entry["model"]), (KIND_AGENT, STATUS_OK, "m"))
        self.assertEqual((entry["prompt"], entry["response"]), ("meteo Paris", "Beau."))
        self.assertEqual((entry["model_calls"], entry["tokens"], entry["latency_s"]), (2, 20, 1.0))
        self.assertEqual([(t["name"], t["ok"]) for t in entry["tool_calls"]], [("weather", True)])
        self.assertEqual(entry["tool_calls"][0]["arguments"], {"city": "Paris"})

    # Teste : la version du prompt systeme (prompt_versions de la config) est notee, pour comparer avant et apres un changement.
    def test_the_prompt_version_is_journaled(self):
        self.run_journaled(["salut"], [reply("ok")])
        self.assertEqual(read_lines(self.journal)[0]["prompt_version"], load_config().tool_prompt_version)

    # Teste : chaque tour est une ligne : deux messages donnent deux lignes, dans l'ordre.
    def test_one_line_per_turn(self):
        self.run_journaled(["un", "deux"], [reply("1"), reply("2")])
        self.assertEqual([e["prompt"] for e in read_lines(self.journal)], ["un", "deux"])

    # Teste : sans session nommee, l'identifiant du journal est un code court propre a l'execution, le meme pour tous les
    # tours de cette execution.
    def test_turns_of_one_run_share_a_short_run_id(self):
        self.run_journaled(["un", "deux"], [reply("1"), reply("2")])
        ids = {e["session"] for e in read_lines(self.journal)}
        self.assertEqual(len(ids), 1)
        self.assertRegex(ids.pop(), r"^[0-9a-f]{8}$")

    # Teste : avec une session nommee, l'identifiant du journal est le nom de la session : le meme d'un lancement a l'autre,
    # ce qui relie tous les tours d'une meme conversation reprise.
    def test_a_named_session_is_the_journal_id_across_launches(self):
        with tempfile.TemporaryDirectory() as sessions:
            overrides = {"sessions_dir": Path(sessions)}
            for text in ("un", "deux"):
                self.run_cli([text], [reply("ok")], [*self.MODEL, "--session", "lisbonne"], interaction_log=self.journal, config_overrides=overrides)
        self.assertEqual({e["session"] for e in read_lines(self.journal)}, {"lisbonne"})

    # Teste : apres /new, le journal change d'identifiant : chaque conversation a le sien, et les tours d'une meme
    # conversation gardent le leur.
    def test_new_gives_the_next_conversation_its_own_journal_id(self):
        self.run_journaled(["un", "deux", "/new", "trois"], [reply("1"), reply("2"), reply("3")])
        ids = [e["session"] for e in read_lines(self.journal)]
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[1], ids[2])

    # Teste : un outil dont le resultat contient une erreur est journalise comme un echec, avec le message d'erreur.
    def test_a_failing_tool_is_journaled_with_its_error(self):
        failing = {"weather": lambda args: mcp_result({"error": "api HS"})}
        from tests.helpers import FakeMcpClient
        from src import agent
        mcp = mock.patch.object(agent, "Client", side_effect=lambda server: FakeMcpClient(failing))
        self.run_journaled(["meteo"], [call("weather", {"city": "Paris"}), reply("Indisponible.")], mcp=mcp)
        tool = read_lines(self.journal)[0]["tool_calls"][0]
        self.assertEqual((tool["ok"], tool["error"]), (False, "api HS"))

    # Teste : un tour en erreur est journalise avec le statut "error" et le message, sans reponse.
    def test_a_failed_turn_is_journaled_with_its_error(self):
        self.run_journaled(["salut"], [OllamaError("Ollama est tombe.")] * 3)
        (entry,) = read_lines(self.journal)
        self.assertEqual((entry["status"], entry["error"], entry["response"]), (STATUS_ERROR, "Ollama est tombe.", ""))

    # Teste : un tour interrompu par Ctrl+C est journalise avec le statut "interrupted".
    def test_an_interrupted_turn_is_journaled(self):
        self.run_journaled(["salut"], [KeyboardInterrupt()])
        self.assertEqual(read_lines(self.journal)[0]["status"], STATUS_INTERRUPTED)

    # Teste : un tour arrete par le plafond d'iterations est journalise avec le statut "max_iterations" (et non "error").
    def test_a_turn_stopped_by_the_cap_is_journaled_as_max_iterations(self):
        self.run_journaled(
            ["meteo"], [call("weather", {"city": "Paris"})] * 2, config_overrides={"max_iterations": 2}
        )
        entry = read_lines(self.journal)[0]
        self.assertEqual(entry["status"], STATUS_MAX_ITERATIONS)
        self.assertEqual(len(entry["tool_calls"]), 2)

    # Teste : un tour arrete parce que le modele redemande le meme appel d'outil est journalise avec le statut "error" et le
    # message d'arret, les deux appels executes restant visibles.
    def test_a_turn_stopped_for_repetition_is_journaled_as_an_error(self):
        self.run_journaled(["meteo"], [call("weather", {"city": "Paris"})] * 3)
        entry = read_lines(self.journal)[0]
        self.assertEqual(entry["status"], STATUS_ERROR)
        self.assertIn("3 fois de suite", entry["error"])
        self.assertEqual(len(entry["tool_calls"]), 2)

    # Teste : un tour en chat simple (modele sans outils) est journalise avec le type "chat".
    def test_a_simple_chat_turn_is_journaled_as_chat(self):
        self.run_journaled(["bonjour", "suite"], [reply("a"), reply("b")], drop_tools_on_first_call=True)
        self.assertEqual([e["kind"] for e in read_lines(self.journal)], [KIND_AGENT, KIND_CHAT])

    # Teste : sans chemin de journal (None), la CLI fonctionne normalement et n'ecrit aucun fichier.
    def test_no_journal_file_when_it_is_disabled(self):
        code, _, _, _ = self.run_cli(["salut"], [reply("ok")], self.MODEL)
        self.assertEqual(code, 0)
        self.assertFalse(self.journal.exists())


# 5. Metriques ------------------------------------------------------------------------------------------------------

def entry(ts="2026-10-26T10:00:00", status="ok", latency=10.0, kind="agent", tools=(), **extra):
    return {"ts": ts, "session": "s", "kind": kind, "model": "qwen3:8b", "prompt_version": "v1", "status": status,
            "latency_s": latency, "tokens": 100, "model_calls": 2, "tool_calls": list(tools), **extra}


def tool(name, ok=True, duration=1.0):
    return {"name": name, "ok": ok, "duration_s": duration}


class LoadEntriesTests(TempDirTestCase):
    # Teste : un journal qui n'existe pas encore n'est pas une erreur : aucune interaction, aucune ligne ignoree.
    def test_a_missing_journal_is_empty(self):
        self.assertEqual(metrics.load_entries(self.dir / "absent.jsonl"), ([], 0))

    # Teste : les lignes vides sont ignorees, les lignes illisibles (pas du JSON, ou JSON qui n'est pas un objet) sont
    # comptees pour pouvoir le signaler, et les bonnes lignes restent lues.
    def test_blank_and_unreadable_lines_are_skipped_and_counted(self):
        path = self.dir / "j.jsonl"
        path.write_text('{"a": 1}\n\n{pas du json\n[1, 2]\n{"b": 2}\n', encoding="utf-8")
        self.assertEqual(metrics.load_entries(path), ([{"a": 1}, {"b": 2}], 2))


class AggregateTests(unittest.TestCase):
    # Teste : le percentile 95 est le "rang le plus proche" : sur 1 a 20, c'est 19 ; sur une seule valeur, c'est elle.
    def test_percentile_uses_the_nearest_rank(self):
        self.assertEqual(metrics.percentile(list(range(1, 21)), 95), 19)
        self.assertEqual(metrics.percentile([7.0], 95), 7.0)
        self.assertEqual(metrics.percentile([3, 1, 2], 100), 3)

    # Teste : le resume compte les statuts et les types de tour, et les sessions distinctes.
    def test_summary_counts_statuses_kinds_and_sessions(self):
        entries = [entry(), entry(status="error"), entry(status="interrupted"), entry(kind="plan", session="autre")]
        summary = metrics.summarize(entries)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["statuses"], {"ok": 2, "error": 1, "interrupted": 1, "max_iterations": 0, "blocked": 0})
        self.assertEqual(summary["kinds"], {"agent": 3, "plan": 1})
        self.assertEqual(summary["sessions"], 2)

    # Teste : le taux d'erreur compte les erreurs et les plafonds atteints, mais pas une interruption volontaire (Ctrl+C).
    def test_the_error_rate_ignores_voluntary_interruptions(self):
        entries = [entry(), entry(status="error"), entry(status="max_iterations"), entry(status="interrupted")]
        self.assertEqual(metrics.summarize(entries)["error_rate"], 0.5)

    # Teste : la latence est resumee par sa moyenne, sa mediane, son p95 et son pire cas.
    def test_latency_statistics(self):
        latency = metrics.summarize([entry(latency=v) for v in (10, 20, 30, 100)])["latency"]
        self.assertEqual((latency["mean"], latency["median"], latency["p95"], latency["worst"]), (40, 25, 100, 100))

    # Teste : sans aucune latence mesuree (tours en erreur), il n'y a pas de statistiques de latence (None).
    def test_no_latency_statistics_without_measures(self):
        self.assertIsNone(metrics.summarize([entry(latency=None)])["latency"])

    # Teste : les tokens et les appels au modele sont additionnes ou moyennes.
    def test_tokens_and_model_calls(self):
        summary = metrics.summarize([entry(), entry()])
        self.assertEqual((summary["tokens_total"], summary["tokens_mean"], summary["calls_mean"]), (200, 100, 2))

    # Teste : l'usage de chaque outil est compte : nombre d'appels, echecs et durees.
    def test_tool_usage_counts_calls_failures_and_durations(self):
        entries = [entry(tools=[tool("weather", duration=1.0), tool("search_flights", ok=False, duration=3.0)]),
                   entry(tools=[tool("weather", duration=2.0)])]
        tools = metrics.summarize(entries)["tools"]
        self.assertEqual((tools["weather"]["calls"], tools["weather"]["failures"], tools["weather"]["durations"]), (2, 0, [1.0, 2.0]))
        self.assertEqual((tools["search_flights"]["calls"], tools["search_flights"]["failures"]), (1, 1))

    # Teste : le regroupement par jour trie les groupes par date et donne, pour chacun, le nombre de tours, la latence
    # moyenne et le nombre d'echecs.
    def test_grouping_by_day(self):
        entries = [entry(ts="2026-10-27T09:00:00", latency=30), entry(ts="2026-10-26T09:00:00", latency=10),
                   entry(ts="2026-10-26T18:00:00", latency=20, status="error")]
        rows = metrics.group_rows(entries, metrics._day)
        self.assertEqual([r["key"] for r in rows], ["2026-10-26", "2026-10-27"])
        self.assertEqual((rows[0]["turns"], rows[0]["latency_mean"], rows[0]["failed"]), (2, 15, 1))


class DashboardTests(unittest.TestCase):
    def text(self, entries):
        return metrics.format_dashboard(entries, "logs/interactions.jsonl")

    # Teste : sans interaction, le tableau de bord le dit et invite a discuter d'abord.
    def test_an_empty_journal_says_so(self):
        text = self.text([])
        self.assertIn("Aucune interaction journalisée", text)
        self.assertIn("Discute avec SensAI", text)

    # Teste : avec des interactions, le tableau de bord montre ses sections : fiabilite, latence, outils, evolution par jour,
    # par version de prompt et par modele.
    def test_the_dashboard_has_every_section(self):
        text = self.text([entry(tools=[tool("weather")]), entry(ts="2026-10-27T10:00:00")])
        for section in ("Fiabilité", "Latence d'un tour", "Outils", "Évolution par jour", "Par version de prompt", "Par modèle"):
            with self.subTest(section=section):
                self.assertIn(section, text)

    # Teste : les nombres sont ecrits a la francaise (virgule decimale) et le taux d'erreur est affiche en pourcentage.
    def test_numbers_use_the_french_decimal_comma(self):
        text = self.text([entry(latency=12.5), entry(status="error", latency=7.5)])
        self.assertIn("taux d'erreur : 50,0 %", text)
        self.assertIn("moyenne 10,00 s", text)

    # Teste : un outil qui echoue apparait dans la table des outils avec son nombre d'echecs.
    def test_a_failing_tool_shows_its_failures(self):
        text = self.text([entry(tools=[tool("search_flights", ok=False), tool("search_flights")])])
        row = next(line for line in text.splitlines() if line.strip().startswith("search_flights"))
        self.assertEqual(row.split()[:3], ["search_flights", "2", "1"])

    # Teste : les lignes illisibles du journal sont signalees en bas du tableau de bord, avec leur nombre.
    def test_unreadable_lines_are_reported(self):
        text = metrics.format_dashboard([entry()], "j", skipped=3)
        self.assertIn("3 ligne(s) illisible(s) ignorée(s)", text)

    # Teste : si le journal est desactive dans la config (chemin None), la vue explique comment l'activer.
    def test_a_disabled_journal_is_explained(self):
        self.assertIn("désactivé", metrics.dashboard(None))


# 6. /stats et --stats ----------------------------------------------------------------------------------------------

class StatsCommandTests(CliTestCase, TempDirTestCase):
    MODEL = ["--model", "m"]

    def setUp(self):
        TempDirTestCase.setUp(self)
        self.journal = self.dir / "interactions.jsonl"

    def write_journal(self, entries):
        self.journal.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")

    # Teste : --stats affiche le tableau de bord et quitte avec le code 0 sans jamais contacter Ollama (ni verifier un modele).
    def test_stats_option_prints_the_dashboard_without_contacting_ollama(self):
        self.write_journal([entry()])
        code, out, _, fake = self.run_cli([], [], ["--stats"], interaction_log=self.journal)
        self.assertEqual(code, 0)
        self.assertIn("1 interaction(s)", out)
        self.assertEqual((fake.requests, fake.checked_models), ([], []))

    # Teste : --stats sur un journal qui n'existe pas encore le dit au lieu d'echouer.
    def test_stats_option_without_a_journal_says_so(self):
        code, out, _, _ = self.run_cli([], [], ["--stats"], interaction_log=self.journal)
        self.assertEqual(code, 0)
        self.assertIn("Aucune interaction journalisée", out)

    # Teste : la commande /stats, dans la conversation, affiche le meme tableau de bord et ne contacte pas le modele.
    def test_the_stats_command_prints_the_dashboard_in_the_conversation(self):
        self.write_journal([entry(), entry(status="error")])
        _, out, _, fake = self.run_cli(["/stats"], [], self.MODEL, interaction_log=self.journal)
        self.assertIn("2 interaction(s)", out)
        self.assertEqual(fake.requests, [])

    # Teste : de bout en bout : une conversation ecrit le journal, et un lancement suivant avec --stats la voit.
    def test_a_conversation_then_stats_shows_it(self):
        self.run_cli(["salut"], [reply("ok")], self.MODEL, interaction_log=self.journal)
        _, out, _, _ = self.run_cli([], [], ["--stats"], interaction_log=self.journal)
        self.assertIn("1 interaction(s) (agent 1)", out)

    # Teste : si le journal est desactive dans la config, /stats l'explique.
    def test_the_stats_command_explains_a_disabled_journal(self):
        _, out, _, _ = self.run_cli(["/stats"], [], self.MODEL)
        self.assertIn("Journal des interactions désactivé", out)


# 7. Latence --------------------------------------------------------------------------------------------------------

class LatencyTests(TempDirTestCase):
    # Teste : les mesures de plusieurs appels au modele sont additionnees (durees, tokens, chargement), le delai avant le
    # premier token est celui du premier appel qui en a un, et le texte est celui du dernier appel.
    def test_combining_the_calls_of_a_turn(self):
        combined = combine_results([result(total=1, ttft=None, tokens=5, load=2.0, content="a"), result(total=2, ttft=0.4, tokens=7, load=1.0, content="b")])
        self.assertEqual((combined.total_s, combined.ttft_s, combined.eval_count, combined.load_s, combined.content), (3, 0.4, 12, 3.0, "b"))

    # Teste : si aucune mesure n'est connue pour un champ (tokens, chargement), le total reste inconnu (None), pas 0.
    def test_unknown_figures_stay_unknown(self):
        combined = combine_results([result(tokens=None, load=None)])
        self.assertIsNone(combined.eval_count)
        self.assertIsNone(combined.load_s)

    # Teste : la ligne affichee apres un tour donne la latence, le premier token, les tokens et, pour un tour d'agent, le
    # nombre d'appels (au singulier ou au pluriel).
    def test_the_line_shown_after_a_turn(self):
        tracker = LatencyTracker("m", None)
        self.assertEqual(tracker.format_turn(result(total=4.8, ttft=2.1, tokens=120)), "[latence 4.80 s | 1er token 2.10 s | 120 tokens]")
        self.assertTrue(tracker.format_turn(result(), calls=1).endswith("| 1 appel]"))
        self.assertTrue(tracker.format_turn(result(), calls=3).endswith("| 3 appels]"))

    # Teste : les morceaux inconnus (pas de premier token, pas de tokens) sont omis de la ligne.
    def test_unknown_pieces_are_left_out_of_the_line(self):
        self.assertEqual(LatencyTracker("m", None).format_turn(result(total=1.0, ttft=None, tokens=None)), "[latence 1.00 s]")

    # Teste : chaque tour ajoute une ligne JSON au log de latence (date, modele, latence, premier token, tokens, chargement),
    # avec le nombre d'appels pour un tour d'agent seulement.
    def test_each_turn_appends_a_line_to_the_latency_log(self):
        path = self.dir / "logs" / "latency.jsonl"
        tracker = LatencyTracker("qwen3:8b", path)
        tracker.record(result(total=1.5, ttft=0.2, tokens=50, load=2.0), calls=2)
        tracker.record(result(total=0.7))
        first, second = read_lines(path)
        self.assertEqual(set(first), {"ts", "model", "latency_s", "ttft_s", "eval_count", "load_s", "calls"})
        self.assertEqual((first["model"], first["latency_s"], first["load_s"], first["calls"]), ("qwen3:8b", 1.5, 2.0, 2))
        self.assertNotIn("calls", second)

    # Teste : sans fichier de log (None), les mesures sont quand meme gardees en memoire pour la moyenne.
    def test_samples_are_kept_even_without_a_log_file(self):
        tracker = LatencyTracker("m", None)
        tracker.record(result(total=2.0))
        self.assertEqual(tracker.samples, [2.0])

    # Teste : si le log ne peut pas etre ecrit, il se desactive avec un seul avertissement, et les mesures restent gardees.
    def test_a_log_write_failure_warns_once(self):
        (self.dir / "fichier").write_text("pas un dossier")
        tracker = LatencyTracker("m", self.dir / "fichier" / "latency.jsonl")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                tracker.record(result())
        self.assertEqual(err.getvalue().count("log de latence désactivé"), 1)
        self.assertEqual(len(tracker.samples), 3)

    # Teste : sans aucun tour, il n'y a pas de resume de latence a afficher (None).
    def test_no_summary_without_turns(self):
        self.assertIsNone(LatencyTracker("m", None).summary())

    # Teste : le resume a la fermeture donne la moyenne ; a partir de deux tours, il donne aussi la moyenne hors premier tour
    # (le premier tour charge le modele en memoire, donc il est plus lent).
    def test_the_summary_gives_the_average_and_the_average_without_the_first_turn(self):
        tracker = LatencyTracker("m", None)
        tracker.record(result(total=5.0))
        self.assertEqual(tracker.summary(), "Latence moyenne : 5.00 s sur 1 tour(s)")
        tracker.record(result(total=2.0))
        tracker.record(result(total=3.0))
        self.assertEqual(tracker.summary(), "Latence moyenne : 3.33 s sur 3 tour(s) (2.50 s hors 1er tour)")


class CliLatencyLogTests(CliTestCase, TempDirTestCase):
    # Teste : la CLI ecrit une ligne dans le log de latence par tour, avec le nombre d'appels au modele du tour.
    def test_the_cli_writes_one_latency_line_per_turn(self):
        path = self.dir / "latency.jsonl"
        self.run_cli(["meteo", "merci"], [call("weather", {"city": "Paris"}), reply("Beau."), reply("De rien.")], ["--model", "m"], latency_log=path)
        lines = read_lines(path)
        self.assertEqual([line["calls"] for line in lines], [2, 1])
        self.assertEqual(lines[0]["latency_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
