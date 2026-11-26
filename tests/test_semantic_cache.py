# A6 : Cache semantique - src/tools/web_cache.py (search_in_cache_semantic) et son usage dans src/tools/web_search.py.
#
# Le cache simple (T3) ne reconnait qu'une question identique. Le cache semantique compare le SENS des questions : chaque
# question est transformee en vecteur (un "embedding") par un modele, et une question dont le vecteur est assez proche
# (similarite cosinus >= 0,80) d'une question deja en cache recoit la meme reponse, sans appel reseau.
#
# Ce fichier remplace le vrai modele (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, qui se telecharge sur
# Internet) par un faux encodeur dont on choisit les vecteurs : on sait donc exactement quelle similarite chaque paire de
# questions a. Le vrai modele n'est pas teste ici (il faudrait le telecharger).
#
#   1. Comparaison : une question proche est reconnue, une question eloignee non, le seuil est a 0,80.
#   2. Robustesse  : cache vide, entree abimee.
#   3. Chargement  : le modele n'est charge qu'une fois, au premier besoin, et avec le bon nom.
#   4. Recherche web en mode semantique : une question reformulee evite l'appel a Tavily.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import contextlib
import io
import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np

from tests.tools_import import import_server_side, server_config

web_search = import_server_side("web_search")
web_cache = import_server_side("web_cache")
SERVER_CONFIG = server_config()


# Vecteur unitaire dont la similarite cosinus avec [1, 0, 0] vaut exactement `cosine`.
def at_cosine(cosine):
    return [cosine, math.sqrt(1 - cosine ** 2), 0.0]


BASE = [1.0, 0.0, 0.0]


# Faux modele d'embeddings : encode(texte) renvoie le vecteur choisi pour ce texte. Comme le vrai modele, il renvoie
# des nombres a virgule de 32 bits (float32) : cos_sim refuse de comparer du float32 avec du float64.
class FakeEncoder:
    def __init__(self, vectors):
        self.vectors = {text: np.array(vector, dtype=np.float32) for text, vector in vectors.items()}
        self.encoded = []

    def encode(self, text):
        self.encoded.append(text)
        return self.vectors[text]


def entry(query, embedding, result):
    return {"query": query, "embedding": list(embedding), "result": result}


# 1. Comparaison ----------------------------------------------------------------------------------------------------

class SimilarityTests(unittest.TestCase):
    def lookup(self, query_vector, cache):
        encoder = FakeEncoder({"question": query_vector})
        with mock.patch.object(web_cache, "model", encoder):
            return web_cache.search_in_cache_semantic("question", cache)

    # Teste : une question dont le sens est tres proche (similarite 0,99) d'une question en cache recoit son resultat.
    def test_a_close_question_gets_the_cached_result(self):
        self.assertEqual(self.lookup(at_cosine(0.99), [entry("q", BASE, ["R"])]), ["R"])

    # Teste : une question sans rapport (similarite 0) ne recoit rien : le resultat est None.
    def test_an_unrelated_question_gets_nothing(self):
        self.assertIsNone(self.lookup([0.0, 1.0, 0.0], [entry("q", BASE, ["R"])]))

    # Teste : le seuil est 0,80 : une similarite de 0,81 est acceptee.
    def test_similarity_just_above_the_threshold_is_accepted(self):
        self.assertEqual(self.lookup(at_cosine(0.81), [entry("q", BASE, ["R"])]), ["R"])

    # Teste : le seuil est 0,80 : une similarite de 0,79 est refusee.
    def test_similarity_just_below_the_threshold_is_refused(self):
        self.assertIsNone(self.lookup(at_cosine(0.79), [entry("q", BASE, ["R"])]))

    # Teste : quand plusieurs questions du cache depassent le seuil, c'est le resultat de la plus proche qui est rendu.
    def test_the_closest_cached_question_wins(self):
        cache = [entry("a", BASE, ["A"]), entry("b", at_cosine(0.9), ["B"])]
        self.assertEqual(self.lookup(at_cosine(0.99), cache), ["A"])  # 0,99 avec A et environ 0,95 avec B
        self.assertEqual(self.lookup(at_cosine(0.9), cache), ["B"])   # 1,00 avec B et 0,90 avec A

    # Teste : la question est transformee en vecteur par le modele, une seule fois par recherche.
    def test_the_question_is_encoded_once(self):
        encoder = FakeEncoder({"question": BASE})
        with mock.patch.object(web_cache, "model", encoder):
            web_cache.search_in_cache_semantic("question", [entry("q", BASE, ["R"]), entry("r", BASE, ["R"])])
        self.assertEqual(encoder.encoded, ["question"])


# 2. Robustesse -----------------------------------------------------------------------------------------------------

