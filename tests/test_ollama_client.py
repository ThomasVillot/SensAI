# B1 : Chatbot CLI (socle) - le client Ollama (src/ollama_client.py).
#
# Tout appel au modele passe par l'API HTTP d'Ollama, via requests, sans framework LLM. Ce fichier teste ce client
# sans Ollama : requests.get et requests.post sont remplaces par de fausses reponses.
#
#   1. Connexion et modeles : lister les modeles installes, verifier qu'un modele existe, joindre Ollama.
#   2. Streaming            : lecture de la reponse morceau par morceau, appels d'outils, mesures de latence.
#   3. Contenu de la requete : ce qui part vers Ollama (options, outils, think, format).
#   4. Erreurs              : Ollama absent, modele absent, reponse coupee... toujours un message lisible.
#   5. Fonctions refusees   : un modele qui refuse "tools" ou "think" est rappele sans cette option.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import json
import unittest
from unittest import mock

import requests

from src.model_funcs import contains_invalid_response_pattern
from src.ollama_client import ChatResult, OllamaClient, OllamaError


# Fausse reponse HTTP d'Ollama. lines : ce que iter_lines() renvoie, dans l'ordre ; un dict est encode en JSON, des
# octets sont renvoyes tels quels, une exception est levee a cet endroit du flux.
class FakeResponse:
    def __init__(self, status_code=200, lines=(), body=None, text=""):
        self.status_code = status_code
        self.lines = list(lines)
        self.body = body
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_lines(self):
        for item in self.lines:
            if isinstance(item, BaseException):
                raise item
            yield item if isinstance(item, bytes) else json.dumps(item).encode()

    def json(self):
        if self.body is ValueError:
            raise ValueError("pas du json")
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def chunk(content="", done=False, **extra):
    message = {"role": "assistant", "content": content}
    message.update(extra.pop("message", {}))
    return {"message": message, "done": done, **extra}


FINAL = chunk(done=True, eval_count=42, load_duration=2_500_000_000)


def make_client(**kwargs):
    warnings = []
    client = OllamaClient("http://ollama.test:11434/", 5, 300, warn=warnings.append, **kwargs)
    client.warnings = warnings
    return client


def play(client, responses, **call_kwargs):
    with mock.patch("src.ollama_client.requests.post", side_effect=responses) as post:
        events = list(client.chat_events("m", [{"role": "user", "content": "salut"}], **call_kwargs))
    return events, post


# Comme play, mais renvoie une copie de chaque requete envoyee : le client retire des options de la requete entre deux
# essais, et un mock garderait la version modifiee.
def play_recording(client, responses, **call_kwargs):
    sent, remaining = [], iter(responses)

    def post(url, json=None, **kwargs):
        sent.append(dict(json))
        return next(remaining)

    with mock.patch("src.ollama_client.requests.post", side_effect=post):
        events = list(client.chat_events("m", [{"role": "user", "content": "salut"}], **call_kwargs))
    return events, sent


# 1. Connexion et modeles -------------------------------------------------------------------------------------------

