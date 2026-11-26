import json
import unittest

from src import model_funcs
from src.config import load_config, set_active_config
from src.ollama_client import OllamaError
from tests.harness import CliTestCase
from tests.helpers import FakeClient, reply


class TestScopeCheck(unittest.TestCase):
    def test_out_of_scope_message_is_rejected(self):
        config = load_config()
        set_active_config(config)

        client = FakeClient([
            {"content": json.dumps({"classification": "OUT_OF_SCOPE"})}
        ])
        model_funcs.set_client(client)

        classification = model_funcs.detect_message_scope(config.pii_check_model, "Quel est le plus grand pays du monde ?")

        self.assertEqual(classification, "OUT_OF_SCOPE")
        self.assertEqual(client.requests[0]["format"], config.schemas["message_scope"])

    def test_in_scope_message_is_accepted(self):
        config = load_config()
        set_active_config(config)

        client = FakeClient([
            {"content": json.dumps({"classification": "IN_SCOPE"})}
        ])
        model_funcs.set_client(client)

        classification = model_funcs.detect_message_scope(config.pii_check_model, "Je veux réserver un vol Paris-Rome pour le 12 avril.")

        self.assertEqual(classification, "IN_SCOPE")

    def test_a_response_that_does_not_conform_is_refused(self):
        config = load_config()
        set_active_config(config)
        model_funcs.set_client(FakeClient([reply(json.dumps({"classification": "PEUT_ETRE"}))]))

        result = model_funcs.validate_message_scope("Je veux un vol Paris-Rome", config.pii_check_model)

        self.assertEqual(result, (False, "OUT_OF_SCOPE"))

    def test_an_unavailable_ollama_is_an_error_not_a_refusal(self):
        config = load_config()
        set_active_config(config)
        model_funcs.set_client(FakeClient([OllamaError("Impossible de joindre Ollama.")]))

        with self.assertRaisesRegex(OllamaError, "Impossible de joindre Ollama"):
            model_funcs.validate_message_scope("Je veux un vol Paris-Rome", config.pii_check_model)


class TestScopeCheckCli(CliTestCase):
    scope_check = {"side_effect": [OllamaError("Impossible de joindre Ollama."), (True, "IN_SCOPE")]}

    def test_an_unavailable_ollama_is_reported_and_the_chat_goes_on(self):
        code, out, _, fake = self.run_cli(["vol Paris-Rome", "et ensuite"], [reply("Revenu.")])

        self.assertEqual(code, 0)
        self.assertIn("[erreur] Impossible de joindre Ollama.", out)
        self.assertNotIn("[scope check]", out)
        self.assertIn("Revenu.", out)
        self.assertNotIn("vol Paris-Rome", json.dumps(fake.requests[0]["messages"]))


if __name__ == "__main__":
    unittest.main()
