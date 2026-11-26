# T3 : Recherche web - src/tools/web_search.py, src/tools/search_activities.py, cache simple de src/tools/web_cache.py.
#
# Les outils web_search_basic et web_search_deep interrogent Tavily ; search_activities s'appuie dessus. Ce fichier teste
# ces outils sans reseau : le client Tavily est remplace par un faux, et les fichiers de cache sont ecrits dans un
# dossier temporaire (jamais dans le dossier du projet).
#
#   1. Recherche    : ce qui est demande a Tavily, ce qui est rendu au modele, les sources citees.
#   2. Cache simple : une question deja posee n'appelle plus le reseau, meme hors connexion.
#   3. Panne reseau : un message clair, rien d'ecrit dans le cache.
#   4. Option --cache : la CLI valide le mode de cache et le transmet au serveur d'outils.
#
# Le cache semantique (questions reformulees) est teste dans test_semantic_cache.py.
# Les modules sont importes comme le fait le serveur MCP (voir tests/tools_import.py).
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import requests

from src import agent
from tests.harness import CliTestCase, mcp_result
from tests.helpers import FakeMcpClient, call, reply
from tests.tools_import import import_server_side, server_config

web_search = import_server_side("web_search")
web_cache = import_server_side("web_cache")
search_activities = import_server_side("search_activities")

SERVER_CONFIG = server_config()

TAVILY_RESPONSE = {
    "results": [
        {"title": "Visiter Rome", "url": "https://exemple.test/rome", "content": "R" * 2000, "score": 0.93},
        {"title": "Rome en mai", "url": "https://exemple.test/mai", "content": "court", "score": 0.5},
    ]
}


