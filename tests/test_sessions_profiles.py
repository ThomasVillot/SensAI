# M1 : Session persistante et profils - src/session.py, src/user_profile.py, et leur usage dans src/cli.py.
#
# Deux mecanismes independants. Les sessions : toute conversation est sauvegardee apres chaque tour et peut etre reprise
# (--continue, --session NOM). Les profils : des preferences stables (budget, rythme, contraintes) ajoutees au prompt
# systeme (--profile NOM). Ce n'est pas le modele qui decide : c'est le code qui sauvegarde, recharge et injecte.
#
#   1. Session en memoire : historique, tours, prompt systeme.
#   2. Sauvegarde         : fichier JSON, ecriture atomique, panne d'ecriture.
#   3. Rechargement       : fichier valide, fichier abime, fenetre max_reloaded_turns.
#   4. Noms de session    : nom automatique, derniere session.
#   5. Profils            : lecture, erreurs, mise en forme pour le prompt.
#   6. Sessions dans la CLI : sauvegarde automatique, --session, --continue, erreurs.
#   7. Profils dans la CLI  : --profile, /prompt, erreurs.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie. Les sessions sont ecrites dans des dossiers
# temporaires : rien n'est ecrit dans sessions/ du projet.

import contextlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import load_config
from src.ollama_client import OllamaError
from src.session import SESSION_VERSION, Session, SessionError, latest_session_name, new_session_name
from src.user_profile import ProfileError, available_profiles, load_profile, render_profile
from tests.harness import CliTestCase
from tests.helpers import reply


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


def saved(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Les quatre messages d'un tour d'agent avec un outil : question, appel d'outil, resultat, reponse.
def agent_turn(question, answer):
    return [
        {"role": "user", "content": question},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "weather", "arguments": {"city": "Paris"}}}]},
        {"role": "tool", "tool_name": "weather", "content": '{"temp": 20}'},
        {"role": "assistant", "content": answer},
    ]


# 1. Session en memoire ---------------------------------------------------------------------------------------------

class SessionInMemoryTests(unittest.TestCase):
    # Teste : une session neuve ne contient que le prompt systeme, sans aucun tour.
    def test_new_session_has_only_the_system_prompt(self):
        session = Session("sys")
        self.assertEqual(session.messages, [{"role": "system", "content": "sys"}])
        self.assertEqual(session.turns, 0)

    # Teste : un tour ajoute la question puis la reponse, et compte pour un tour.
    def test_add_turn_appends_the_question_and_the_answer(self):
        session = Session("sys")
        session.add_turn("salut", "bonjour")
        self.assertEqual([m["role"] for m in session.messages], ["system", "user", "assistant"])
        self.assertEqual(session.turns, 1)

    # Teste : messages_with renvoie l'historique plus la nouvelle question, sans modifier l'historique.
    def test_messages_with_does_not_change_the_history(self):
        session = Session("sys")
        full = session.messages_with("salut")
        self.assertEqual(full[-1], {"role": "user", "content": "salut"})
        self.assertEqual(len(session.messages), 1)

    # Teste : la liste renvoyee par messages est une copie : la modifier ne change pas la session.
    def test_messages_returns_a_copy(self):
        session = Session("sys")
        session.messages.append({"role": "user", "content": "intrus"})
        self.assertEqual(len(session.messages), 1)

    # Teste : add_messages enregistre d'un coup tous les messages d'un tour d'agent (appels d'outils compris) et compte
    # pour un seul tour.
    def test_add_messages_records_a_whole_agent_turn_at_once(self):
        session = Session("sys")
        session.add_messages([
            {"role": "user", "content": "meteo"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "weather", "arguments": {}}}]},
            {"role": "tool", "tool_name": "weather", "content": "{}"},
            {"role": "assistant", "content": "Beau."},
        ])
        self.assertEqual(session.turns, 1)
        self.assertEqual(len(session.messages), 5)

    # Teste : set_system_prompt remplace le prompt systeme existant.
    def test_set_system_prompt_replaces_the_existing_one(self):
        session = Session("ancien")
        session.set_system_prompt("nouveau")
        self.assertEqual(session.messages, [{"role": "system", "content": "nouveau"}])

    # Teste : set_system_prompt ajoute un prompt systeme en tete quand il n'y en avait pas.
    def test_set_system_prompt_inserts_one_when_missing(self):
        session = Session()
        session.add_turn("a", "b")
        session.set_system_prompt("sys")
        self.assertEqual(session.messages[0], {"role": "system", "content": "sys"})
        self.assertEqual(len(session.messages), 3)

    # Teste : restart vide l'historique et le compteur de tours, ne garde que le nouveau prompt systeme et conserve le profil.
    def test_restart_gives_an_empty_conversation_and_keeps_the_profile(self):
        session = Session("ancien")
        session.profile = "gourmet"
        session.add_turn("a", "b")
        session.restart("nouveau", None)
        self.assertEqual(session.messages, [{"role": "system", "content": "nouveau"}])
        self.assertEqual(session.turns, 0)
        self.assertEqual(session.profile, "gourmet")


