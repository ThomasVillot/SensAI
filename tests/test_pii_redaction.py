import unittest

from src import model_funcs
from src.config import load_config, set_active_config
from tests.helpers import FakeClient, reply


class TestPiiRedaction(unittest.TestCase):
    def test_redacts_phone_number_from_user_message(self):
        config = load_config()
        set_active_config(config)

        client = FakeClient([
            {
                "personal_information": [
                    {"type": "PHONE_NUMBER", "value": "06 12 34 56 78"}
                ]
            }
        ])
        model_funcs.set_client(client)

        redacted = model_funcs.sanitize_user_message("Mon numéro est 06 12 34 56 78.", model=config.pii_check_model)

        self.assertEqual(redacted, "Mon numéro est [PHONE_NUMBER].")
        self.assertEqual(client.requests[0]["format"], config.schemas["personal_information"])

    def test_an_answer_that_does_not_conform_is_ignored_and_regexes_still_mask(self):
        config = load_config()
        set_active_config(config)
        model_funcs.set_client(FakeClient([reply('{"personal_information": "pas une liste"}')]))

        redacted = model_funcs.sanitize_user_message("Mon numéro est 06 12 34 56 78.", model=config.pii_check_model)

        self.assertEqual(redacted, "Mon numéro est [PHONE_NUMBER].")


if __name__ == "__main__":
    unittest.main()