class WebSearchTestCase(unittest.TestCase):
    cache_mode = "naif"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.naif_file = str(Path(tmp.name) / "naif.json")
        self.semantic_file = str(Path(tmp.name) / "semantic.json")
        self.tavily = mock.Mock()
        self.tavily.search.return_value = TAVILY_RESPONSE
        for patcher in (
            mock.patch.object(web_cache, "NAIF_CACHE_FILE", self.naif_file),
            mock.patch.object(web_cache, "SEMANTIC_CACHE_FILE", self.semantic_file),
            mock.patch.object(web_search, "TavilyClient", return_value=self.tavily),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        SERVER_CONFIG.set_active_config(replace(SERVER_CONFIG.load_config(), cache_mode=self.cache_mode))
        self.addCleanup(SERVER_CONFIG.set_active_config, None)


# 1. Recherche ------------------------------------------------------------------------------------------------------

class SearchTests(WebSearchTestCase):
    # Teste : web_search_basic demande a Tavily 3 resultats au plus, en recherche "basic". La question est normalisee
    # (espaces retires, minuscules) avant d'etre envoyee.
    def test_basic_search_asks_for_three_results_in_basic_depth(self):
        web_search.web_search_basic("Musees a Rome")
        self.tavily.search.assert_called_once_with(query="musees a rome", max_results=3, search_depth="basic")

    # Teste : web_search_deep demande a Tavily 5 resultats au plus, en recherche "advanced" (approfondie), avec la meme
    # normalisation de la question.
    def test_deep_search_asks_for_five_results_in_advanced_depth(self):
        web_search.web_search_deep("Musees a Rome")
        self.tavily.search.assert_called_once_with(query="musees a rome", max_results=5, search_depth="advanced")

    # Teste : seuls le titre, l'adresse (la source) et le contenu de chaque resultat sont rendus au modele : les autres
    # champs de Tavily (comme le score) sont retires pour economiser des tokens.
    def test_results_keep_only_title_url_and_content(self):
        results = web_search.web_search_basic("musees a Rome")
        self.assertEqual([sorted(r) for r in results], [["content", "title", "url"]] * 2)
        self.assertEqual(results[0]["url"], "https://exemple.test/rome")
        self.assertEqual(results[0]["title"], "Visiter Rome")

    # Teste : le contenu de chaque resultat est tronque : 500 caracteres en recherche simple, 1500 en recherche approfondie.
    def test_content_is_truncated_per_search_type(self):
        self.assertEqual(len(web_search.web_search_basic("a")[0]["content"]), 500)
        self.assertEqual(len(web_search.web_search_deep("b")[0]["content"]), 1500)

    # Teste : un contenu plus court que la limite est rendu en entier.
    def test_short_content_is_kept_whole(self):
        self.assertEqual(web_search.web_search_basic("a")[1]["content"], "court")

    # Teste : la cle d'API Tavily est lue dans la variable d'environnement TAVILY_API_KEY.
    def test_api_key_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"TAVILY_API_KEY": "cle-de-test"}):
            web_search.web_search_basic("a")
        web_search.TavilyClient.assert_called_with(api_key="cle-de-test")

    # Teste : search_activities cherche "Activities in <Ville> in <Mois>" (majuscule initiale) avec la recherche approfondie.
    def test_activities_are_a_deep_web_search_for_the_city_and_month(self):
        with mock.patch.object(search_activities, "web_search_deep", return_value=["x"]) as search, \
                mock.patch.object(search_activities, "rejected_places", return_value=[]):
            result = search_activities.search_activities("rome", "mai")
        search.assert_called_once_with("Activities in Rome in Mai")
        self.assertEqual(result, ["x"])

    # Teste : les preferences du voyageur, quand il y en a, sont ajoutees a la question apres deux-points ; des
    # preferences vides (ou faites d'espaces) sont ignorees.
    def test_activities_preferences_are_appended_to_the_query(self):
        with mock.patch.object(search_activities, "web_search_deep", return_value=["x"]) as search, \
                mock.patch.object(search_activities, "rejected_places", return_value=[]):
            search_activities.search_activities("rome", "mai", " adventure sports, outdoor ")
            search_activities.search_activities("rome", "mai", "   ")
        self.assertEqual(
            [c.args for c in search.call_args_list],
            [("Activities in Rome in Mai: adventure sports, outdoor",), ("Activities in Rome in Mai",)],
        )

    def test_activities_drop_the_refused_places_and_add_a_note(self):
        found = [
            {"title": "Visit the Vatican Museums", "url": "u1", "content": "c1"},
            {"title": "Rome in March", "url": "u2", "content": "Walk the Forum. Colosseum tours run daily."},
        ]
        refused = [{"name": "Vatican", "kind": "sight", "decision": "rejected", "reason": "trop de monde"}]
        with mock.patch.object(search_activities, "web_search_deep", return_value=found), \
                mock.patch.object(search_activities, "rejected_places", return_value=refused):
            result = search_activities.search_activities("rome", "mars")
        self.assertIn("Vatican", result[0]["note"])
        self.assertEqual(result[1:], [found[1]])
        self.assertEqual(len(found), 2)

    def test_sentences_that_mention_a_refused_place_are_removed_from_the_content(self):
        refused = [{"name": "Vatican", "kind": "sight", "decision": "rejected", "reason": None}]
        found = [
            {
                "title": "Rome in March",
                "url": "u1",
                "content": "Book the Vatican Museums early. The Colosseum opens at 9.\n### Vatican Museums Tour\nTrevi Fountain · Vatican City · Pantheon",
            },
            {"title": "Only the Vatican", "url": "u2", "content": "x"},
            {"title": "Another page", "url": "u3", "content": "The Vatican is crowded. Skip the Vatican."},
        ]
        result = search_activities.without_rejected_places(found, refused)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["url"], "u1")
        self.assertEqual(result[1]["content"], "The Colosseum opens at 9.\nTrevi Fountain Pantheon")
        self.assertIn("Vatican", found[0]["content"])

    def test_a_refused_place_matches_whole_words_and_ignores_accents_and_case(self):
        refused = [{"name": "Colisée", "kind": "sight", "decision": "rejected", "reason": None}]
        found = [
            {"title": "COLISEE : horaires", "url": "u1", "content": ""},
            {"title": "Colisee-like arenas", "url": "u2", "content": ""},
            {"title": "Colise", "url": "u3", "content": ""},
        ]
        result = search_activities.without_rejected_places(found, refused)
        self.assertEqual([item["url"] for item in result[1:]], ["u3"])

    def test_results_that_are_not_a_list_of_pages_are_returned_unchanged(self):
        refused = [{"name": "Vatican", "kind": "sight", "decision": "rejected", "reason": None}]
        self.assertEqual(search_activities.without_rejected_places("Connexion perdue", refused), "Connexion perdue")
        self.assertEqual(search_activities.without_rejected_places(["x"], []), ["x"])
        self.assertEqual(search_activities.without_rejected_places(["x"], refused)[1:], ["x"])