# 2. Sauvegarde -----------------------------------------------------------------------------------------------------

class SaveTests(TempDirTestCase):
    # Teste : chaque tour ajoute est ecrit tout de suite dans le fichier de session, au format {"version", "messages"}.
    def test_each_turn_is_written_to_the_file(self):
        session = Session("sys", self.dir / "s.json")
        session.add_turn("a", "b")
        self.assertEqual(saved(self.dir / "s.json"), {
            "version": SESSION_VERSION,
            "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
        })

    # Teste : le prompt systeme n'est jamais ecrit dans le fichier (il est reconstruit a chaque lancement).
    def test_the_system_prompt_is_never_saved(self):
        session = Session("sys", self.dir / "s.json")
        session.add_turn("a", "b")
        self.assertNotIn("system", [m["role"] for m in saved(self.dir / "s.json")["messages"]])

    # Teste : le dossier de sauvegarde est cree s'il n'existe pas.
    def test_the_folder_is_created_when_missing(self):
        session = Session("sys", self.dir / "nouveau" / "dossier" / "s.json")
        session.add_turn("a", "b")
        self.assertTrue((self.dir / "nouveau" / "dossier" / "s.json").exists())

    # Teste : l'ecriture est atomique (fichier temporaire puis remplacement) : aucun fichier temporaire ne reste.
    def test_no_temporary_file_is_left_behind(self):
        session = Session("sys", self.dir / "s.json")
        session.add_turn("a", "b")
        self.assertEqual([p.name for p in self.dir.iterdir()], ["s.json"])

    # Teste : si l'ecriture echoue (disque plein), l'ancien fichier reste intact, un avertissement est ecrit une seule fois,
    # et la conversation continue en memoire sans erreur.
    def test_a_write_failure_keeps_the_old_file_and_warns_once(self):
        path = self.dir / "s.json"
        session = Session("sys", path)
        session.add_turn("a", "b")
        before = path.read_text(encoding="utf-8")
        err = io.StringIO()
        with mock.patch("src.session.os.replace", side_effect=OSError("disque plein")), contextlib.redirect_stderr(err):
            session.add_turn("c", "d")
            session.add_turn("e", "f")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(err.getvalue().count("sauvegarde de la session désactivée"), 1)
        self.assertEqual(session.turns, 3)

    # Teste : apres restart, les tours suivants vont dans le nouveau fichier ; l'ancien garde exactement ce qu'il avait.
    def test_restart_switches_to_a_new_file_and_leaves_the_old_one_untouched(self):
        session = Session("sys", self.dir / "a.json")
        session.add_turn("un", "r1")
        session.restart("sys", self.dir / "b.json")
        session.add_turn("deux", "r2")
        self.assertEqual([m["content"] for m in saved(self.dir / "a.json")["messages"]], ["un", "r1"])
        self.assertEqual([m["content"] for m in saved(self.dir / "b.json")["messages"]], ["deux", "r2"])

    # Teste : une session sans fichier (sauvegarde desactivee) vit en memoire et ne cree aucun fichier.
    def test_a_session_without_a_path_writes_nothing(self):
        session = Session("sys")
        session.add_turn("a", "b")
        self.assertEqual(list(self.dir.iterdir()), [])

    # Teste : chaque message d'un tour est ecrit avec le modele qui l'a joue (il peut changer d'un tour a l'autre), sans
    # toucher aux autres champs, et l'annotation survit a la reprise de la conversation.
    def test_every_message_of_a_turn_is_saved_with_the_model_that_played_it(self):
        path = self.dir / "s.json"
        session = Session.load(path, "sys")
        session.add_turn("un", "a", "llama3.2")
        session.add_messages(agent_turn("deux", "b"), model="qwen3")
        messages = saved(path)["messages"]
        self.assertEqual(messages[0], {"role": "user", "content": "un", "model": "llama3.2"})
        self.assertEqual(messages[1], {"role": "assistant", "content": "a", "model": "llama3.2"})
        self.assertEqual([m["model"] for m in messages[2:]], ["qwen3"] * 4)  # question, appel d'outil, resultat, reponse
        self.assertEqual(messages[4]["tool_name"], "weather")  # les autres champs sont intacts
        self.assertEqual(Session.load(path, "sys").messages[1:], messages)  # et survivent a la reprise

    # Teste : sans modele donne, les messages n'ont pas de champ "model".
    def test_without_a_model_messages_get_no_model_field(self):
        path = self.dir / "s.json"
        session = Session.load(path, "sys")
        session.add_turn("un", "a")
        self.assertEqual(saved(path)["messages"], [{"role": "user", "content": "un"}, {"role": "assistant", "content": "a"}])

    # Teste : le profil actif au moment de l'ajout est ecrit sur chaque message (il peut changer en cours de conversation,
    # sur le web) ; le profil actif vient du lancement, pas du fichier.
    def test_messages_also_carry_the_profile_active_when_they_were_added(self):
        path = self.dir / "s.json"
        session = Session.load(path, "sys")
        session.add_turn("un", "a", "llama3.2")  # sans profil : pas de champ
        session.profile = "budget"
        session.add_messages(agent_turn("deux", "b"), model="qwen3")
        session.profile = "luxe"
        session.add_turn("trois", "c", "qwen3")
        messages = saved(path)["messages"]
        self.assertEqual(messages[0], {"role": "user", "content": "un", "model": "llama3.2"})
        self.assertEqual([m.get("profile") for m in messages[2:6]], ["budget"] * 4)
        self.assertEqual([m.get("profile") for m in messages[6:]], ["luxe"] * 2)
        self.assertEqual(messages[5], {"role": "assistant", "content": "b", "model": "qwen3", "profile": "budget"})
        self.assertIsNone(Session.load(path, "sys").profile)

    # Teste : un profil est enregistre meme quand aucun modele n'est donne.
    def test_a_profile_is_recorded_even_when_no_model_is_given(self):
        path = self.dir / "s.json"
        session = Session.load(path, "sys")
        session.profile = "budget"
        session.add_turn("un", "a")
        self.assertEqual(saved(path)["messages"][0], {"role": "user", "content": "un", "profile": "budget"})

    # Teste : l'annotation du modele n'altere pas les messages de l'appelant : elle est ajoutee a une copie.
    def test_the_callers_messages_are_not_modified_by_the_model_tag(self):
        turn = agent_turn("un", "a")
        Session("sys").add_messages(turn, model="qwen3")
        self.assertNotIn("model", turn[0])


