# Configuration (regle du projet : "rien de code en dur") - src/config.py et le dossier config/.
#
# Le modele, l'adresse d'Ollama, les timeouts, les prompts, les outils, les schemas, les chemins des logs, des sessions et
# de la base : tout vit dans config/ et est lu par src/config.py. Ce fichier verifie ce chargement et ses garde-fous : une
# configuration invalide doit toujours donner un message lisible (ConfigError), jamais un traceback.
#
#   1. Chargement          : ce qui est lu, d'ou, et que le dossier courant n'a pas d'importance.
#   2. Choix du modele     : --model > config/models.yaml > "model" de config/config.json.
#   3. Valeurs invalides   : chaque erreur de configuration est refusee avec un message.
#   4. Reglages facultatifs : les chemins a null desactivent la fonction concernee.
#   5. Configuration active : la configuration partagee par les modules de l'agent.
#   6. Rien en dur         : aucune adresse d'Ollama dans le code Python.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie. Les fichiers de configuration de test sont ecrits
# dans des dossiers temporaires.

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.config import ROOT, ConfigError, active_config, load_config, set_active_config


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    # Ecrit une configuration de test : une copie de config/config.json dont on remplace certaines cles, avec un models.yaml
    # temporaire (chemin absolu : accepte tel quel). Une cle donnee avec la valeur DROP est retiree.
    DROP = object()

    def write_config(self, models_yaml="{}\n", **overrides):
        base = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))
        models = self.tmp / "models.yaml"
        models.write_text(models_yaml, encoding="utf-8")
        base["models_file"] = str(models)
        for key, value in overrides.items():
            if value is self.DROP:
                base.pop(key, None)
            else:
                base[key] = value
        path = self.tmp / "config.json"
        path.write_text(json.dumps(base), encoding="utf-8")
        return path

    def assertConfigError(self, fragment, **kwargs):
        models_yaml = kwargs.pop("models_yaml", "{}\n")
        with self.assertRaises(ConfigError, msg=f"{kwargs}") as caught:
            load_config(self.write_config(models_yaml, **kwargs))
        self.assertIn(fragment, str(caught.exception))


# 1. Chargement -----------------------------------------------------------------------------------------------------

class LoadingTests(ConfigTestCase):
    # Teste : la configuration se charge depuis n'importe quel dossier courant : les chemins sont relatifs a la racine du
    # depot, pas au dossier ou l'on lance le programme.
    def test_loading_does_not_depend_on_the_working_directory(self):
        previous = os.getcwd()
        os.chdir(self.tmp)
        try:
            config = load_config()
        finally:
            os.chdir(previous)
        self.assertTrue(config.tools)

    # Teste : le prompt des outils contient la date du jour (pour que le modele calcule bien "demain" ou "dans 2 mois").
    def test_the_tool_prompt_contains_todays_date(self):
        self.assertIn(datetime.now().astimezone().strftime("%Y-%m-%d"), load_config().tool_system_prompt)

    # Teste : les chemins relatifs de config.json (sessions, profils, logs, base) sont resolus depuis la racine du depot.
    def test_relative_paths_are_resolved_from_the_repository_root(self):
        config = load_config()
        self.assertEqual(config.sessions_dir, ROOT / "sessions")
        self.assertEqual(config.profiles_dir, ROOT / "config" / "profiles")
        self.assertEqual(config.interaction_log, ROOT / "logs" / "interactions.jsonl")
        self.assertEqual(config.latency_log, ROOT / "logs" / "latency.jsonl")
        self.assertEqual(config.trip_db, ROOT / "data" / "trips.db")

    # Teste : la fenetre de contexte (options.num_ctx) est definie dans la config, et assez grande pour le prompt systeme et
    # les definitions d'outils (estimation : 1 token pour 3 caracteres). Sans elle, Ollama tronque le prompt en silence.
    def test_the_context_window_is_large_enough_for_the_prompt_and_the_tools(self):
        config = load_config()
        self.assertIn("num_ctx", config.options)
        estimated_tokens = (len(config.tool_system_prompt) + len(json.dumps(config.tools))) / 3
        self.assertLess(estimated_tokens, config.options["num_ctx"])

    # Teste : l'adresse d'Ollama et les timeouts viennent de config.json.
    def test_the_ollama_address_and_the_timeouts_come_from_config_json(self):
        raw = json.loads((ROOT / "config" / "config.json").read_text(encoding="utf-8"))
        config = load_config()
        self.assertEqual(config.ollama_host, raw["ollama_host"])
        self.assertEqual((config.connect_timeout_s, config.read_timeout_s), (float(raw["connect_timeout_s"]), float(raw["read_timeout_s"])))