# 2. Cache simple ---------------------------------------------------------------------------------------------------

class NaiveCacheTests(WebSearchTestCase):
    # Teste : une premiere recherche appelle Tavily puis enregistre la question (normalisee), le resultat et l'heure de
    # l'enregistrement dans le fichier de cache.
    def test_first_search_is_stored_in_the_cache_file(self):
        with mock.patch.object(web_search.time, "time", return_value=1_700_000_000.5):
            results = web_search.web_search_basic("Musees a Rome")
        stored = json.loads(Path(self.naif_file).read_text(encoding="utf-8"))
        self.assertEqual(stored, [{"query": "musees a rome", "result": results, "timestamp": 1_700_000_000.5}])

    # Teste : la meme question une seconde fois est servie par le cache : Tavily n'est appele qu'une fois.
    def test_same_question_is_served_from_the_cache(self):
        first = web_search.web_search_basic("musees a Rome")
        second = web_search.web_search_basic("musees a Rome")
        self.assertEqual(first, second)
        self.assertEqual(self.tavily.search.call_count, 1)

    # Teste : une question differente n'est pas servie par le cache et appelle Tavily.
    def test_a_different_question_calls_tavily(self):
        web_search.web_search_basic("musees a Rome")
        web_search.web_search_basic("restaurants a Rome")
        self.assertEqual(self.tavily.search.call_count, 2)

    # Teste : la question est normalisee avant la recherche dans le cache : majuscules et espaces autour ne changent
    # rien, la meme question est servie par le cache.
    def test_naive_cache_ignores_case_and_surrounding_spaces(self):
        web_search.web_search_basic("musees a rome")
        web_search.web_search_basic("  Musees A Rome ")
        self.assertEqual(self.tavily.search.call_count, 1)

    # Teste : le cache simple reste litteral sur les mots : une question formulee autrement (meme proche) le rate.
    def test_naive_cache_needs_the_same_words(self):
        web_search.web_search_basic("musees a rome")
        web_search.web_search_basic("musees de rome")
        self.assertEqual(self.tavily.search.call_count, 2)

    # Teste : le cache sert de plan B reseau coupe : une question deja posee recoit sa reponse meme si Tavily est
    # injoignable.
    def test_cached_question_still_works_when_the_network_is_down(self):
        first = web_search.web_search_basic("musees a Rome")
        self.tavily.search.side_effect = requests.exceptions.ConnectionError()
        self.assertEqual(web_search.web_search_basic("musees a Rome"), first)

    # Teste : le cache est partage entre recherche simple et approfondie : la meme question n'appelle pas deux fois Tavily.
    def test_basic_and_deep_searches_share_the_cache(self):
        web_search.web_search_basic("musees a Rome")
        web_search.web_search_deep("musees a Rome")
        self.assertEqual(self.tavily.search.call_count, 1)

    # Teste : un fichier de cache abime est traite comme vide (message d'avertissement) : la recherche aboutit quand
    # meme et le cache est reecrit proprement.
    def test_a_corrupted_cache_file_is_treated_as_empty(self):
        Path(self.naif_file).write_text("{pas du json", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            results = web_search.web_search_basic("musees a Rome")
        self.assertIn("malformé", out.getvalue())
        self.assertEqual(len(results), 2)
        self.assertEqual(len(json.loads(Path(self.naif_file).read_text(encoding="utf-8"))), 1)


class CacheFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "cache.json")

    # Teste : un fichier de cache qui n'existe pas encore donne un cache vide.
    def test_missing_file_gives_an_empty_cache(self):
        self.assertEqual(web_cache.load_cache(self.path), [])

    # Teste : un fichier de cache vide (ou fait d'espaces) donne un cache vide.
    def test_blank_file_gives_an_empty_cache(self):
        Path(self.path).write_text("  \n", encoding="utf-8")
        self.assertEqual(web_cache.load_cache(self.path), [])

    # Teste : le cache enregistre puis relu est identique, accents compris (ecrits lisiblement, pas en é).
    def test_save_then_load_keeps_accents(self):
        cache = [{"query": "où manger à Lyon ?", "result": [{"title": "Café"}]}]
        web_cache.save_cache(self.path, cache)
        self.assertEqual(web_cache.load_cache(self.path), cache)
        self.assertIn("où manger", Path(self.path).read_text(encoding="utf-8"))

    # Teste : reset_cache vide le fichier, qui se relit ensuite comme un cache vide.
    def test_reset_empties_the_cache(self):
        web_cache.save_cache(self.path, [{"query": "a", "result": []}])
        web_cache.reset_cache(self.path)
        self.assertEqual(web_cache.load_cache(self.path), [])

    # Teste : la recherche dans le cache simple renvoie le resultat de la question identique, et None s'il n'y en a pas.
    def test_naive_lookup(self):
        cache = [{"query": "a", "result": ["R"]}]
        self.assertEqual(web_cache.search_in_cache_naif("a", cache), ["R"])
        self.assertIsNone(web_cache.search_in_cache_naif("b", cache))