class ModelListTests(unittest.TestCase):
    def list_with(self, **get_kwargs):
        with mock.patch("src.ollama_client.requests.get", **get_kwargs) as get:
            return make_client().list_models(), get

    # Teste : la liste des modeles installes est lue sur GET /api/tags, et le "/" final de l'adresse est ignore.
    def test_list_models_reads_the_installed_names(self):
        response = FakeResponse(body={"models": [{"name": "qwen3:8b"}, {"name": "mistral:latest"}]})
        names, get = self.list_with(return_value=response)
        self.assertEqual(names, ["qwen3:8b", "mistral:latest"])
        self.assertEqual(get.call_args.args[0], "http://ollama.test:11434/api/tags")

    # Teste : un modele demande sans etiquette ("mistral") est reconnu s'il est installe avec ":latest".
    def test_ensure_model_accepts_the_latest_tag(self):
        response = FakeResponse(body={"models": [{"name": "mistral:latest"}]})
        with mock.patch("src.ollama_client.requests.get", return_value=response):
            make_client().ensure_model("mistral")

    # Teste : un modele absent donne un message lisible avec la commande "ollama pull" et la liste des modeles installes.
    def test_ensure_model_missing_explains_how_to_install_it(self):
        response = FakeResponse(body={"models": [{"name": "mistral:latest"}]})
        with mock.patch("src.ollama_client.requests.get", return_value=response):
            with self.assertRaises(OllamaError) as caught:
                make_client().ensure_model("qwen3:8b")
        message = str(caught.exception)
        self.assertIn("ollama pull qwen3:8b", message)
        self.assertIn("mistral:latest", message)

    # Teste : si Ollama ne repond pas du tout, le message nomme l'adresse et suggere "ollama serve".
    def test_unreachable_ollama_is_a_clear_message(self):
        with mock.patch("src.ollama_client.requests.get", side_effect=requests.ConnectionError("refused")):
            with self.assertRaises(OllamaError) as caught:
                make_client().list_models()
        self.assertIn("http://ollama.test:11434", str(caught.exception))
        self.assertIn("ollama serve", str(caught.exception))

    # Teste : si Ollama repond trop lentement, le message annonce le delai depasse.
    def test_read_timeout_is_a_clear_message(self):
        with mock.patch("src.ollama_client.requests.get", side_effect=requests.ReadTimeout()):
            with self.assertRaises(OllamaError) as caught:
                make_client().list_models()
        self.assertIn("délai imparti (300 s)", str(caught.exception))

    # Teste : une reponse qui n'est pas du JSON valide donne un message "illisible", pas un traceback.
    def test_unreadable_model_list_is_a_clear_message(self):
        with mock.patch("src.ollama_client.requests.get", return_value=FakeResponse(body=ValueError)):
            with self.assertRaises(OllamaError) as caught:
                make_client().list_models()
        self.assertIn("illisible", str(caught.exception))


# 2. Streaming ------------------------------------------------------------------------------------------------------

class StreamingTests(unittest.TestCase):
    # Teste : la reponse arrive morceau par morceau (un evenement "token" par morceau de texte), puis un evenement
    # "done" porte le message complet.
    def test_tokens_are_streamed_then_the_full_message_is_returned(self):
        events, _ = play(make_client(), [FakeResponse(lines=[chunk("Bon"), chunk("jour"), FINAL])])
        self.assertEqual([e["type"] for e in events], ["token", "token", "done"])
        self.assertEqual([e["content"] for e in events[:2]], ["Bon", "jour"])
        self.assertEqual(events[-1]["message"], {"role": "assistant", "content": "Bonjour"})

    # Teste : les mesures de latence : tokens generes (eval_count), temps de chargement du modele converti en secondes,
    # temps total, et delai avant le premier token.
    def test_latency_figures_come_with_the_result(self):
        events, _ = play(make_client(), [FakeResponse(lines=[chunk("a"), FINAL])])
        result = events[-1]["result"]
        self.assertIsInstance(result, ChatResult)
        self.assertEqual(result.content, "a")
        self.assertEqual(result.eval_count, 42)
        self.assertAlmostEqual(result.load_s, 2.5)
        self.assertGreaterEqual(result.total_s, result.ttft_s)

    # Teste : une reponse sans aucun texte (seulement un appel d'outil) n'a pas de delai de premier token.
    def test_a_reply_without_text_has_no_first_token_delay(self):
        call = {"function": {"name": "weather", "arguments": {"city": "Paris"}}}
        events, _ = play(make_client(), [FakeResponse(lines=[chunk(message={"tool_calls": [call]}), FINAL])])
        self.assertIsNone(events[-1]["result"].ttft_s)

    # Teste : les appels d'outils demandes par le modele sont collectes dans le message final ("tool_calls").
    def test_tool_calls_are_collected_in_the_final_message(self):
        call = {"function": {"name": "weather", "arguments": {"city": "Paris"}}}
        events, _ = play(make_client(), [FakeResponse(lines=[chunk(message={"tool_calls": [call]}), FINAL])])
        self.assertEqual(events[-1]["message"]["tool_calls"], [call])

    # Teste : le rappel on_call recoit le ChatResult de chaque appel termine (c'est ce qui sert a mesurer la latence).
    def test_on_call_receives_the_result_of_each_finished_call(self):
        client, seen = make_client(), []
        client.on_call = seen.append
        play(client, [FakeResponse(lines=[chunk("a"), FINAL])])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].content, "a")

    # Teste : chat_stream appelle on_token pour chaque morceau recu, des son arrivee, et renvoie le ChatResult.
    def test_chat_stream_calls_on_token_for_each_piece_and_returns_the_result(self):
        pieces = []
        with mock.patch("src.ollama_client.requests.post", return_value=FakeResponse(lines=[chunk("Bon"), chunk("jour"), FINAL])):
            result = make_client().chat_stream("m", [], {}, pieces.append)
        self.assertEqual(pieces, ["Bon", "jour"])
        self.assertEqual(result.content, "Bonjour")