# 3. Rechargement ---------------------------------------------------------------------------------------------------

class LoadTests(TempDirTestCase):
    def write(self, content, name="s.json"):
        path = self.dir / name
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    # Teste : charger un fichier qui n'existe pas donne une session neuve (avec le prompt systeme), liee a ce fichier.
    def test_loading_a_missing_file_gives_a_new_session(self):
        session = Session.load(self.dir / "absent.json", "sys")
        self.assertEqual(session.messages, [{"role": "system", "content": "sys"}])
        self.assertEqual(session.path, self.dir / "absent.json")

    # Teste : une conversation sauvegardee puis rechargee est identique, appels d'outils compris, et le nombre de tours
    # est retrouve.
    def test_a_saved_conversation_is_restored_identically(self):
        path = self.dir / "s.json"
        original = Session("sys", path)
        original.add_messages([
            {"role": "user", "content": "meteo"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "weather", "arguments": {"city": "Rome"}}}]},
            {"role": "tool", "tool_name": "weather", "content": "{}"},
            {"role": "assistant", "content": "Beau."},
        ])
        original.add_turn("merci", "de rien")
        loaded = Session.load(path, "sys")
        self.assertEqual(loaded.messages, original.messages)
        self.assertEqual(loaded.turns, 2)

    # Teste : une session sauvegardee avant l'ajout du champ "model" se charge toujours, et les tours suivants sont annotes
    # sans toucher aux anciens messages.
    def test_a_session_saved_before_the_model_field_still_loads_and_keeps_going(self):
        old = [{"role": "user", "content": "un"}, {"role": "assistant", "content": "a"}]
        path = self.write({"version": SESSION_VERSION, "messages": old})
        session = Session.load(path, "sys")
        session.add_turn("deux", "b", "llama3.2")
        messages = saved(path)["messages"]
        self.assertEqual(messages[:2], old)
        self.assertEqual(messages[2]["model"], "llama3.2")

    # Teste : le prompt systeme est celui du lancement en cours, pas celui d'avant : modifier la config ou le profil change le
    # comportement meme sur une conversation reprise.
    def test_the_system_prompt_is_the_one_of_the_current_launch(self):
        path = self.dir / "s.json"
        Session("ancien prompt", path).add_turn("a", "b")
        loaded = Session.load(path, "nouveau prompt")
        self.assertEqual(loaded.messages[0], {"role": "system", "content": "nouveau prompt"})

    # Teste : un fichier avec un BOM (ajoute par certains editeurs sous Windows) se charge normalement.
    def test_a_file_with_a_bom_loads(self):
        path = self.dir / "s.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"version": 1, "messages": [{"role": "user", "content": "a"}]}).encode())
        self.assertEqual(Session.load(path, "sys").turns, 1)

    # Teste : un fichier qui n'est pas du JSON donne une SessionError qui nomme le fichier, sans traceback.
    def test_unreadable_json_is_a_clear_error(self):
        path = self.write("{pas du json")
        with self.assertRaises(SessionError) as caught:
            Session.load(path, "sys")
        self.assertIn("illisible", str(caught.exception))
        self.assertIn(str(path), str(caught.exception))

    # Teste : une version de format inconnue est refusee avec un message qui dit quelle version est attendue.
    def test_unknown_version_is_refused(self):
        path = self.write({"version": 99, "messages": []})
        with self.assertRaises(SessionError) as caught:
            Session.load(path, "sys")
        self.assertIn("format inconnu", str(caught.exception))

    # Teste : une liste de messages corrompue (role inconnu, ou pas une liste) est refusee.
    def test_corrupted_messages_are_refused(self):
        for messages in ("pas une liste", [{"role": "robot", "content": "a"}], ["texte"]):
            with self.subTest(messages=messages):
                path = self.write({"version": 1, "messages": messages})
                with self.assertRaises(SessionError) as caught:
                    Session.load(path, "sys")
                self.assertIn("corrompue", str(caught.exception))

    # Teste : une conversation qui ne commence pas par un message de l'utilisateur est refusee.
    def test_a_conversation_must_start_with_a_user_message(self):
        path = self.write({"version": 1, "messages": [{"role": "assistant", "content": "a"}]})
        with self.assertRaises(SessionError) as caught:
            Session.load(path, "sys")
        self.assertIn("commencer par un message user", str(caught.exception))

    # Teste : un fichier de session dont le contenu n'est pas un objet est refuse.
    def test_a_file_that_is_not_an_object_is_refused(self):
        with self.assertRaises(SessionError):
            Session.load(self.write("[1, 2, 3]"), "sys")


