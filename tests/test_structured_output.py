import unittest

from src import model_funcs
from src.utils import extract_json_in_content
from tests.helpers import AgentTestCase, reply


class ConformityTests(AgentTestCase):
    def ask(self, answer):
        client = self.use([reply(answer)])
        schema = self.config.schemas["personal_information"]
        response = model_funcs.request_response_model("m", [{"role": "user", "content": "bonjour"}], format=schema)
        return response, client

    def test_the_schema_is_sent_in_the_format_field_without_tools(self):
        _, client = self.ask('{"personal_information": []}')
        self.assertEqual(client.requests[0]["format"], self.config.schemas["personal_information"])
        self.assertIsNone(client.requests[0]["tools"])

    def test_a_conforming_answer_is_accepted_even_surrounded_by_text(self):
        answer = 'Voici : {"personal_information": []} merci.'
        self.assertEqual(self.ask(answer)[0], answer)

    def test_an_answer_that_does_not_conform_is_rejected(self):
        answers = (
            "{}",
            '{"personal_information": "pas une liste"}',
            '{"personal_information": [{"type": "INCONNU", "value": "x"}]}',
            "Pas de JSON ici.",
        )
        for answer in answers:
            with self.subTest(answer=answer):
                self.assertIsNone(self.ask(answer)[0])


class ExtractorTests(unittest.TestCase):
    # Teste : un texte qui est deja du JSON valide est decode tel quel.
    def test_plain_json(self):
        self.assertEqual(extract_json_in_content('{"a": 1}'), {"a": 1})

    # Teste : un bloc de code ```json ... ``` est retrouve dans le texte.
    def test_json_in_a_labelled_code_block(self):
        self.assertEqual(extract_json_in_content('Voici :\n```json\n{"a": 1}\n```'), {"a": 1})

    # Teste : un bloc de code sans etiquette (``` ... ```) est retrouve aussi.
    def test_json_in_an_unlabelled_code_block(self):
        self.assertEqual(extract_json_in_content('Voici :\n```\n{"a": 1}\n```'), {"a": 1})

    # Teste : un objet JSON entoure de texte, sans bloc de code, est retrouve.
    def test_json_object_surrounded_by_prose(self):
        self.assertEqual(extract_json_in_content('Voici le resultat : {"a": 1} voila.'), {"a": 1})

    # Teste : une liste JSON entouree de texte est retrouvee.
    def test_json_list_surrounded_by_prose(self):
        self.assertEqual(extract_json_in_content("Les voici : [1, 2, 3] fin."), [1, 2, 3])

    # Teste : un objet JSON qui contient une liste, entoure de texte et sans bloc de code, est retrouve en entier.
    def test_a_json_object_with_a_list_inside_is_found_in_prose(self):
        text = 'Voici : {"personal_information": [{"type": "EMAIL", "value": "a@b.fr"}]} merci.'
        self.assertEqual(extract_json_in_content(text), {"personal_information": [{"type": "EMAIL", "value": "a@b.fr"}]})

    # Teste : quand la phrase qui precede contient elle-meme des crochets ("Etape [1] : {...}"), c'est l'objet JSON, la
    # valeur la plus longue, qui est rendu, et non le "[1]".
    def test_the_longest_json_value_wins_over_a_bracket_in_the_sentence(self):
        self.assertEqual(extract_json_in_content('Etape [1] : {"a": 1}'), {"a": 1})

    # Teste : du texte apres le JSON, contenant lui aussi des accolades, ne perturbe pas la lecture de l'objet.
    def test_text_with_braces_after_the_json_is_ignored(self):
        self.assertEqual(extract_json_in_content('Resultat : {"a": [1, 2]} (voir {la note})'), {"a": [1, 2]})

    # Teste : un texte sans aucun JSON donne None.
    def test_text_without_json_gives_none(self):
        self.assertIsNone(extract_json_in_content("Aucun JSON ici."))

    # Teste : des accolades qui ne forment pas un JSON valide donnent None, sans erreur.
    def test_invalid_braces_give_none(self):
        self.assertIsNone(extract_json_in_content("{pas du json}"))

    # Teste : un texte vide donne None.
    def test_empty_text_gives_none(self):
        self.assertIsNone(extract_json_in_content(""))


if __name__ == "__main__":
    unittest.main()