# 3. Contenu de la requete ------------------------------------------------------------------------------------------

class RequestPayloadTests(unittest.TestCase):
    def payload_of(self, **call_kwargs):
        _, post = play(make_client(), [FakeResponse(lines=[FINAL])], **call_kwargs)
        return post.call_args.kwargs["json"]

    # Teste : la requete part sur POST /api/chat en mode streaming, avec le modele et les messages donnes.
    def test_request_goes_to_api_chat_in_streaming_mode(self):
        _, post = play(make_client(), [FakeResponse(lines=[FINAL])])
        self.assertEqual(post.call_args.args[0], "http://ollama.test:11434/api/chat")
        self.assertEqual(post.call_args.kwargs["json"]["stream"], True)
        self.assertEqual(post.call_args.kwargs["json"]["model"], "m")
        self.assertEqual(post.call_args.kwargs["json"]["messages"], [{"role": "user", "content": "salut"}])

    # Teste : les annotations "model" et "profile" que la session ajoute a ses messages ne partent jamais vers Ollama, et
    # l'historique de l'appelant n'est pas modifie.
    def test_the_model_and_profile_annotations_of_session_messages_are_not_sent(self):
        history = [
            {"role": "user", "content": "hi", "model": "m", "profile": "budget"},
            {"role": "assistant", "content": "yo", "model": "m", "profile": "budget"},
        ]
        with mock.patch("src.ollama_client.requests.post", return_value=FakeResponse(lines=[chunk("ok"), FINAL])) as post:
            make_client().chat_stream("m", history, {}, lambda token: None)
        self.assertEqual(post.call_args.kwargs["json"]["messages"], [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}])
        self.assertEqual(history[0], {"role": "user", "content": "hi", "model": "m", "profile": "budget"})  # historique de l'appelant intact

    # Teste : les blobs techniques du runtime Go (ex. {function, {object, <nil>) sont rejectes comme reponses non valides.
    def test_invalid_runtime_markers_are_detected_by_regex(self):
        self.assertTrue(contains_invalid_response_pattern("{function foo"))
        self.assertTrue(contains_invalid_response_pattern("{object foo"))
        self.assertTrue(contains_invalid_response_pattern("<nil>"))
        self.assertFalse(contains_invalid_response_pattern("bonjour, tout va bien"))

    # Teste : options, outils, think et format sont envoyes quand on les donne.
    def test_options_tools_think_and_format_are_sent_when_given(self):
        payload = self.payload_of(options={"num_ctx": 8192}, tools=[{"type": "function"}], think=False, format={"type": "object"})
        self.assertEqual(payload["options"], {"num_ctx": 8192})
        self.assertEqual(payload["tools"], [{"type": "function"}])
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["format"], {"type": "object"})

    # Teste : outils, options, think et format sont absents de la requete quand ils sont vides ou non fournis.
    def test_optional_fields_are_left_out_when_empty(self):
        payload = self.payload_of(options={}, tools=[], think=None, format=None)
        for key in ("options", "tools", "think", "format"):
            self.assertNotIn(key, payload)

    # Teste : pour un modele qu'Ollama a deja refuse avec "tools", les outils ne sont plus renvoyes.
    def test_tools_are_not_sent_to_a_model_known_not_to_support_them(self):
        client = make_client()
        client.tools_unsupported.add("m")
        _, post = play(client, [FakeResponse(lines=[FINAL])], tools=[{"type": "function"}])
        self.assertNotIn("tools", post.call_args.kwargs["json"])


# 4. Erreurs --------------------------------------------------------------------------------------------------------