class ReloadWindowTests(TempDirTestCase):
    def three_turns(self):
        path = self.dir / "s.json"
        session = Session("sys", path)
        for n in (1, 2, 3):
            session.add_turn(f"q{n}", f"r{n}")
        return path

    # Teste : max_turns limite ce qui est recharge aux derniers tours, et compte les tours plus anciens mis de cote.
    def test_only_the_last_turns_are_reloaded(self):
        loaded = Session.load(self.three_turns(), "sys", max_turns=2)
        self.assertEqual(loaded.turns, 2)
        self.assertEqual(loaded.archived_turns, 1)
        self.assertEqual([m["content"] for m in loaded.messages if m["role"] == "user"], ["q2", "q3"])

    # Teste : sans limite (None), tous les tours sont recharges.
    def test_no_limit_reloads_everything(self):
        loaded = Session.load(self.three_turns(), "sys", max_turns=None)
        self.assertEqual((loaded.turns, loaded.archived_turns), (3, 0))

    # Teste : la fenetre commence toujours par un message de l'utilisateur, meme quand les tours contiennent des
    # appels d'outils (un message d'outil sans l'appel qui l'a declenche perturberait le modele).
    def test_the_window_starts_at_the_beginning_of_a_turn(self):
        path = self.dir / "s.json"
        session = Session("sys", path)
        for n in (1, 2, 3):
            session.add_messages([
                {"role": "user", "content": f"q{n}"},
                {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "weather", "arguments": {}}}]},
                {"role": "tool", "tool_name": "weather", "content": "{}"},
                {"role": "assistant", "content": f"r{n}"},
            ])
        loaded = Session.load(path, "sys", max_turns=2)
        self.assertEqual(loaded.messages[1]["role"], "user")
        self.assertEqual(loaded.messages[1]["content"], "q2")

    # Teste : les tours plus anciens restent dans le fichier : ils ne sont pas perdus a la sauvegarde suivante.
    def test_older_turns_survive_the_next_save(self):
        path = self.three_turns()
        loaded = Session.load(path, "sys", max_turns=1)
        loaded.add_turn("q4", "r4")
        users = [m["content"] for m in saved(path)["messages"] if m["role"] == "user"]
        self.assertEqual(users, ["q1", "q2", "q3", "q4"])


# 4. Noms de session ------------------------------------------------------------------------------------------------