# 2. Choix du modele ------------------------------------------------------------------------------------------------

class ModelSelectionTests(ConfigTestCase):
    # Teste : models.yaml passe avant le "model" de config.json, role par role (outils et reponse).
    def test_models_yaml_wins_over_config_json(self):
        config = load_config(self.write_config("default_tool_model: outils-x\ndefault_response_model: reponse-y\n", model="du-config-json"))
        self.assertEqual((config.tool_model, config.response_model), ("outils-x", "reponse-y"))

    # Teste : sans modele dans models.yaml, le "model" de config.json sert de dernier recours pour les deux roles.
    def test_config_json_model_is_the_last_resort(self):
        config = load_config(self.write_config("{}\n", model="du-config-json"))
        self.assertEqual((config.tool_model, config.response_model), ("du-config-json", "du-config-json"))

    # Teste : un seul role peut etre donne par models.yaml ; l'autre retombe sur le "model" de config.json.
    def test_one_role_can_fall_back_while_the_other_is_set(self):
        config = load_config(self.write_config("default_tool_model: outils-x\n", model="du-config-json"))
        self.assertEqual((config.tool_model, config.response_model), ("outils-x", "du-config-json"))

    # Teste : les espaces autour d'un nom de modele sont retires.
    def test_model_names_are_trimmed(self):
        config = load_config(self.write_config("default_tool_model: '  outils-x  '\n"))
        self.assertEqual(config.tool_model, "outils-x")

    # Teste : la temperature de models.yaml s'ajoute aux options du modele et remplace celle de config.json.
    def test_the_temperature_of_models_yaml_wins(self):
        config = load_config(self.write_config("parameters:\n  temperature: 0.2\n", options={"temperature": 0.9, "num_ctx": 4096}))
        self.assertEqual(config.options, {"temperature": 0.2, "num_ctx": 4096})

    # Teste : sans temperature dans models.yaml, les options de config.json restent telles quelles.
    def test_without_a_temperature_in_models_yaml_the_options_are_kept(self):
        config = load_config(self.write_config("{}\n", options={"temperature": 0.9}))
        self.assertEqual(config.options, {"temperature": 0.9})

    # Teste : "stream: false" dans models.yaml est ignore (le streaming est toujours actif) et signale par un avertissement.
    def test_stream_false_is_ignored_with_a_warning(self):
        config = load_config(self.write_config("parameters:\n  stream: false\n"))
        self.assertEqual(len(config.warnings), 1)
        self.assertIn("stream", config.warnings[0])

    # Teste : sans "stream: false", il n'y a aucun avertissement.
    def test_no_warning_by_default(self):
        self.assertEqual(load_config(self.write_config("{}\n")).warnings, ())

    # Teste : un nom de modele vide ou qui n'est pas du texte dans models.yaml est refuse.
    def test_invalid_model_names_in_models_yaml_are_refused(self):
        for yaml_text in ("default_tool_model: '   '\n", "default_response_model: 3\n"):
            with self.subTest(yaml=yaml_text):
                self.assertConfigError("nom de modèle non vide", models_yaml=yaml_text)

    # Teste : une section "parameters" qui n'est pas un objet, ou une temperature qui n'est pas un nombre, est refusee.
    def test_invalid_parameters_in_models_yaml_are_refused(self):
        self.assertConfigError("'parameters' doit être un objet", models_yaml="parameters: 3\n")
        self.assertConfigError("temperature", models_yaml="parameters:\n  temperature: chaud\n")
        self.assertConfigError("temperature", models_yaml="parameters:\n  temperature: true\n")