class RobustnessTests(unittest.TestCase):
    # Teste : avec un cache vide, la recherche rend None sans erreur.
    def test_empty_cache_gives_nothing(self):
        encoder = FakeEncoder({"question": BASE})
        with mock.patch.object(web_cache, "model", encoder):
            self.assertIsNone(web_cache.search_in_cache_semantic("question", []))

    # Teste : une entree de cache sans vecteur est ignoree avec un message, et les autres entrees sont quand meme lues.
    def test_a_malformed_entry_is_skipped_and_the_others_are_still_searched(self):
        encoder = FakeEncoder({"question": BASE})
        cache = [{"query": "abimee", "result": ["X"]}, entry("q", BASE, ["R"])]
        with mock.patch.object(web_cache, "model", encoder), contextlib.redirect_stdout(io.StringIO()) as out:
            result = web_cache.search_in_cache_semantic("question", cache)
        self.assertEqual(result, ["R"])
        self.assertIn("malformed", out.getvalue())

    # Teste : une entree du cache semantique plus vieille que 6h est ignoree et retiree du cache.
    def test_expired_cache_entries_are_skipped_and_removed(self):
        encoder = FakeEncoder({"question": BASE})
        cache = [{
            "query": "question",
            "embedding": list(BASE),
            "result": ["R"],
            "timestamp": 1_700_000_000,
        }]
        with mock.patch.object(web_cache, "model", encoder), \
                mock.patch.object(web_cache.time, "time", return_value=1_700_000_000 + 6 * 60 * 60 + 1):
            self.assertIsNone(web_cache.search_in_cache_semantic("question", cache))
        self.assertEqual(cache, [])


# 3. Chargement du modele -------------------------------------------------------------------------------------------

class ModelLoadingTests(unittest.TestCase):
    # Teste : le modele n'est charge qu'au premier besoin, puis garde : deux recherches ne le chargent qu'une fois.
    def test_the_model_is_loaded_lazily_and_only_once(self):
        encoder = FakeEncoder({"question": BASE})

        def fake_init():
            web_cache.model = encoder

        with mock.patch.object(web_cache, "model", None), \
                mock.patch.object(web_cache, "init_cache_model_semantic", side_effect=fake_init) as init:
            web_cache.search_in_cache_semantic("question", [])
            web_cache.search_in_cache_semantic("question", [])
        self.assertEqual(init.call_count, 1)


# 4. Recherche web en mode semantique -------------------------------------------------------------------------------

class SemanticSearchTests(unittest.TestCase):
    QUESTION = "Quelle est la capitale de la France ?"
    REPHRASED = "Quelle est la ville qui est la capitale francaise ?"
    OTHER = "Quel temps fait-il a Rome ?"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.semantic_file = str(Path(tmp.name) / "semantic.json")
        self.tavily = mock.Mock()
        self.tavily.search.return_value = {"results": [{"title": "Paris", "url": "https://x.test/paris", "content": "Paris est la capitale."}]}
        # web_search normalise la question (minuscules) avant de l'encoder : le faux encodeur est donc indexe en minuscules.
        self.encoder = FakeEncoder({
            self.QUESTION.lower(): BASE,
            self.REPHRASED.lower(): at_cosine(0.95),
            self.OTHER.lower(): [0.0, 1.0, 0.0],
        })
        for patcher in (
            mock.patch.object(web_cache, "SEMANTIC_CACHE_FILE", self.semantic_file),
            mock.patch.object(web_cache, "model", self.encoder),
            mock.patch.object(web_search, "TavilyClient", return_value=self.tavily),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        SERVER_CONFIG.set_active_config(replace(SERVER_CONFIG.load_config(), cache_mode="semantic"))
        self.addCleanup(SERVER_CONFIG.set_active_config, None)

    # Teste : une premiere recherche en mode semantique appelle Tavily et enregistre la question (normalisee en
    # minuscules) avec son vecteur.
    def test_first_search_is_stored_with_its_embedding(self):
        web_search.web_search_basic(self.QUESTION)
        stored = json.loads(Path(self.semantic_file).read_text(encoding="utf-8"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["query"], self.QUESTION.lower())
        self.assertEqual(stored[0]["embedding"], BASE)

    # Teste : c'est l'observable d'A6 : une question reformulee ("capitale francaise") est servie par le cache et n'appelle
    # pas Tavily une seconde fois.
    def test_a_rephrased_question_is_served_from_the_cache(self):
        first = web_search.web_search_basic(self.QUESTION)
        second = web_search.web_search_basic(self.REPHRASED)
        self.assertEqual(second, first)
        self.assertEqual(self.tavily.search.call_count, 1)

    # Teste : une question d'un autre sujet n'est pas servie par le cache : Tavily est appele, et elle est ajoutee au cache.
    def test_a_different_subject_calls_tavily_and_is_added_to_the_cache(self):
        web_search.web_search_basic(self.QUESTION)
        web_search.web_search_basic(self.OTHER)
        self.assertEqual(self.tavily.search.call_count, 2)
        self.assertEqual(len(json.loads(Path(self.semantic_file).read_text(encoding="utf-8"))), 2)

    # Teste : le mode semantique utilise son propre fichier de cache, pas celui du cache simple.
    def test_semantic_mode_does_not_touch_the_naive_cache_file(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(web_cache, "NAIF_CACHE_FILE", str(Path(tmp) / "naif.json")):
            web_search.web_search_basic(self.QUESTION)
            self.assertFalse((Path(tmp) / "naif.json").exists())


if __name__ == "__main__":
    unittest.main()
