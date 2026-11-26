# X1 : Interface web - l'API FastAPI de src/web/server.py (lancee par web.py).
#
# L'interface web est un front React (dossier web/) qui parle a cette API. Elle utilise le meme coeur que la CLI
# (run_agent, sessions, profils). Ce fichier teste l'API avec le client de test de FastAPI : le serveur n'est pas lance,
# Ollama est remplace par un faux modele, et les conversations sont ecrites dans un dossier temporaire.
#
# Non teste ici : le front React lui-meme (affichage, bouton Arreter, liste des conversations a l'ecran), car le projet
# n'a pas d'outillage de test JavaScript. Le serveur MCP n'est pas lance non plus (il est teste dans test_mcp.py).
#
#   1. Etat et choix   : sante, modeles installes, profils disponibles.
#   2. Conversations   : creer, lister, rouvrir.
#   3. Discussion      : flux d'evenements, outils, modele et profil choisis, sauvegarde.
#   4. Erreurs         : conversation inconnue, message vide, panne du modele, generation deja en cours.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src.config import set_active_config
from src.ollama_client import OllamaError
from src.web import server
from tests.harness import WEATHER_TOOL
from tests.helpers import AgentTestCase, FakeClient, call, reply


class WebFakeClient(FakeClient):
    installed_models = ["qwen3:8b", "mistral:latest"]

    def list_models(self):
        return self.installed_models


# Les evenements d'un flux SSE ("data: {...}" separes par une ligne vide).
def events_of(response):
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]