class SessionNameTests(TempDirTestCase):
    # Teste : le nom automatique d'une session est la date et l'heure (AAAA-MM-JJ_HH-MM-SS).
    def test_automatic_name_is_the_date_and_time(self):
        self.assertRegex(new_session_name(self.dir), r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")

    # Teste : si ce nom existe deja, un suffixe -2, puis -3 est ajoute.
    def test_a_taken_name_gets_a_numeric_suffix(self):
        (self.dir / "2026-10-22_16-35-12.json").write_text("{}")
        self.assertEqual(new_session_name(self.dir, "2026-10-22_16-35-12"), "2026-10-22_16-35-12-2")
        (self.dir / "2026-10-22_16-35-12-2.json").write_text("{}")
        self.assertEqual(new_session_name(self.dir, "2026-10-22_16-35-12"), "2026-10-22_16-35-12-3")

    # Teste : un nom deja attribue a une conversation sans fichier (parametre reserved) est evite comme un fichier existant :
    # suffixe -2, puis -3 si celui-ci est aussi reserve.
    def test_a_reserved_name_gets_a_numeric_suffix(self):
        self.assertEqual(new_session_name(self.dir, "2026-10-22_16-35-12", reserved={"2026-10-22_16-35-12"}), "2026-10-22_16-35-12-2")
        taken = {"2026-10-22_16-35-12", "2026-10-22_16-35-12-2"}
        self.assertEqual(new_session_name(self.dir, "2026-10-22_16-35-12", reserved=taken), "2026-10-22_16-35-12-3")

    # Teste : sans noms reserves, le comportement est inchange (le parametre est facultatif).
    def test_reserved_names_are_optional(self):
        self.assertEqual(new_session_name(self.dir, "2026-10-22_16-35-12"), "2026-10-22_16-35-12")

    # Teste : la derniere session est celle dont le fichier a ete modifie le plus recemment.
    def test_latest_session_is_the_most_recently_modified(self):
        for name, when in (("ancienne", 100), ("recente", 200), ("vieille", 50)):
            path = self.dir / f"{name}.json"
            path.write_text("{}")
            os.utime(path, (when, when))
        self.assertEqual(latest_session_name(self.dir), "recente")

    # Teste : sans aucune session, ou si le dossier n'existe pas, il n'y a pas de derniere session (None).
    def test_latest_session_is_none_without_sessions(self):
        self.assertIsNone(latest_session_name(self.dir))
        self.assertIsNone(latest_session_name(self.dir / "absent"))


# 5. Profils --------------------------------------------------------------------------------------------------------

class ProfileTests(TempDirTestCase):
    def profile(self, content, name="p"):
        (self.dir / f"{name}.json").write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")

    # Teste : un profil est un objet JSON aux cles libres, lu dans <dossier>/<nom>.json.
    def test_a_profile_is_read_from_its_file(self):
        self.profile({"budget_total_eur": 600, "rythme": "calme"})
        self.assertEqual(load_profile(self.dir, "p"), {"budget_total_eur": 600, "rythme": "calme"})

    # Teste : un profil avec un BOM se charge normalement.
    def test_a_profile_with_a_bom_loads(self):
        (self.dir / "p.json").write_bytes(b"\xef\xbb\xbf" + b'{"a": 1}')
        self.assertEqual(load_profile(self.dir, "p"), {"a": 1})

    # Teste : sans dossier de profils dans la config (profiles_dir a null), le profil est refuse avec un message.
    def test_no_profiles_folder_is_a_clear_error(self):
        with self.assertRaises(ProfileError) as caught:
            load_profile(None, "p")
        self.assertIn("profiles_dir", str(caught.exception))

    # Teste : un nom de profil avec un "/" ou ".." est refuse : le fichier doit rester dans le dossier prevu.
    def test_invalid_profile_names_are_refused(self):
        for name in ("../secret", "a/b", "", "-debut", " espace"):
            with self.subTest(name=name):
                with self.assertRaises(ProfileError) as caught:
                    load_profile(self.dir, name)
                self.assertIn("Nom de profil invalide", str(caught.exception))

    # Teste : un profil inconnu donne un message qui liste les profils disponibles.
    def test_unknown_profile_lists_the_available_ones(self):
        self.profile({"a": 1}, "gourmet")
        self.profile({"a": 1}, "budget")
        with self.assertRaises(ProfileError) as caught:
            load_profile(self.dir, "inconnu")
        self.assertIn("introuvable", str(caught.exception))
        self.assertIn("budget, gourmet", str(caught.exception))

    # Teste : si le dossier ne contient aucun profil, le message le dit.
    def test_unknown_profile_in_an_empty_folder_says_so(self):
        with self.assertRaises(ProfileError) as caught:
            load_profile(self.dir, "inconnu")
        self.assertIn("Aucun profil", str(caught.exception))

    # Teste : un profil qui n'est pas du JSON valide donne un message "illisible".
    def test_a_corrupted_profile_is_a_clear_error(self):
        self.profile("{pas du json")
        with self.assertRaises(ProfileError) as caught:
            load_profile(self.dir, "p")
        self.assertIn("illisible", str(caught.exception))

    # Teste : un profil qui n'est pas un objet JSON (une liste) est refuse.
    def test_a_profile_that_is_not_an_object_is_refused(self):
        self.profile([1, 2])
        with self.assertRaises(ProfileError) as caught:
            load_profile(self.dir, "p")
        self.assertIn("objet JSON", str(caught.exception))

    # Teste : un profil vide ({}) est refuse : il n'apporterait rien au prompt.
    def test_an_empty_profile_is_refused(self):
        self.profile({})
        with self.assertRaises(ProfileError) as caught:
            load_profile(self.dir, "p")
        self.assertIn("vide", str(caught.exception))

    # Teste : la liste des profils disponibles est triee, et vide si le dossier n'existe pas.
    def test_available_profiles_are_sorted(self):
        self.profile({"a": 1}, "zebre")
        self.profile({"a": 1}, "alpha")
        self.assertEqual(available_profiles(self.dir), ["alpha", "zebre"])
        self.assertEqual(available_profiles(self.dir / "absent"), [])


class RenderProfileTests(unittest.TestCase):
    # Teste : le profil est mis en forme a la suite du prompt : une ligne "- cle : valeur" par entree, apres un titre
    # qui demande au modele de le respecter dans chaque reponse.
    def test_one_line_per_entry_after_a_heading(self):
        text = render_profile({"rythme": "calme", "budget": 600})
        self.assertTrue(text.startswith("\n\nProfil du voyageur"))
        self.assertIn("- rythme : calme", text)
        self.assertIn("- budget : 600", text)

    # Teste : une liste est affichee avec ses elements separes par des virgules.
    def test_lists_are_joined_with_commas(self):
        self.assertIn("- contraintes : a, b", render_profile({"contraintes": ["a", "b"]}))

    # Teste : un objet imbrique est affiche en JSON, accents conserves.
    def test_nested_objects_are_shown_as_json(self):
        self.assertIn('- horaires : {"matin": "tôt"}', render_profile({"horaires": {"matin": "tôt"}}))

    # Teste : le texte du profil peut etre colle tel quel a la fin du prompt de base, sans le modifier.
    def test_the_text_can_be_appended_to_a_prompt(self):
        self.assertTrue((("PROMPT" + render_profile({"a": 1})).startswith("PROMPT\n\n")))


# 6. Sessions dans la CLI -------------------------------------------------------------------------------------------

class CliSessionTests(CliTestCase):
    MODEL = ["--model", "m"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sessions = Path(tmp.name)

    def run_session(self, inputs, script, argv=(), **kwargs):
        return self.run_cli(inputs, script, [*self.MODEL, *argv], config_overrides={"sessions_dir": self.sessions}, **kwargs)

    # Teste : sans aucune option, une conversation est sauvegardee sous un nom automatique (date et heure), et la CLI
    # annonce ou, au lancement comme a la sortie.
    def test_a_conversation_is_saved_under_an_automatic_name_and_announced(self):
        _, out, _, _ = self.run_session(["bonjour"], [reply("Salut")])
        files = [p.name for p in self.sessions.iterdir()]
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.json$")
        self.assertIn("nouvelle session, sauvegardée dans", out)
        self.assertIn("Conversation sauvegardée :", out)

    # Teste : une conversation sans aucun tour reussi ne cree aucun fichier et n'annonce pas de sauvegarde a la sortie.
    def test_no_file_without_a_successful_turn(self):
        _, out, _, _ = self.run_session([], [])
        self.assertEqual(list(self.sessions.iterdir()), [])
        self.assertNotIn("Conversation sauvegardée", out)

    # Teste : un tour en erreur n'est pas sauvegarde : aucun fichier n'est cree.
    def test_a_failed_turn_is_not_saved(self):
        self.run_session(["bonjour"], [OllamaError("coupure")] * 3)
        self.assertEqual(list(self.sessions.iterdir()), [])

    # Teste : la sauvegarde se fait apres chaque tour, pas seulement a la sortie : si le programme est interrompu au 2e tour,
    # le fichier contient le 1er.
    def test_the_first_turn_is_on_disk_even_if_the_second_is_interrupted(self):
        self.run_session(["un", "deux"], [reply("r1"), KeyboardInterrupt()], ["--session", "s"])
        users = [m["content"] for m in saved(self.sessions / "s.json")["messages"] if m["role"] == "user"]
        self.assertEqual(users, ["un"])

    # Teste : --session NOM cree NOM.json au premier tour, puis une seconde execution avec le meme nom reprend la
    # conversation : le modele recoit le tour precedent, et la CLI annonce la reprise.
    def test_a_named_session_is_resumed_on_the_next_launch(self):
        self.run_session(["bonjour"], [reply("Salut")], ["--session", "lisbonne"])
        self.assertTrue((self.sessions / "lisbonne.json").exists())
        _, out, _, fake = self.run_session(["et ensuite ?"], [reply("Ok")], ["--session", "lisbonne"])
        self.assertIn("[session] 'lisbonne' reprise : 1 tour(s) rechargé(s)", out)
        messages = fake.requests[0]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "bonjour")

    # Teste : deux sessions differentes ne se melangent pas : la seconde ne voit rien de la premiere.
    def test_two_sessions_do_not_mix(self):
        self.run_session(["bonjour A"], [reply("a")], ["--session", "a"])
        _, _, _, fake = self.run_session(["bonjour B"], [reply("b")], ["--session", "b"])
        self.assertEqual([m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user"], ["bonjour B"])

    # Teste : chaque message sauvegarde porte le modele et le profil du lancement qui l'a ecrit : le profil peut changer
    # d'un lancement a l'autre, ou etre absent.
    def test_saved_messages_carry_the_model_and_the_profile_of_each_launch(self):
        self.run_session(["Je pars à Lisbonne"], [reply("Super.")], ["--session", "lisbonne", "--profile", "budget-serre"])
        self.run_session(["Et pour Rome ?"], [reply("Oui.")], ["--session", "lisbonne", "--profile", "gourmet"])
        self.run_session(["Sans profil"], [reply("Ok.")], ["--session", "lisbonne"])
        messages = saved(self.sessions / "lisbonne.json")["messages"]
        self.assertEqual([m.get("profile") for m in messages], ["budget-serre"] * 2 + ["gourmet"] * 2 + [None] * 2)
        self.assertEqual({m["model"] for m in messages}, {"m"})  # --model m

    # Teste : --continue reprend la conversation sauvegardee le plus recemment.
    def test_continue_resumes_the_most_recent_session(self):
        self.run_session(["premiere"], [reply("a")], ["--session", "ancienne"])
        self.run_session(["seconde"], [reply("b")], ["--session", "recente"])
        os.utime(self.sessions / "ancienne.json", (100, 100))
        _, out, _, fake = self.run_session(["suite"], [reply("c")], ["--continue"])
        self.assertIn("'recente' reprise", out)
        self.assertEqual([m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user"], ["seconde", "suite"])

    # Teste : --continue sans aucune conversation sauvegardee le dit et demarre une conversation neuve.
    def test_continue_without_a_saved_conversation_starts_a_new_one(self):
        code, out, _, _ = self.run_session([], [], ["--continue"])
        self.assertEqual(code, 0)
        self.assertIn("aucune conversation sauvegardée à reprendre", out)

    # Teste : un fichier de session abime arrete le programme avec le code 1 et un message clair (jamais de traceback),
    # avant tout appel au modele.
    def test_a_corrupted_session_file_is_a_clear_error(self):
        (self.sessions / "abimee.json").write_text("{pas du json", encoding="utf-8")
        code, _, err, fake = self.run_session([], [], ["--session", "abimee"])
        self.assertEqual(code, 1)
        self.assertIn("Session illisible", err)
        self.assertEqual(fake.requests, [])

    # Teste : un nom de session avec un "/" est refuse avec le code 1 : le fichier doit rester dans le dossier prevu.
    def test_an_invalid_session_name_is_refused(self):
        code, _, err, _ = self.run_session([], [], ["--session", "../evasion"])
        self.assertEqual(code, 1)
        self.assertIn("Nom de session invalide", err)

    # Teste : quand les sessions sont desactivees (sessions_dir a null), --session et --continue sont refuses avec un
    # message clair, et rien n'est ecrit.
    def test_options_are_refused_when_sessions_are_disabled(self):
        for option in (["--session", "x"], ["--continue"]):
            with self.subTest(option=option):
                code, _, err, _ = self.run_cli([], [], [*self.MODEL, *option])
                self.assertEqual(code, 1)
                self.assertIn("Aucun dossier de sessions", err)

    # Fichiers de session, retrouves par leur premiere question (les trier par nom ne donne pas l'ordre de creation : le
    # suffixe "-2" d'une session de la meme seconde se trie avant ".json").
    def files_by_first_question(self):
        return {saved(path)["messages"][0]["content"]: path for path in self.sessions.iterdir()}

    # Teste : /new repart d'une conversation vierge : le modele ne voit rien de l'ancienne, chaque conversation a son
    # fichier (avec son propre tour), et la CLI annonce l'ancien fichier puis la nouvelle session.
    def test_new_starts_a_fresh_conversation_in_its_own_file(self):
        _, out, _, fake = self.run_session(["bonjour", "/new", "salut"], [reply("a"), reply("b")])
        messages = fake.requests[1]["messages"]
        self.assertEqual([(m["role"], m["content"]) for m in messages[1:]], [("user", "salut")])
        files = self.files_by_first_question()
        first, second = files["bonjour"], files["salut"]
        self.assertEqual([m["content"] for m in saved(first)["messages"]], ["bonjour", "a"])
        self.assertEqual([m["content"] for m in saved(second)["messages"]], ["salut", "b"])
        self.assertIn(f"Conversation sauvegardée : {first} (pour la reprendre : --session {first.stem})", out)
        self.assertIn(f"[session] '{second.stem}' : nouvelle session", out)

    # Teste : /nouveau fait la meme chose que /new, quelle que soit la casse.
    def test_nouveau_is_an_alias_of_new(self):
        _, _, _, fake = self.run_session(["bonjour", "/NOUVEAU", "salut"], [reply("a"), reply("b")])
        self.assertEqual(len(fake.requests[1]["messages"]), 2)
        self.assertEqual(len(list(self.sessions.iterdir())), 2)

    # Teste : /new sur une conversation sans aucun tour reussi ne fait rien : pas de nouveau fichier, un message le dit.
    def test_new_on_an_empty_conversation_does_nothing(self):
        _, out, _, _ = self.run_session(["/new", "/new", "bonjour"], [reply("a")])
        self.assertEqual(out.count("la conversation est déjà vierge"), 2)
        self.assertEqual(len(list(self.sessions.iterdir())), 1)

    # Teste : le profil voyageur reste dans le prompt systeme de la conversation neuve.
    def test_new_keeps_the_profile(self):
        _, _, _, fake = self.run_session(["bonjour", "/new", "salut"], [reply("a"), reply("b")], ["--profile", "budget-serre"])
        self.assertIn("- budget_total_eur : 600", fake.requests[1]["messages"][0]["content"])
        second = self.files_by_first_question()["salut"]
        self.assertEqual({m["profile"] for m in saved(second)["messages"]}, {"budget-serre"})

    # Teste : a la sortie, la CLI annonce la derniere conversation (la neuve), pas celle d'avant /new.
    def test_the_exit_message_names_the_latest_conversation(self):
        _, out, _, _ = self.run_session(["bonjour", "/new", "salut"], [reply("a"), reply("b")])
        second = self.files_by_first_question()["salut"]
        self.assertTrue(out.rstrip().endswith(f"Conversation sauvegardée : {second} (pour la reprendre : --continue)"))

    # Teste : le prompt systeme n'est pas dans le fichier, et c'est celui du lancement en cours qui est utilise : en
    # reprenant la conversation avec un profil, le modele recoit le profil.
    def test_a_resumed_conversation_uses_the_current_system_prompt(self):
        self.run_session(["bonjour"], [reply("Salut")], ["--session", "s"])
        self.assertNotIn("system", [m["role"] for m in saved(self.sessions / "s.json")["messages"]])
        _, _, _, fake = self.run_session(["suite"], [reply("Ok")], ["--session", "s", "--profile", "budget-serre"])
        self.assertIn("Profil du voyageur", fake.requests[0]["messages"][0]["content"])

    # Teste : avec max_reloaded_turns, seuls les derniers tours sont renvoyes au modele, la CLI annonce combien sont gardes
    # dans le fichier sans etre recharges, et le fichier conserve tous les tours apres un nouveau tour.
    def test_max_reloaded_turns_limits_what_the_model_sees_but_not_the_file(self):
        self.run_session(["un", "deux", "trois"], [reply("1"), reply("2"), reply("3")], ["--session", "s"])
        _, out, _, fake = self.run_cli(
            ["quatre"], [reply("4")], [*self.MODEL, "--session", "s"],
            config_overrides={"sessions_dir": self.sessions, "max_reloaded_turns": 1},
        )
        self.assertIn("reprise : 1 tour(s) rechargé(s), 2 plus ancien(s) gardé(s)", out)
        self.assertEqual([m["content"] for m in fake.requests[0]["messages"] if m["role"] == "user"], ["trois", "quatre"])
        users = [m["content"] for m in saved(self.sessions / "s.json")["messages"] if m["role"] == "user"]
        self.assertEqual(users, ["un", "deux", "trois", "quatre"])


# 7. Profils dans la CLI --------------------------------------------------------------------------------------------

class CliProfileTests(CliTestCase):
    MODEL = ["--model", "m"]

    # Teste : --profile ajoute le profil au prompt systeme envoye au modele, et la CLI annonce que le profil est applique.
    def test_the_profile_is_injected_into_the_system_prompt(self):
        _, out, _, fake = self.run_cli(["salut"], [reply("ok")], [*self.MODEL, "--profile", "budget-serre"])
        self.assertIn("[profil] 'budget-serre' appliqué", out)
        system = fake.requests[0]["messages"][0]["content"]
        self.assertTrue(system.startswith(load_config().tool_system_prompt))
        self.assertIn("- budget_total_eur : 600", system)

    # Teste : la commande /prompt affiche le prompt envoye au modele, profil compris : c'est la preuve que le profil est injecte.
    def test_the_prompt_command_proves_the_profile_is_injected(self):
        _, out, _, fake = self.run_cli(["/prompt"], [], [*self.MODEL, "--profile", "gourmet"])
        self.assertIn("Profil du voyageur", out)
        self.assertIn("- budget_total_eur : 2500", out)
        self.assertEqual(fake.requests, [])

    # Teste : sans profil, /prompt affiche le prompt de base, sans section de profil.
    def test_the_prompt_command_without_a_profile(self):
        _, out, _, _ = self.run_cli(["/prompt"], [], self.MODEL)
        self.assertIn(load_config().tool_system_prompt[:80], out)
        self.assertNotIn("Profil du voyageur", out)

    # Teste : un profil inconnu arrete le programme avec le code 1 en listant les profils disponibles, avant meme de
    # contacter Ollama.
    def test_an_unknown_profile_stops_before_contacting_ollama(self):
        code, _, err, fake = self.run_cli([], [], [*self.MODEL, "--profile", "inconnu"])
        self.assertEqual(code, 1)
        self.assertIn("introuvable", err)
        self.assertIn("budget-serre", err)
        self.assertEqual(fake.checked_models, [])

    # Teste : un profil corrompu arrete le programme avec le code 1 et un message clair.
    def test_a_corrupted_profile_stops_the_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "casse.json").write_text("{pas du json", encoding="utf-8")
            code, _, err, _ = self.run_cli([], [], [*self.MODEL, "--profile", "casse"], config_overrides={"profiles_dir": Path(tmp)})
        self.assertEqual(code, 1)
        self.assertIn("illisible", err)

    # Teste : quand le modele ne gere pas les outils et que SensAI bascule en chat simple, le profil reste dans le prompt
    # (sinon le changement de prompt l'effacerait).
    def test_the_profile_survives_the_fallback_to_simple_chat(self):
        _, _, _, fake = self.run_cli(
            ["bonjour", "suite"], [reply("a"), reply("b")], [*self.MODEL, "--profile", "budget-serre"], drop_tools_on_first_call=True
        )
        system = fake.requests[1]["messages"][0]["content"]
        self.assertTrue(system.startswith(load_config().system_prompt))
        self.assertIn("- budget_total_eur : 600", system)


if __name__ == "__main__":
    unittest.main()