# 3. Panne reseau ---------------------------------------------------------------------------------------------------

class NetworkFailureTests(WebSearchTestCase):
    def search_while_offline(self):
        self.tavily.search.side_effect = requests.exceptions.ConnectionError()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return web_search.web_search_basic("musees a Rome"), err.getvalue()

    # Teste : sans reseau, l'outil rend le message "Connexion perdue" (que le prompt demande de reconnaitre) au lieu de
    # planter.
    def test_lost_connection_returns_a_clear_message(self):
        result, _ = self.search_while_offline()
        self.assertEqual(result, "Connexion perdue")

    # Teste : sans reseau, un avertissement explicatif est ecrit sur stderr et suggere le mode de cache "semantic".
    def test_lost_connection_prints_a_hint_on_stderr(self):
        _, err = self.search_while_offline()
        self.assertIn("connexion perdue", err)
        self.assertIn("semantic", err)

    # Teste : une recherche ratee n'ajoute rien au cache (le fichier peut exister, vide, car chaque recherche purge les
    # entrees expirees).
    def test_a_failed_search_is_not_cached(self):
        self.search_while_offline()
        self.assertEqual(web_cache.load_cache(self.naif_file), [])


# 4. Option --cache -------------------------------------------------------------------------------------------------

class CacheOptionTests(CliTestCase):
    def server_args_for(self, argv):
        servers = []
        fake_mcp = mock.patch.object(
            agent, "Client", side_effect=lambda server: (servers.append(server), FakeMcpClient({"weather": lambda a: mcp_result({})}))[1]
        )
        self.run_cli(["meteo"], [call("weather", {"city": "Paris"}), reply("ok")], ["--model", "m", *argv], mcp=fake_mcp)
        return servers[0].args

    # Teste : sans option, le serveur d'outils est lance avec le cache simple ("naif").
    def test_default_cache_is_naive(self):
        self.assertEqual(self.server_args_for([])[-2:], ["--cache", "naif"])

    # Teste : --cache semantic est transmis au serveur d'outils quand l'agent le lance.
    def test_cache_option_is_passed_to_the_tool_server(self):
        self.assertEqual(self.server_args_for(["--cache", "semantic"])[-2:], ["--cache", "semantic"])

    # Teste : un mode de cache inconnu est refuse avec le code 1 et un message qui donne les valeurs possibles.
    def test_unknown_cache_mode_is_refused(self):
        code, _, err, _ = self.run_cli([], [], ["--model", "m", "--cache", "magique"])
        self.assertEqual(code, 1)
        self.assertIn("'naif' ou 'semantic'", err)

    # Teste : un mode de cache vide est refuse avec le code 1.
    def test_blank_cache_mode_is_refused(self):
        code, _, err, _ = self.run_cli([], [], ["--model", "m", "--cache", " "])
        self.assertEqual(code, 1)
        self.assertIn("vide", err)


if __name__ == "__main__":
    unittest.main()