# 3. Valeurs invalides ----------------------------------------------------------------------------------------------

class ValidationTests(ConfigTestCase):
    # Teste : un fichier de configuration introuvable donne une ConfigError qui nomme le fichier.
    def test_a_missing_config_file_is_a_clear_error(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(Path("n_existe_pas.json"))
        self.assertIn("introuvable", str(caught.exception))

    # Teste : un fichier qui n'est pas du JSON, ou pas un objet, est refuse.
    def test_a_config_file_that_is_not_a_json_object_is_refused(self):
        bad = self.tmp / "c.json"
        for content, fragment in (("pas du json", "illisible"), ("[1, 2]", "objet JSON")):
            with self.subTest(content=content):
                bad.write_text(content, encoding="utf-8")
                with self.assertRaises(ConfigError) as caught:
                    load_config(bad)
                self.assertIn(fragment, str(caught.exception))

    # Teste : les cles obligatoires manquantes sont toutes citees dans le message.
    def test_missing_required_keys_are_all_listed(self):
        bad = self.tmp / "c.json"
        bad.write_text('{"model": "x"}', encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            load_config(bad)
        for key in ("ollama_host", "max_iterations", "max_repeated_actions", "prompt_versions", "tools_file"):
            self.assertIn(key, str(caught.exception))

    # Teste : des valeurs du mauvais type ou hors limites sont refusees : plafond d'iterations, seuil d'actions repetees
    # (au moins 2), think, options, timeouts, modele, fenetre de rechargement.
    def test_invalid_values_are_refused(self):
        invalid = (
            {"max_iterations": 0}, {"max_iterations": "huit"}, {"max_iterations": True}, {"think": "non"}, {"options": 3},
            {"max_repeated_actions": 1}, {"max_repeated_actions": "trois"}, {"max_repeated_actions": True},
            {"connect_timeout_s": "vite"}, {"read_timeout_s": None}, {"model": "  "}, {"model": 3},
            {"max_reloaded_turns": 0}, {"max_reloaded_turns": "vingt"}, {"max_reloaded_turns": True},
            {"prompt_versions": 3},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ConfigError):
                    load_config(self.write_config("{}\n", **overrides))

    # Teste : une version de prompt qui n'existe pas dans prompts.yaml est refusee, avec le nom du prompt dans le message.
    def test_an_unknown_prompt_version_is_refused(self):
        self.assertConfigError("tool_model", prompt_versions={"tool_model": "v999"})

    # Teste : une version de prompt manquante dans prompt_versions est refusee.
    def test_a_missing_prompt_version_is_refused(self):
        self.assertConfigError("tool_model", prompt_versions={"pii_check": "v1"})

    # Teste : un fichier de prompt systeme introuvable est refuse avec un message clair.
    def test_an_unreadable_system_prompt_file_is_refused(self):
        self.assertConfigError("Prompt système illisible", system_prompt_file=str(self.tmp / "absent.txt"))

    # Teste : un fichier d'outils sans section "tools" est refuse.
    def test_a_tools_file_without_a_tools_section_is_refused(self):
        tools = self.tmp / "tools.yaml"
        tools.write_text("autre: 1\n", encoding="utf-8")
        self.assertConfigError("section 'tools'", tools_file=str(tools))

    # Teste : un outil sans nom (function.name) est refuse.
    def test_a_tool_without_a_name_is_refused(self):
        tools = self.tmp / "tools.yaml"
        tools.write_text("tools:\n  sans_nom:\n    type: function\n    function:\n      description: x\n", encoding="utf-8")
        self.assertConfigError("sans 'function.name'", tools_file=str(tools))

    # Teste : un fichier de schemas dont la section "schemas" n'est pas un objet est refuse.
    def test_schemas_that_are_not_an_object_are_refused(self):
        schemas = self.tmp / "schemas.yaml"
        schemas.write_text("schemas: 3\n", encoding="utf-8")
        self.assertConfigError("'schemas' doit être un objet", schemas_file=str(schemas))

    # Teste : un fichier YAML illisible est refuse avec un message qui nomme le fichier.
    def test_an_unreadable_yaml_file_is_refused(self):
        self.assertConfigError("illisible", models_yaml="a: [1, 2\n")


# 4. Reglages facultatifs -------------------------------------------------------------------------------------------

class OptionalSettingsTests(ConfigTestCase):
    # Teste : sessions, profils, fenetre de rechargement, journaux et base peuvent etre mis a null : la fonction concernee
    # est alors desactivee (valeur None).
    def test_null_settings_disable_their_feature(self):
        config = load_config(self.write_config(
            "{}\n", sessions_dir=None, profiles_dir=None, max_reloaded_turns=None, latency_log=None, interaction_log=None, trip_db=None))
        self.assertEqual(
            (config.sessions_dir, config.profiles_dir, config.max_reloaded_turns, config.latency_log, config.interaction_log, config.trip_db),
            (None,) * 6,
        )

    # Teste : ces memes reglages peuvent aussi etre absents du fichier : meme resultat, sans erreur.
    def test_absent_settings_are_the_same_as_null(self):
        config = load_config(self.write_config(
            "{}\n", sessions_dir=self.DROP, profiles_dir=self.DROP, max_reloaded_turns=self.DROP, trip_db=self.DROP))
        self.assertEqual((config.sessions_dir, config.profiles_dir, config.max_reloaded_turns, config.trip_db), (None,) * 4)

    # Teste : "think" vaut false et "options" est vide quand ils sont absents de config.json.
    def test_think_and_options_have_defaults(self):
        config = load_config(self.write_config("{}\n", think=self.DROP, options=self.DROP))
        self.assertIs(config.think, False)
        self.assertEqual(config.options, {})


# 5. Configuration active -------------------------------------------------------------------------------------------

class ActiveConfigTests(unittest.TestCase):
    def tearDown(self):
        set_active_config(None)

    # Teste : sans configuration fixee, la configuration active est chargee depuis config/config.json a la premiere utilisation,
    # puis gardee.
    def test_it_is_loaded_on_first_use_then_kept(self):
        set_active_config(None)
        first = active_config()
        self.assertEqual(first.tool_model, load_config().tool_model)
        self.assertIs(active_config(), first)

    # Teste : set_active_config impose une configuration (c'est ce que fait la CLI apres --model et --config).
    def test_a_config_can_be_imposed(self):
        imposed = load_config()
        set_active_config(imposed)
        self.assertIs(active_config(), imposed)

    # Teste : set_active_config(None) oublie la configuration : la suivante est rechargee depuis le disque.
    def test_resetting_forces_a_reload(self):
        set_active_config(load_config())
        before = active_config()
        set_active_config(None)
        self.assertIsNot(active_config(), before)


# 6. Rien en dur ----------------------------------------------------------------------------------------------------

class NothingHardcodedTests(unittest.TestCase):
    # Teste : l'adresse d'Ollama (localhost, port 11434) n'est ecrite dans aucun module qui parle au modele : elle vient
    # uniquement de config/config.json.
    def test_no_ollama_address_in_the_python_code(self):
        for name in ("ollama_client.py", "model_funcs.py", "agent.py", "cli.py", "session.py", "web/server.py"):
            text = (ROOT / "src" / name).read_text(encoding="utf-8")
            self.assertNotIn("localhost", text, name)
            self.assertNotIn("11434", text, name)


if __name__ == "__main__":
    unittest.main()