class WebTestCase(AgentTestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sessions = Path(tmp.name)
        self.config = replace(self.config, sessions_dir=self.sessions)
        set_active_config(self.config)
        server.conversations.clear()
        self.addCleanup(server.conversations.clear)
        self.addCleanup(self.release_lock)
        self.http = TestClient(server.app)
        for patcher in (
            mock.patch.object(server, "sanitize_user_message", side_effect=lambda text, model=None: text),
            mock.patch.object(server, "validate_message_scope", return_value=(True, "IN_SCOPE")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def release_lock(self):
        if server.generation_lock.locked():
            server.generation_lock.release()

    def use(self, script, **kwargs):
        self.client = WebFakeClient(script, **kwargs)
        from src import model_funcs
        model_funcs.set_client(self.client)
        return self.client

    def new_conversation(self):
        return self.http.post("/api/conversations").json()["conversation_id"]

    def chat(self, conversation_id, message="salut", model="m", profile=""):
        with self.mcp(WEATHER_TOOL):
            return self.http.post("/api/chat", json={"conversation_id": conversation_id, "message": message, "model": model, "profile": profile})


# 1. Etat et choix --------------------------------------------------------------------------------------------------

class StatusTests(WebTestCase):
    # Teste : /api/health repond "ok" : c'est ce que le front interroge pour savoir que le serveur tourne.
    def test_health(self):
        response = self.http.get("/api/health")
        self.assertEqual((response.status_code, response.json()), (200, {"status": "ok"}))

    # Teste : /api/models donne le modele par defaut de la config, les modeles recommandes et suggeres (installables depuis
    # le front) et les modeles installes dans Ollama (liste deroulante du front).
    def test_models_lists_the_default_and_the_installed_ones(self):
        self.use([])
        body = self.http.get("/api/models").json()
        self.assertEqual(body, {
            "default": self.config.tool_model,
            "recommended": server.RECOMMENDED_MODELS,
            "suggested": server.SUGGESTED_MODELS,
            "installed": ["qwen3:8b", "mistral:latest"],
        })

    # Teste : si Ollama est injoignable, /api/models repond 502 avec le message de l'erreur.
    def test_models_reports_an_unreachable_ollama_as_502(self):
        self.use([])
        self.client.list_models = mock.Mock(side_effect=OllamaError("Impossible de joindre Ollama."))
        response = self.http.get("/api/models")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Impossible de joindre Ollama", response.json()["detail"])

    # Teste : /api/profiles liste les profils voyageur disponibles (config/profiles), dont les deux profils d'exemple.
    def test_profiles_lists_the_available_ones(self):
        profiles = self.http.get("/api/profiles").json()["profiles"]
        self.assertIn("budget-serre", profiles)
        self.assertIn("gourmet", profiles)

    # Teste : sans dossier de profils dans la config, la liste est vide (pas d'erreur).
    def test_profiles_is_empty_without_a_profiles_folder(self):
        set_active_config(replace(self.config, profiles_dir=None))
        self.assertEqual(self.http.get("/api/profiles").json(), {"profiles": []})


# 2. Conversations --------------------------------------------------------------------------------------------------

class ConversationTests(WebTestCase):
    # Teste : creer une conversation renvoie son identifiant, qui est un nom de session automatique (date et heure) ; le
    # fichier n'est ecrit qu'au premier tour.
    def test_creating_a_conversation_returns_a_session_name_without_writing_a_file(self):
        conversation_id = self.new_conversation()
        self.assertRegex(conversation_id, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")
        self.assertEqual(list(self.sessions.iterdir()), [])

    # Teste : sans dossier de sessions dans la config, l'identifiant est un code aleatoire et la conversation reste en memoire.
    def test_without_a_sessions_folder_the_id_is_random(self):
        set_active_config(replace(self.config, sessions_dir=None))
        self.assertRegex(self.new_conversation(), r"^[0-9a-f]{32}$")

    # Teste : la liste des conversations montre, pour chaque conversation sauvegardee, son identifiant, son titre (le
    # premier message de l'utilisateur) et sa date de modification, la plus recente en premier.
    def test_the_list_shows_id_title_and_date_most_recent_first(self):
        self.use([reply("a"), reply("b")])
        first = self.new_conversation()
        self.chat(first, "premiere question")
        second = self.new_conversation()
        self.chat(second, "seconde question")
        os.utime(self.sessions / f"{first}.json", (100, 100))
        listed = self.http.get("/api/conversations").json()["conversations"]
        self.assertEqual([c["title"] for c in listed], ["seconde question", "premiere question"])
        self.assertEqual(listed[0]["id"], second)
        self.assertRegex(listed[0]["updated"], r"^\d{2}/\d{2} \d{2}:\d{2}$")

    # Teste : deux conversations creees coup sur coup (meme seconde), avant le premier message de la premiere, ont des
    # identifiants differents ; sinon la seconde ecrase la premiere en memoire et elles partagent le meme fichier.
    def test_two_conversations_created_back_to_back_get_different_ids(self):
        with mock.patch("src.session.time.strftime", return_value="2026-10-29_12-00-00"):
            first, second = self.new_conversation(), self.new_conversation()
        self.assertNotEqual(first, second)
        self.assertEqual((first, second), ("2026-10-29_12-00-00", "2026-10-29_12-00-00-2"))

    # Teste : deux conversations creees coup sur coup restent independantes : un message dans la seconde n'apparait pas dans
    # la premiere, et chacune a son propre fichier.
    def test_two_conversations_created_back_to_back_stay_independent(self):
        self.use([reply("a"), reply("b")])
        with mock.patch("src.session.time.strftime", return_value="2026-10-29_12-00-00"):
            first, second = self.new_conversation(), self.new_conversation()
        self.chat(first, "pour la premiere")
        self.chat(second, "pour la seconde")
        users = lambda conversation_id: [
            m["content"] for m in json.loads((self.sessions / f"{conversation_id}.json").read_text(encoding="utf-8"))["messages"] if m["role"] == "user"
        ]
        self.assertEqual((users(first), users(second)), (["pour la premiere"], ["pour la seconde"]))

    # Teste : la liste ignore un fichier de session abime au lieu de tout casser.
    def test_the_list_skips_a_corrupted_session_file(self):
        (self.sessions / "abimee.json").write_text("{pas du json", encoding="utf-8")
        self.assertEqual(self.http.get("/api/conversations").json(), {"conversations": []})

    # Teste : sans dossier de sessions, la liste est vide.
    def test_the_list_is_empty_without_a_sessions_folder(self):
        set_active_config(replace(self.config, sessions_dir=None))
        self.assertEqual(self.http.get("/api/conversations").json(), {"conversations": []})

    # Teste : rouvrir une conversation renvoie ses messages (sans le prompt systeme) et la remet en memoire : la discussion
    # peut reprendre, et le modele recoit l'historique.
    def test_reopening_a_conversation_returns_its_messages_and_lets_it_continue(self):
        self.use([reply("Salut"), reply("Ok")])
        conversation_id = self.new_conversation()
        self.chat(conversation_id, "bonjour")
        server.conversations.clear()
        messages = self.http.get(f"/api/conversations/{conversation_id}").json()["messages"]
        self.assertEqual([(m["role"], m["content"]) for m in messages], [("user", "bonjour"), ("assistant", "Salut")])
        self.chat(conversation_id, "suite")
        sent = self.client.requests[-1]["messages"]
        self.assertEqual([m["content"] for m in sent if m["role"] == "user"], ["bonjour", "suite"])

    # Teste : rouvrir une conversation inconnue repond 404.
    def test_reopening_an_unknown_conversation_is_a_404(self):
        response = self.http.get("/api/conversations/inconnue")
        self.assertEqual(response.status_code, 404)
        self.assertIn("introuvable", response.json()["detail"])

    # Teste : rouvrir une conversation dont le fichier est abime repond 500 avec le message de l'erreur.
    def test_reopening_a_corrupted_conversation_is_a_500_with_the_message(self):
        (self.sessions / "abimee.json").write_text("{pas du json", encoding="utf-8")
        response = self.http.get("/api/conversations/abimee")
        self.assertEqual(response.status_code, 500)
        self.assertIn("illisible", response.json()["detail"])


# 3. Discussion -----------------------------------------------------------------------------------------------------

class ChatTests(WebTestCase):
    # Teste : /api/chat repond par un flux d'evenements (text/event-stream), pour afficher la reponse au fil de l'eau.
    def test_the_answer_is_a_server_sent_event_stream(self):
        self.use([reply("Bonjour")])
        response = self.chat(self.new_conversation())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))

    # Teste : le flux contient l'etape de la boucle ("step", que le front ignore pour l'instant), les morceaux de texte
    # ("token"), la reponse finale ("done_tool"), puis les statistiques du tour (latence, premier token, tokens, nombre
    # d'appels) que le front affiche sous la reponse.
    def test_the_stream_has_tokens_then_the_answer_then_the_stats(self):
        self.use([reply("Bonjour")])
        events = events_of(self.chat(self.new_conversation()))
        self.assertEqual([e["type"] for e in events], ["step", "token", "done_tool", "stats"])
        self.assertEqual(events[0], {"type": "step", "n": 1, "max": self.config.max_iterations})
        self.assertEqual("".join(e["content"] for e in events if e["type"] == "token"), "Bonjour")
        self.assertEqual(events[-2], {"type": "done_tool", "content": "Bonjour"})
        stats = events[-1]
        self.assertGreaterEqual(stats.pop("latency_s"), 0)
        self.assertEqual(stats, {"type": "stats", "first_token_s": 0.1, "tokens": 10, "calls": 1})

    # Teste : les appels d'outils apparaissent dans le flux (le front les montre en gris) : appel avec ses arguments, puis
    # resultat, et les statistiques cumulent les deux appels au modele.
    def test_tool_calls_and_results_are_streamed(self):
        self.use([call("weather", {"city": "Paris"}), reply("Beau.")])
        events = events_of(self.chat(self.new_conversation(), "meteo"))
        useful = [e for e in events if e["type"] in ("tool_call", "tool_result", "stats")]
        self.assertEqual(useful[0], {"type": "tool_call", "name": "weather", "arguments": {"city": "Paris"}})
        self.assertEqual(useful[1], {"type": "tool_result", "name": "weather", "result": {"temp": 20, "city": "Paris"}})
        self.assertEqual(useful[2]["calls"], 2)

    # Teste : le modele choisi dans la liste deroulante du front est celui qui est utilise.
    def test_the_model_chosen_in_the_front_is_used(self):
        self.use([reply("ok")])
        self.chat(self.new_conversation(), model="autre-modele")
        self.assertEqual(self.client.requests[0]["model"], "autre-modele")

    # Teste : le profil choisi dans l'en-tete est ajoute au prompt systeme envoye au modele.
    def test_the_profile_chosen_in_the_header_is_added_to_the_system_prompt(self):
        self.use([reply("ok")])
        self.chat(self.new_conversation(), profile="budget-serre")
        system = self.client.requests[0]["messages"][0]["content"]
        self.assertTrue(system.startswith(self.config.tool_system_prompt))
        self.assertIn("- budget_total_eur : 600", system)

    # Teste : sans profil choisi, le prompt systeme est celui de la config, sans section de profil.
    def test_no_profile_means_the_plain_system_prompt(self):
        self.use([reply("ok")])
        self.chat(self.new_conversation(), profile="")
        self.assertEqual(self.client.requests[0]["messages"][0]["content"], self.config.tool_system_prompt)

    # Teste : le profil peut changer d'un message a l'autre dans la meme conversation (il est reapplique a chaque message).
    def test_the_profile_can_change_between_messages(self):
        self.use([reply("a"), reply("b")])
        conversation_id = self.new_conversation()
        self.chat(conversation_id, "un", profile="")
        self.chat(conversation_id, "deux", profile="gourmet")
        self.assertNotIn("Profil du voyageur", self.client.requests[0]["messages"][0]["content"])
        self.assertIn("- budget_total_eur : 2500", self.client.requests[1]["messages"][0]["content"])

    # Teste : apres un tour, la conversation est sauvegardee dans le dossier des sessions, sous son identifiant, comme
    # celles de la CLI.
    def test_the_conversation_is_saved_to_a_session_file(self):
        self.use([reply("Salut")])
        conversation_id = self.new_conversation()
        self.chat(conversation_id, "bonjour")
        saved = json.loads((self.sessions / f"{conversation_id}.json").read_text(encoding="utf-8"))
        self.assertEqual([(m["role"], m["content"]) for m in saved["messages"]], [("user", "bonjour"), ("assistant", "Salut")])

    # Teste : l'historique est conserve d'un message a l'autre : le modele recoit le tour precedent.
    def test_history_is_kept_between_messages(self):
        self.use([reply("a"), reply("b")])
        conversation_id = self.new_conversation()
        self.chat(conversation_id, "un")
        self.chat(conversation_id, "deux")
        roles = [m["role"] for m in self.client.requests[1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    # Teste : le verrou de generation est libere a la fin d'un tour : on peut enchainer plusieurs messages.
    def test_the_generation_lock_is_released_after_a_turn(self):
        self.use([reply("a"), reply("b")])
        conversation_id = self.new_conversation()
        self.chat(conversation_id, "un")
        self.assertFalse(server.generation_lock.locked())
        self.assertEqual(events_of(self.chat(conversation_id, "deux"))[-2]["type"], "done_tool")


# 4. Erreurs --------------------------------------------------------------------------------------------------------

class ChatErrorTests(WebTestCase):
    # Teste : ecrire dans une conversation inconnue repond 404.
    def test_an_unknown_conversation_is_a_404(self):
        self.use([])
        response = self.chat("inconnue")
        self.assertEqual(response.status_code, 404)
        self.assertIn("introuvable", response.json()["detail"])

    # Teste : un message vide ou fait d'espaces repond 400 "Message vide", sans appeler le modele.
    def test_a_blank_message_is_a_400(self):
        self.use([])
        response = self.chat(self.new_conversation(), message="   ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Message vide", response.json()["detail"])
        self.assertEqual(self.client.requests, [])

    def test_an_unavailable_ollama_during_the_scope_check_is_a_502(self):
        self.use([])
        with mock.patch.object(server, "validate_message_scope", side_effect=OllamaError("Impossible de joindre Ollama.")):
            response = self.chat(self.new_conversation())
        self.assertEqual(response.status_code, 502)
        self.assertIn("Impossible de joindre Ollama", response.json()["detail"])

    # Teste : un profil inconnu repond 400 avec le message de l'erreur, sans appeler le modele.
    def test_an_unknown_profile_is_a_400(self):
        self.use([])
        response = self.chat(self.new_conversation(), profile="inconnu")
        self.assertEqual(response.status_code, 400)
        self.assertIn("introuvable", response.json()["detail"])
        self.assertEqual(self.client.requests, [])

    # Teste : une panne d'Ollama pendant le tour arrive dans le flux comme un evenement "error" (le front l'affiche), et la
    # conversation n'est pas modifiee.
    def test_a_model_failure_is_streamed_as_an_error_event(self):
        self.use([OllamaError("Ollama est tombe.")] * 3)
        conversation_id = self.new_conversation()
        events = events_of(self.chat(conversation_id))
        self.assertEqual(events[-1], {"type": "error", "message": "Ollama est tombe."})
        self.assertEqual(list(self.sessions.iterdir()), [])

    # Teste : une exception inattendue dans un tour est rapportee dans le flux ("Erreur inattendue (...)"), pas par un
    # plantage du serveur, et le verrou de generation est libere.
    def test_an_unexpected_exception_is_streamed_and_releases_the_lock(self):
        self.use([])
        with mock.patch.object(server, "run_agent", side_effect=RuntimeError("boom")):
            events = events_of(self.chat(self.new_conversation()))
        self.assertEqual(events[-1], {"type": "error", "message": "Erreur inattendue (RuntimeError: boom)"})
        self.assertFalse(server.generation_lock.locked())

    # Teste : si une reponse est deja en cours de generation, un nouveau message recoit un evenement "error" qui le dit,
    # et ne lance pas une seconde generation.
    def test_a_second_generation_while_one_is_running_is_refused(self):
        self.use([reply("ok")])
        conversation_id = self.new_conversation()
        server.generation_lock.acquire()
        events = events_of(self.chat(conversation_id))
        self.assertEqual([e["type"] for e in events], ["error"])
        self.assertIn("déjà en cours", events[0]["message"])
        self.assertEqual(self.client.requests, [])


if __name__ == "__main__":
    unittest.main()