class ErrorTests(unittest.TestCase):
    def error_of(self, responses, **call_kwargs):
        with self.assertRaises(OllamaError) as caught:
            play(make_client(), responses, **call_kwargs)
        return str(caught.exception)

    # Teste : un modele inconnu (HTTP 404) donne le message "introuvable" avec la commande ollama pull.
    def test_http_404_means_the_model_is_missing(self):
        message = self.error_of([FakeResponse(status_code=404, body={"error": "not found"})])
        self.assertIn("Modèle 'm' introuvable", message)
        self.assertIn("ollama pull m", message)

    # Teste : une autre erreur HTTP est rapportee avec son code et le detail donne par Ollama.
    def test_other_http_errors_report_the_status_and_the_detail(self):
        message = self.error_of([FakeResponse(status_code=500, body={"error": "memoire insuffisante"})])
        self.assertIn("HTTP 500", message)
        self.assertIn("memoire insuffisante", message)

    # Teste : une erreur annoncee dans le flux par Ollama lui-meme est rapportee telle quelle.
    def test_error_sent_inside_the_stream_is_reported(self):
        message = self.error_of([FakeResponse(lines=[{"error": "modele corrompu"}])])
        self.assertIn("Erreur renvoyée par Ollama : modele corrompu", message)

    # Teste : si Ollama est injoignable avant toute reponse, le message dit de le lancer (ollama serve).
    def test_connection_refused_before_any_answer_is_unreachable(self):
        message = self.error_of(requests.ConnectionError("refused"))
        self.assertIn("Impossible de joindre Ollama", message)
        self.assertIn("ollama serve", message)

    # Teste : si la connexion est coupee apres avoir commence a recevoir, le message dit "perdue pendant la reponse".
    def test_connection_lost_mid_answer_is_reported_as_lost(self):
        message = self.error_of([FakeResponse(lines=[chunk("Bon"), requests.ConnectionError("reset")])])
        self.assertIn("Connexion avec Ollama perdue pendant la réponse", message)

    # Teste : un delai de lecture depasse pendant la reponse donne le message de delai.
    def test_read_timeout_mid_answer_is_reported(self):
        message = self.error_of([FakeResponse(lines=[requests.ReadTimeout()])])
        self.assertIn("délai imparti", message)

    # Teste : un flux qui se termine sans le marqueur de fin donne le message "interrompue avant la fin".
    def test_stream_ending_without_the_final_chunk_is_reported(self):
        message = self.error_of([FakeResponse(lines=[chunk("Bon")])])
        self.assertIn("interrompue avant la fin", message)

    # Teste : une ligne qui n'est pas du JSON donne le message "illisible", pas un traceback.
    def test_unreadable_line_is_reported(self):
        message = self.error_of([FakeResponse(lines=[b"ceci n'est pas du json"])])
        self.assertIn("illisible", message)


# 5. Fonctions refusees par le modele -------------------------------------------------------------------------------

class RejectedFeatureTests(unittest.TestCase):
    # Teste : si Ollama refuse "tools" (HTTP 400, "does not support tools"), la requete est refaite sans outils,
    # un avertissement est emis et le modele est memorise comme ne gerant pas les outils.
    def test_tools_rejected_by_the_model_are_dropped_and_the_call_is_retried(self):
        client = make_client()
        refused = FakeResponse(status_code=400, body={"error": "registry.ollama.ai/library/gemma does not support tools"})
        events, sent = play_recording(client, [refused, FakeResponse(lines=[chunk("ok"), FINAL])], tools=[{"type": "function"}])
        self.assertEqual(events[-1]["message"]["content"], "ok")
        self.assertEqual(len(sent), 2)
        self.assertIn("tools", sent[0])
        self.assertNotIn("tools", sent[1])
        self.assertIn("m", client.tools_unsupported)
        self.assertEqual(len(client.warnings), 1)
        self.assertIn("ne gère pas les outils", client.warnings[0])

    # Teste : de meme pour "think" (HTTP 400, "does not support thinking") : le parametre est retire, le modele memorise.
    def test_think_rejected_by_the_model_is_dropped_and_the_call_is_retried(self):
        client = make_client()
        refused = FakeResponse(status_code=400, body={"error": "m does not support thinking"})
        events, sent = play_recording(client, [refused, FakeResponse(lines=[chunk("ok"), FINAL])], think=False)
        self.assertEqual(events[-1]["message"]["content"], "ok")
        self.assertIn("think", sent[0])
        self.assertNotIn("think", sent[1])
        self.assertIn("m", client.think_unsupported)

    # Teste : un HTTP 400 pour une autre raison n'est pas rejoue : c'est une vraie erreur, rapportee avec son detail.
    def test_other_http_400_is_not_retried(self):
        client = make_client()
        with self.assertRaises(OllamaError) as caught:
            play(client, [FakeResponse(status_code=400, body={"error": "requete invalide"})], tools=[{"type": "function"}])
        self.assertIn("HTTP 400", str(caught.exception))
        self.assertEqual(client.tools_unsupported, set())


if __name__ == "__main__":
    unittest.main()
