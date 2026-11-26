# M4 : Document d'itineraire - src/tools/trip_document.py (les outils et la lecture) et src/tools/trip_db.py (les tables).
#
# Le modele met a jour un document vivant, section par section, en appelant document_edit. Ce fichier appelle les outils
# directement, sur une base temporaire : aucun modele, aucun serveur MCP, et jamais le vrai data/trips.db.
#
#   1. Edition       : creer, remplacer et completer une section, en retirer une ligne.
#   2. Une section   : chaque edition ne touche que sa section, le reste est identique au caractere pres.
#   3. Refus         : une edition invalide est refusee et le document garde son etat d'avant.
#   4. Lecture       : document_get pour le modele, read_document et render_markdown pour l'application.
#   5. Base          : persistance, un document par voyage, historique, contraintes SQL, migration des versions 1 et 2.
#   6. Branchement   : coherence entre config/tools.yaml, le code, le serveur MCP et le prompt.
#   7. Commande /doc : afficher et exporter le document dans la CLI.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import ast
import difflib
import inspect
import re
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from src.config import ROOT, load_config
from src.tools import trip_db
from src.tools import trip_document as document
from src.tools import trip_memory as memory
from src.tools.trip_db import DOCUMENT_MODES, DOCUMENT_SECTIONS, MAX_SECTION_LEN, SCHEMA_VERSION, SECTION_TITLES
from src.utils import get_tool_schema, is_schema_valid
from tests.harness import CliTestCase
from tests.helpers import reply

M4_TOOLS = ("document_edit", "document_get")
PLACEHOLDER = "_À compléter._"


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


class DocumentTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "data" / "trips.db"
        trip_db.set_db_path(self.db)
        self.addCleanup(trip_db.set_db_path, None)

    # Lit la base telle qu'elle est sur le disque, sans passer par les outils.
    def query(self, sql, *params):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    # Ecrit dans la base sans passer par les outils (cles etrangeres activees, comme dans l'application).
    def execute(self, sql, *params):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                conn.execute(sql, params)
        finally:
            conn.close()

    def count(self, table):
        return self.query(f"SELECT COUNT(*) FROM {table}")[0][0] if self.db.exists() else 0

    def open_trip(self, **overrides):
        arguments = {"destination": "Rome", "currency": "EUR", "start_date": in_days(60), "end_date": in_days(67), **overrides}
        result = memory.trip_save(**arguments)
        self.assertTrue(result.get("ok"), result)
        return result

    def edit(self, section, content, **extra):
        result = document.document_edit(section=section, content=content, **extra)
        self.assertTrue(result.get("ok"), result)
        return result

    def exported(self):
        return document.render_markdown(document.read_document())

    # Le document exporte, decoupe : {"": en-tete, "<titre>": texte de la section}. Fiable parce qu'un texte de section ne
    # peut pas contenir de titre.
    def parts(self):
        pieces = re.split(r"^## (.+)$", self.exported(), flags=re.MULTILINE)
        return {"": pieces[0], **dict(zip(pieces[1::2], pieces[2::2]))}

    def assertRefused(self, result, fragment=""):
        self.assertNotIn("ok", result, result)
        self.assertIn(fragment, result["error"])


# 1. Edition --------------------------------------------------------------------------------------------------------

class EditTests(DocumentTestCase):
    # Teste : la premiere edition cree la section ; la reponse dit laquelle a bouge, combien de caracteres elle contient, et
    # quelles sections sont remplies ou vides.
    def test_the_first_edit_creates_the_section(self):
        self.open_trip()
        result = self.edit("transport", "Vol Paris-Rome, 120 EUR.")
        self.assertEqual(result["action"], "created")
        self.assertEqual((result["section"], result["mode"], result["characters"]), ("transport", "replace", 24))
        self.assertEqual(result["filled_sections"], {"transport": 24})
        self.assertEqual(result["empty_sections"], ["overview", "lodging", "activities", "practical"])
        self.assertEqual(document.document_get()["sections"], {"transport": "Vol Paris-Rome, 120 EUR."})

    # Teste : le mode par defaut est "replace" : une deuxieme edition remplace le texte de la section au lieu de s'y ajouter.
    def test_replace_overwrites_the_section(self):
        self.open_trip()
        self.edit("lodging", "Hotel Roma")
        result = self.edit("lodging", "Hotel Colosseo")
        self.assertEqual(result["action"], "updated")
        self.assertEqual(document.document_get("lodging")["sections"], {"lodging": "Hotel Colosseo"})

    # Teste : le mode "append" ajoute a la fin du texte existant, sur une nouvelle ligne, sans reecrire ce qui est deja la.
    def test_append_adds_to_the_end_of_the_section(self):
        self.open_trip()
        self.edit("activities", "Colisée le lundi.")
        result = self.edit("activities", "Vatican le mardi.", mode="append")
        self.assertEqual((result["action"], result["mode"]), ("updated", "append"))
        self.assertEqual(document.document_get("activities")["sections"]["activities"], "Colisée le lundi.\nVatican le mardi.")

    # Teste : "append" sur une section vide la cree, comme "replace".
    def test_append_on_an_empty_section_creates_it(self):
        self.open_trip()
        result = self.edit("practical", "Prévoir un adaptateur.", mode="append")
        self.assertEqual(result["action"], "created")
        self.assertEqual(document.document_get("practical")["sections"]["practical"], "Prévoir un adaptateur.")

    # Teste : le mode "remove" retire la ligne qui contient l'element donne (son nom suffit) ; les autres lignes restent
    # telles quelles, et la reponse dit quelle ligne a ete retiree.
    def test_remove_takes_out_the_line_that_contains_the_item(self):
        self.open_trip()
        self.edit("activities", "Colisée (visite le lundi)\nVatican (visite le mardi)\nTrastevere le soir")
        result = self.edit("activities", "Vatican", mode="remove")
        self.assertEqual((result["action"], result["mode"]), ("updated", "remove"))
        self.assertEqual(result["removed"], ["Vatican (visite le mardi)"])
        self.assertEqual(document.document_get("activities")["sections"]["activities"], "Colisée (visite le lundi)\nTrastevere le soir")

    # Teste : "remove" ignore accents, majuscules et ponctuation (puces, parentheses), mais compare des mots entiers : un
    # bout de mot ne retire rien.
    def test_remove_ignores_accents_and_case_but_compares_whole_words(self):
        self.open_trip()
        self.edit("activities", "- Colisée (lundi)\n- Musées du Vatican (mardi)")
        self.assertEqual(self.edit("activities", "colisee", mode="remove")["removed"], ["- Colisée (lundi)"])
        self.assertRefused(document.document_edit(section="activities", content="Vati", mode="remove"), "ne contient")
        self.assertEqual(self.edit("activities", "MUSEES DU VATICAN", mode="remove")["removed"], ["- Musées du Vatican (mardi)"])

    # Teste : quand une ligne liste plusieurs elements (separes par une virgule, un point-virgule ou "et"), seul l'element
    # demande part ; les autres restent sur la ligne, avec leurs details.
    def test_remove_takes_only_the_item_when_the_line_lists_several(self):
        self.open_trip()
        for line in ("Colisée (lundi), Vatican (mardi)", "Colisée (lundi) et Vatican (mardi)", "Colisée (lundi) ; Vatican (mardi)"):
            with self.subTest(line=line):
                self.edit("activities", f"{line}\nTrastevere")
                self.assertEqual(self.edit("activities", "Vatican", mode="remove")["removed"], ["Vatican (mardi)"])
                self.assertEqual(document.document_get("activities")["sections"]["activities"], "Colisée (lundi)\nTrastevere")

    # Teste : retirer un element d'une liste garde les bons separateurs entre ceux qui restent, qu'il soit au debut, au
    # milieu ou a la fin.
    def test_remove_keeps_the_separators_of_the_remaining_items(self):
        self.open_trip()
        for item, expected in (("Colisée", "Vatican et Panthéon"), ("Vatican", "Colisée et Panthéon"), ("Panthéon", "Colisée, Vatican")):
            with self.subTest(item=item):
                self.edit("activities", "Colisée, Vatican et Panthéon")
                self.edit("activities", item, mode="remove")
                self.assertEqual(document.document_get("activities")["sections"]["activities"], expected)

    # Teste : ce qui suit une virgule sans majuscule est un detail de l'element, pas un autre element ; une virgule entre
    # parentheses, ou un "et" a l'interieur de l'element donne, ne coupent pas non plus : la ligne entiere est retiree.
    def test_remove_takes_the_whole_line_when_it_is_a_single_item(self):
        self.open_trip()
        self.edit("activities", "Hôtel Colosseo, 3 nuits (réservé)\nVatican (mardi, 9h)\nMusée d'art et d'histoire (jeudi)\nColisée")
        self.assertEqual(self.edit("activities", "Hôtel Colosseo", mode="remove")["removed"], ["Hôtel Colosseo, 3 nuits (réservé)"])
        self.assertEqual(self.edit("activities", "Vatican", mode="remove")["removed"], ["Vatican (mardi, 9h)"])
        result = self.edit("activities", "Musée d'art et d'histoire", mode="remove")
        self.assertEqual(result["removed"], ["Musée d'art et d'histoire (jeudi)"])
        self.assertEqual(document.document_get("activities")["sections"]["activities"], "Colisée")

    # Teste : quand plusieurs lignes contiennent l'element mais qu'une seule est exactement lui, c'est celle-la qui part.
    def test_remove_prefers_the_line_that_is_exactly_the_item(self):
        self.open_trip()
        self.edit("activities", "Colisée de nuit (mardi)\nColisée")
        self.assertEqual(self.edit("activities", "Colisée", mode="remove")["removed"], ["Colisée"])
        self.assertEqual(document.document_get("activities")["sections"]["activities"], "Colisée de nuit (mardi)")

    # Teste : plusieurs elements a retirer, un par ligne de content, partent en un seul appel.
    def test_remove_takes_one_item_per_line(self):
        self.open_trip()
        self.edit("activities", "Colisée\nVatican\nPanthéon")
        self.assertEqual(self.edit("activities", "Vatican\nPanthéon", mode="remove")["removed"], ["Vatican", "Panthéon"])
        self.assertEqual(document.document_get("activities")["sections"]["activities"], "Colisée")

    # Teste : retirer la derniere ligne vide la section : elle redevient vide (texte d'attente a l'export), les autres
    # sections ne bougent pas.
    def test_removing_the_last_line_empties_the_section(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        self.edit("activities", "Vatican")
        result = self.edit("activities", "Vatican", mode="remove")
        self.assertEqual(result["characters"], 0)
        self.assertIn("activities", result["empty_sections"])
        self.assertEqual(document.document_get()["sections"], {"transport": "Vol Paris-Rome."})
        self.assertEqual(self.parts()[SECTION_TITLES["activities"]].strip(), PLACEHOLDER)

    # Teste : redire exactement le meme texte ne change rien : action "unchanged", et aucune ligne d'historique en plus.
    def test_repeating_the_same_text_changes_nothing(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        result = self.edit("transport", "Vol Paris-Rome.")
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(self.count("document_edits"), 1)

    # Teste : un petit modele envoie parfois un mode vide, des majuscules ou des espaces : c'est lu comme "replace" ou normalise.
    def test_sloppy_arguments_are_normalised(self):
        self.open_trip()
        self.assertEqual(self.edit("  TRANSPORT ", "Vol.", mode="")["mode"], "replace")
        self.assertEqual(self.edit("transport", "Retour.", mode=" Append ")["mode"], "append")
        self.assertEqual(document.document_get("transport")["sections"]["transport"], "Vol.\nRetour.")

    # Teste : le texte est nettoye avant d'etre enregistre (fins de ligne Windows, espaces de fin de ligne, lignes vides et
    # espaces autour du texte), mais l'indentation des lignes du milieu est conservee.
    def test_the_text_is_tidied_before_saving(self):
        self.open_trip()
        self.edit("overview", "\n\n  Séjour à Rome.   \r\n  Deux jours de visite.  \r\n\n")
        self.assertEqual(document.document_get("overview")["sections"]["overview"], "Séjour à Rome.\n  Deux jours de visite.")

    # Teste : sans voyage enregistre, l'edition est refusee avec un indice qui renvoie a trip_save ; rien n'est ecrit.
    def test_an_edit_needs_a_trip(self):
        result = document.document_edit(section="transport", content="Vol.")
        self.assertRefused(result, "Aucun voyage")
        self.assertIn("trip_save", result["hint"])
        self.assertEqual(result["tool"], "document_edit")
        self.assertEqual(self.count("document_sections"), 0)


# 2. Une section a la fois ------------------------------------------------------------------------------------------

class OneSectionAtATimeTests(DocumentTestCase):
    # Teste : trois editions successives touchent trois sections differentes ; apres chacune, seule la section visee a change
    # dans le document exporte, l'en-tete et les autres sections sont identiques au caractere pres.
    def test_three_edits_change_three_different_sections_and_nothing_else(self):
        self.open_trip()
        previous = self.parts()
        for section, text in (("transport", "Vol Paris-Rome."), ("lodging", "Hôtel Colosseo."), ("activities", "Colisée.")):
            self.edit(section, text)
            current = self.parts()
            changed = {part for part in current if current[part] != previous[part]}
            self.assertEqual(changed, {SECTION_TITLES[section]}, section)
            previous = current

    # Teste : le diff du document exporte avant/apres une edition ne contient que le texte de la section visee (et le texte
    # d'attente qu'il remplace) : aucune autre ligne n'a bouge.
    def test_the_diff_of_an_edit_only_contains_the_targeted_section(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        before = self.exported().splitlines()
        self.edit("lodging", "Hôtel Colosseo.\n3 nuits.")
        after = self.exported().splitlines()
        lines = [line for line in difflib.unified_diff(before, after, lineterm="", n=0) if line[0] in "+-" and line[:3] not in ("+++", "---")]
        self.assertEqual(lines, [f"-{PLACEHOLDER}", "+Hôtel Colosseo.", "+3 nuits."])

    # Teste : le diff du document exporte avant/apres un "remove" ne contient que la ligne retiree.
    def test_the_diff_of_a_remove_only_contains_the_removed_line(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        self.edit("activities", "Colisée (lundi)\nVatican (mardi)\nTrastevere")
        self.edit("practical", "Adaptateur.")
        before = self.exported().splitlines()
        self.edit("activities", "Vatican", mode="remove")
        after = self.exported().splitlines()
        lines = [line for line in difflib.unified_diff(before, after, lineterm="", n=0) if line[0] in "+-" and line[:3] not in ("+++", "---")]
        self.assertEqual(lines, ["-Vatican (mardi)"])

    # Teste : en base aussi, une edition ne touche que la ligne de sa section : les autres gardent texte et date de mise a jour.
    def test_other_sections_are_untouched_in_the_database(self):
        self.open_trip()
        self.edit("transport", "Vol.")
        self.edit("lodging", "Hôtel.")
        rows = "SELECT section, content, updated_at FROM document_sections WHERE section != 'activities' ORDER BY section"
        before = [tuple(row) for row in self.query(rows)]
        self.edit("activities", "Colisée.")
        self.assertEqual([tuple(row) for row in self.query(rows)], before)

    # Teste : un "append" ne reecrit pas le texte existant de sa section : le debut est conserve tel quel, au caractere pres.
    def test_append_keeps_the_existing_text_of_the_section_verbatim(self):
        self.open_trip()
        original = "Jour 1 : Colisée.\nJour 2 : Vatican, entrée 17 €.\n  Détail indenté."
        self.edit("activities", original)
        self.edit("activities", "Jour 3 : Trastevere.", mode="append")
        self.assertTrue(document.document_get("activities")["sections"]["activities"].startswith(original + "\n"))

    # Teste : la structure du document ne change jamais : cinq sections, toujours dans le meme ordre, quelles que soient les editions.
    def test_the_structure_is_fixed(self):
        self.open_trip()
        titles = [SECTION_TITLES[name] for name in DOCUMENT_SECTIONS]
        self.assertEqual([part for part in self.parts() if part], titles)
        self.edit("practical", "Adaptateur.")
        self.edit("overview", "Rome en 7 jours.")
        self.assertEqual([part for part in self.parts() if part], titles)
        self.assertEqual([section["name"] for section in document.read_document()["sections"]], list(DOCUMENT_SECTIONS))


# 3. Refus ----------------------------------------------------------------------------------------------------------

class RefusalTests(DocumentTestCase):
    def filled_document(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        self.edit("lodging", "Hôtel Colosseo.")

    # Verifie qu'une edition est refusee et que le document est exactement dans l'etat d'avant : texte exporte, lignes de la
    # base et historique.
    def assertRejectedAndIntact(self, fragment, **arguments):
        document_before = self.exported()
        rows_before = [tuple(row) for row in self.query("SELECT * FROM document_sections ORDER BY section")]
        edits_before = self.count("document_edits")
        result = document.document_edit(**arguments)
        self.assertRefused(result, fragment)
        self.assertEqual(result["tool"], "document_edit")
        self.assertEqual(self.exported(), document_before)
        self.assertEqual([tuple(row) for row in self.query("SELECT * FROM document_sections ORDER BY section")], rows_before)
        self.assertEqual(self.count("document_edits"), edits_before)
        return result

    # Teste : une section qui n'existe pas est refusee ; la reponse liste les sections valides pour que le modele se corrige.
    def test_an_unknown_section_is_refused_and_the_valid_ones_are_listed(self):
        self.filled_document()
        result = self.assertRejectedAndIntact("'meteo'", section="meteo", content="Soleil.")
        for name in DOCUMENT_SECTIONS:
            self.assertIn(name, result["hint"])

    # Teste : un texte qui contient un titre Markdown est refuse, y compris le document entier colle dans une section : c'est
    # ce qui empeche le modele de reecrire ou de corrompre le document.
    def test_a_markdown_title_in_the_content_is_refused(self):
        self.filled_document()
        whole_document = "# Rome\n\n## Transport\n\nVol.\n\n## Hébergement\n\nHôtel."
        for content in ("## Hébergement\nHôtel.", "# Rome", "   ### Détail", "Vol.\n\n## Activités\nColisée.", whole_document):
            with self.subTest(content=content):
                result = self.assertRejectedAndIntact("titre Markdown", section="transport", content=content)
                self.assertIn("hint", result)

    # Teste : un # au milieu d'une ligne, ou colle a un mot, n'est pas un titre : ces textes sont acceptes.
    def test_a_hash_that_is_not_a_title_is_accepted(self):
        self.open_trip()
        self.edit("activities", "Priorité #1 : le Colisée.\n#astuce réserver en ligne\nTarif C# : 12 €.")
        self.assertIn("#astuce", document.document_get("activities")["sections"]["activities"])

    # Teste : un texte vide ou fait d'espaces est refuse : une section ne se vide pas par erreur.
    def test_an_empty_text_is_refused(self):
        self.filled_document()
        for content in ("", "   ", "\n\n"):
            with self.subTest(content=content):
                self.assertRejectedAndIntact("vide", section="transport", content=content)

    # Teste : un contenu qui n'est pas du texte (nombre, liste, objet, null) est refuse.
    def test_a_content_that_is_not_text_is_refused(self):
        self.filled_document()
        for content in (12, ["Vol."], {"text": "Vol."}, None):
            with self.subTest(content=content):
                self.assertRejectedAndIntact("doit être un texte", section="transport", content=content)

    # Teste : un mode inconnu est refuse, et le message donne les modes acceptes.
    def test_an_unknown_mode_is_refused(self):
        self.filled_document()
        result = self.assertRejectedAndIntact("'delete'", section="transport", content="Vol.", mode="delete")
        for mode in DOCUMENT_MODES:
            self.assertIn(mode, result["hint"])

    # Teste : un "remove" d'un element absent de la section est refuse, et l'indice liste les lignes de la section pour que
    # le modele se corrige.
    def test_removing_an_item_that_is_not_there_is_refused_and_the_lines_are_listed(self):
        self.filled_document()
        result = self.assertRejectedAndIntact("ne contient « Vatican »", section="lodging", content="Vatican", mode="remove")
        self.assertIn("Hôtel Colosseo.", result["hint"])

    # Teste : un "remove" ambigu (plusieurs lignes contiennent l'element, aucune n'est exactement lui) est refuse : le code
    # ne devine pas quelle ligne retirer, l'indice cite les lignes concernees.
    def test_an_ambiguous_remove_is_refused(self):
        self.filled_document()
        self.edit("activities", "Colisée (lundi)\nColisée de nuit (mardi)\nVatican")
        result = self.assertRejectedAndIntact("Plusieurs lignes", section="activities", content="Colisée", mode="remove")
        self.assertIn("Colisée (lundi) | Colisée de nuit (mardi)", result["hint"])

    # Teste : si un des elements a retirer est absent, aucun n'est retire (tout ou rien).
    def test_a_remove_with_one_missing_item_removes_nothing(self):
        self.filled_document()
        self.edit("activities", "Colisée\nVatican")
        self.assertRejectedAndIntact("Panthéon", section="activities", content="Vatican\nPanthéon", mode="remove")

    # Teste : un "remove" sur une section vide est refuse.
    def test_removing_from_an_empty_section_is_refused(self):
        self.filled_document()
        self.assertRejectedAndIntact("vide", section="activities", content="Vatican", mode="remove")

    # Teste : la taille d'une section est limitee : le maximum exact passe, un caractere de plus est refuse.
    def test_a_section_cannot_exceed_the_maximum_size(self):
        self.filled_document()
        self.assertRejectedAndIntact(str(MAX_SECTION_LEN), section="transport", content="x" * (MAX_SECTION_LEN + 1))
        self.edit("transport", "x" * MAX_SECTION_LEN)
        self.assertEqual(len(document.document_get("transport")["sections"]["transport"]), MAX_SECTION_LEN)

    # Teste : un "append" qui ferait depasser la taille maximale est refuse, et la section garde son texte d'avant.
    def test_an_append_that_overflows_is_refused_and_the_section_keeps_its_text(self):
        self.filled_document()
        self.edit("activities", "x" * 3000)
        self.assertRejectedAndIntact("dépasserait", section="activities", content="y" * 1500, mode="append")
        self.assertEqual(len(document.document_get("activities")["sections"]["activities"]), 3000)

    # Teste : meme avec des arguments absurdes, les outils renvoient une erreur au lieu de lever une exception, et rien
    # n'est ecrit.
    def test_the_tools_never_raise_on_garbage_arguments(self):
        self.filled_document()
        rows_before = self.count("document_sections")
        calls = (
            lambda: document.document_edit(["transport"], {"a": 1}, 5),
            lambda: document.document_edit(None, None, None),
            lambda: document.document_edit(12, object()),
            lambda: document.document_get(["transport"]),
            lambda: document.document_get(3),
        )
        for call in calls:
            self.assertIn("error", call())
        self.assertEqual(self.count("document_sections"), rows_before)


# 4. Lecture --------------------------------------------------------------------------------------------------------

class ReadingTests(DocumentTestCase):
    # Teste : document_get renvoie les sections remplies (texte complet) et la liste des sections vides.
    def test_document_get_returns_filled_and_empty_sections(self):
        self.open_trip()
        self.edit("transport", "Vol.")
        self.edit("activities", "Colisée.")
        result = document.document_get()
        self.assertTrue(result["ok"])
        self.assertEqual(result["destination"], "Rome")
        self.assertEqual(result["sections"], {"transport": "Vol.", "activities": "Colisée."})
        self.assertEqual(result["empty_sections"], ["overview", "lodging", "practical"])

    # Teste : document_get avec un nom de section ne renvoie que celle-ci ; une section vide est signalee comme vide.
    def test_document_get_can_read_a_single_section(self):
        self.open_trip()
        self.edit("transport", "Vol.")
        self.edit("lodging", "Hôtel.")
        self.assertEqual(document.document_get("lodging")["sections"], {"lodging": "Hôtel."})
        empty = document.document_get("practical")
        self.assertEqual((empty["sections"], empty["empty_sections"]), ({}, ["practical"]))

    # Teste : document_get refuse une section inconnue et exige un voyage, comme document_edit.
    def test_document_get_refuses_an_unknown_section_and_needs_a_trip(self):
        self.assertRefused(document.document_get(), "Aucun voyage")
        self.open_trip()
        self.assertRefused(document.document_get("meteo"), "'meteo'")

    # Teste : read_document renvoie toujours les cinq sections dans l'ordre, avec content a None pour une section vide.
    def test_read_document_lists_the_five_sections_in_order(self):
        self.open_trip()
        self.edit("lodging", "Hôtel.")
        sections = document.read_document()["sections"]
        self.assertEqual([section["name"] for section in sections], list(DOCUMENT_SECTIONS))
        self.assertEqual([section["title"] for section in sections], list(SECTION_TITLES.values()))
        self.assertEqual({section["name"]: section["content"] for section in sections if section["content"]}, {"lodging": "Hôtel."})
        self.assertIsNone(sections[0]["content"])
        self.assertIsNone(sections[0]["updated_at"])
        self.assertIsNotNone(sections[2]["updated_at"])

    # Teste : sans voyage enregistre, il n'y a pas de document (None) : l'application n'a rien a afficher.
    def test_there_is_no_document_without_a_trip(self):
        self.assertIsNone(document.read_document())

    # Teste : l'export Markdown a un en-tete tire du voyage, les cinq titres dans l'ordre, le texte des sections remplies et
    # un texte d'attente pour les sections vides.
    def test_the_markdown_export(self):
        self.open_trip(travelers=2)
        self.edit("transport", "Vol Paris-Rome.")
        expected = (
            f"# Rome\n\nDu {in_days(60)} au {in_days(67)} · 2 voyageurs\n\n"
            f"## Aperçu\n\n{PLACEHOLDER}\n\n"
            f"## Transport\n\nVol Paris-Rome.\n\n"
            f"## Hébergement\n\n{PLACEHOLDER}\n\n"
            f"## Activités\n\n{PLACEHOLDER}\n\n"
            f"## Conseils pratiques\n\n{PLACEHOLDER}\n"
        )
        self.assertEqual(self.exported(), expected)

    # Teste : l'en-tete de l'export suit le voyage (si une date change), mais les sections ne bougent pas : il est derive du
    # voyage et n'est jamais modifie par une edition.
    def test_the_header_follows_the_trip_and_never_the_edits(self):
        self.open_trip()
        self.edit("transport", "Vol.")
        before = self.parts()
        memory.trip_save(end_date=in_days(70))
        after = self.parts()
        self.assertNotEqual(after[""], before[""])
        self.assertIn(in_days(70), after[""])
        self.assertEqual({k: v for k, v in after.items() if k}, {k: v for k, v in before.items() if k})

    # Teste : l'en-tete de l'export s'adapte quand le voyage n'a ni dates (rien de pose) ni pluriel (un seul voyageur).
    def test_the_header_without_dates_and_with_one_traveler(self):
        memory.trip_save(destination="Lisbonne", currency="EUR")
        self.assertTrue(self.exported().startswith("# Lisbonne\n\n1 voyageur\n\n## Aperçu"))
        memory.trip_save(start_date=in_days(30))
        self.assertIn(f"À partir du {in_days(30)} · 1 voyageur\n", self.exported())



# 5. Base de donnees ------------------------------------------------------------------------------------------------

class DatabaseTests(DocumentTestCase):
    # Teste : le document survit a un redemarrage : apres avoir oublie le chemin de la base et l'avoir redonne, il est la.
    def test_the_document_survives_a_restart(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        trip_db.set_db_path(None)
        trip_db.set_db_path(self.db)
        self.assertEqual(document.document_get()["sections"], {"transport": "Vol Paris-Rome."})

    # Teste : un document appartient a un voyage : en ouvrant un autre voyage (new_trip), le document repart vide ; celui du
    # voyage precedent est conserve dans la base.
    def test_a_new_trip_starts_with_an_empty_document(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        memory.trip_save(destination="Lisbonne", currency="EUR", new_trip=True)
        self.assertEqual(document.document_get()["sections"], {})
        self.assertTrue(all(section["content"] is None for section in document.read_document()["sections"]))
        self.assertEqual(self.count("document_sections"), 1)

    # Teste : chaque edition qui change quelque chose est gardee dans l'historique avec le texte d'avant (NULL pour une
    # creation) et d'apres ; une edition sans effet n'y laisse rien.
    def test_every_effective_edit_is_logged_with_before_and_after(self):
        self.open_trip()
        self.edit("activities", "Colisée.")
        self.edit("activities", "Vatican.", mode="append")
        self.edit("activities", "Vatican.", mode="replace")
        self.edit("activities", "Vatican.", mode="replace")
        rows = [tuple(row) for row in self.query("SELECT section, mode, old_content, new_content FROM document_edits ORDER BY id")]
        self.assertEqual(rows, [
            ("activities", "replace", None, "Colisée."),
            ("activities", "append", "Colisée.", "Colisée.\nVatican."),
            ("activities", "replace", "Colisée.\nVatican.", "Vatican."),
        ])

    # Teste : un "remove" est garde dans l'historique comme les autres editions ; quand il vide la section, le texte d'apres
    # vaut NULL.
    def test_a_remove_is_logged_and_null_marks_an_emptied_section(self):
        self.open_trip()
        self.edit("activities", "Colisée.\nVatican.")
        self.edit("activities", "Vatican", mode="remove")
        self.edit("activities", "Colisée", mode="remove")
        rows = [tuple(row) for row in self.query("SELECT mode, old_content, new_content FROM document_edits ORDER BY id")]
        self.assertEqual(rows[1:], [("remove", "Colisée.\nVatican.", "Colisée."), ("remove", "Colisée.", None)])

    # Teste : la base elle-meme refuse une section inconnue, un texte vide ou trop long, meme si la validation Python avait
    # un trou (dernier filet).
    def test_the_database_refuses_invalid_sections_on_its_own(self):
        self.open_trip()
        trip_id = self.query("SELECT id FROM trips")[0][0]
        insert = "INSERT INTO document_sections (trip_id, section, content, updated_at) VALUES (?, ?, ?, 'now')"
        for section, content in (("meteo", "Soleil."), ("transport", ""), ("transport", "   "), ("transport", "x" * (MAX_SECTION_LEN + 1))):
            with self.subTest(section=section, length=len(content)):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.execute(insert, trip_id, section, content)
        with self.assertRaises(sqlite3.IntegrityError):  # un document sans voyage n'existe pas
            self.execute(insert, 999, "transport", "Vol.")
        self.assertEqual(self.count("document_sections"), 0)

    # Teste : une seule ligne par section et par voyage (cle primaire) : deux ecritures directes sur la meme section sont refusees.
    def test_a_section_exists_once_per_trip(self):
        self.open_trip()
        self.edit("transport", "Vol.")
        trip_id = self.query("SELECT id FROM trips")[0][0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute("INSERT INTO document_sections VALUES (?, 'transport', 'Autre.', 'now')", trip_id)

    # Teste : supprimer un voyage supprime aussi son document et son historique (cles etrangeres en cascade).
    def test_deleting_a_trip_deletes_its_document(self):
        self.open_trip()
        self.edit("transport", "Vol.")
        self.execute("DELETE FROM trips")
        self.assertEqual((self.count("document_sections"), self.count("document_edits")), (0, 0))

    # Cree une base telle que M3 seul la laissait : memes tables, sans celles du document, en version 1.
    def make_version_1_database(self):
        self.open_trip()
        memory.expense_save(label="Vol Paris-Rome", amount=120, category="transport", status="booked")
        memory.place_save(name="Colisée", decision="kept")
        conn = sqlite3.connect(self.db)
        conn.executescript("DROP TABLE document_edits; DROP TABLE document_sections; PRAGMA user_version = 1;")
        conn.close()
        self.assertEqual(self.query("PRAGMA user_version")[0][0], 1)

    # Teste : une base en version 1 (celle d'avant M4) est migree sur place : version 2, les donnees du voyage (voyage,
    # depenses, lieux) sont conservees, et le document est utilisable.
    def test_a_version_1_database_is_migrated_without_losing_data(self):
        self.make_version_1_database()
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Rome")
        self.assertEqual(self.query("PRAGMA user_version")[0][0], SCHEMA_VERSION)
        self.assertEqual(self.edit("transport", "Vol Paris-Rome.")["action"], "created")
        trip = memory.trip_get()
        self.assertEqual([expense["label"] for expense in trip["expenses"]], ["Vol Paris-Rome"])
        self.assertEqual([place["name"] for place in trip["places"]["kept"]], ["Colisée"])
        tables = {row[0] for row in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertLessEqual({"document_sections", "document_edits"}, tables)

    # Teste : le premier outil appele sur une base en version 1 peut etre un outil de M3 ; la migration a lieu quand meme.
    def test_the_migration_happens_whichever_tool_is_called_first(self):
        self.make_version_1_database()
        self.assertTrue(memory.expense_save(label="Hôtel", amount=300, category="lodging")["ok"])
        self.assertEqual(self.query("PRAGMA user_version")[0][0], SCHEMA_VERSION)

    # Teste : un fichier SQLite en version 1 qui n'est pas le notre (pas les tables du voyage) n'est pas migre ni modifie.
    def test_a_foreign_version_1_file_is_not_migrated(self):
        self.db.parent.mkdir(parents=True)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE autre (x)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()
        self.assertRefused(document.document_get(), "version de schéma 1")
        self.assertEqual([row[0] for row in self.query("SELECT name FROM sqlite_master")], ["autre"])

    # Cree une base telle que M4 la laissait avant le mode remove : version 2, historique qui n'accepte que replace et append,
    # et dont le texte d'apres ne peut pas etre NULL.
    def make_version_2_database(self):
        self.open_trip()
        memory.expense_save(label="Vol Paris-Rome", amount=120, category="transport", status="booked")
        self.edit("activities", "Colisée.")
        self.edit("activities", "Vatican.", mode="append")
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            ALTER TABLE document_edits RENAME TO newer;
            CREATE TABLE document_edits (
              id          INTEGER PRIMARY KEY,
              trip_id     INTEGER NOT NULL REFERENCES trips (id) ON DELETE CASCADE,
              section     TEXT    NOT NULL,
              mode        TEXT    NOT NULL CHECK (mode IN ('replace', 'append')),
              old_content TEXT,
              new_content TEXT    NOT NULL CHECK (length(trim(new_content)) > 0),
              created_at  TEXT    NOT NULL
            );
            INSERT INTO document_edits SELECT * FROM newer;
            DROP TABLE newer;
            PRAGMA user_version = 2;
        """)
        conn.close()
        self.assertEqual(self.query("PRAGMA user_version")[0][0], 2)

    # Teste : une base en version 2 (M4 sans le mode remove) est migree sur place : version 3, le document, son historique et
    # les donnees du voyage sont conserves, et un "remove" qui vide une section passe ensuite.
    def test_a_version_2_database_is_migrated_and_accepts_remove(self):
        self.make_version_2_database()
        history = [tuple(row) for row in self.query("SELECT * FROM document_edits ORDER BY id")]
        self.assertEqual(self.edit("activities", "Vatican", mode="remove")["removed"], ["Vatican."])
        self.assertEqual(self.query("PRAGMA user_version")[0][0], SCHEMA_VERSION)
        self.edit("activities", "Colisée", mode="remove")
        rows = [tuple(row) for row in self.query("SELECT * FROM document_edits ORDER BY id")]
        self.assertEqual(rows[:2], history)
        self.assertEqual([(row[3], row[5]) for row in rows[2:]], [("remove", "Colisée."), ("remove", None)])
        self.assertEqual([expense["label"] for expense in memory.trip_get()["expenses"]], ["Vol Paris-Rome"])
        tables = {row[0] for row in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("document_edits_v2", tables)

    # Teste : un fichier en version 2 sans les tables du document n'est pas migre ni modifie.
    def test_a_version_2_file_without_the_document_tables_is_not_migrated(self):
        self.make_version_1_database()
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version = 2")
        conn.close()
        self.assertRefused(document.document_get(), "version de schéma 2")
        tables = {row[0] for row in self.query("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertEqual(tables, {"trips", "expenses", "places"})


# 6. Branchement ----------------------------------------------------------------------------------------------------

class WiringTests(DocumentTestCase):
    def declared(self):
        return {tool["function"]["name"]: tool["function"] for tool in load_config().tools}

    # Teste : les 2 outils du document sont declares dans config/tools.yaml, donc visibles du modele.
    def test_every_m4_tool_is_declared_for_the_model(self):
        self.assertLessEqual(set(M4_TOOLS), set(self.declared()))

    # Teste : pour chaque outil, les parametres declares dans tools.yaml sont ceux de la fonction Python, et les champs
    # obligatoires sont ceux qui n'ont pas de valeur par defaut.
    def test_declared_parameters_are_the_ones_of_the_python_function(self):
        declared = self.declared()
        for name in M4_TOOLS:
            with self.subTest(tool=name):
                parameters = inspect.signature(getattr(document, name)).parameters
                spec = declared[name]["parameters"]
                self.assertEqual(set(spec.get("properties", {})), set(parameters))
                without_default = {n for n, p in parameters.items() if p.default is inspect.Parameter.empty}
                self.assertEqual(set(spec.get("required", [])), without_default)

    # Teste : les valeurs autorisees (enum) de tools.yaml sont exactement celles de la base : sections et modes d'edition.
    def test_enums_in_tools_yaml_are_the_ones_of_the_database(self):
        declared = self.declared()
        expected = {
            ("document_edit", "section"): DOCUMENT_SECTIONS, ("document_edit", "mode"): DOCUMENT_MODES,
            ("document_get", "section"): DOCUMENT_SECTIONS,
        }
        for (tool, parameter), values in expected.items():
            with self.subTest(tool=tool, parameter=parameter):
                self.assertEqual(tuple(declared[tool]["parameters"]["properties"][parameter]["enum"]), values)

    # Teste : la description de la section dans tools.yaml cite chacune des sections, pour que le modele sache laquelle choisir.
    def test_the_section_parameter_describes_every_section(self):
        description = self.declared()["document_edit"]["parameters"]["properties"]["section"]["description"]
        for name in DOCUMENT_SECTIONS:
            with self.subTest(section=name):
                self.assertIn(name, description)

    # Teste : avant d'appeler l'outil, l'agent verifie les arguments contre tools.yaml : un contenu manquant ou qui n'est pas
    # du texte est refuse, un appel valide passe. Les enum, eux, ne sont PAS verifies a ce niveau (is_schema_valid ne lit que
    # "required" et les types) : une section inconnue passe ce controle et c'est l'outil qui la refuse (voir RefusalTests).
    def test_the_agent_checks_required_fields_and_types_before_the_tool_runs(self):
        schema = get_tool_schema(load_config().tools, "document_edit")
        self.assertTrue(is_schema_valid({"section": "activities", "content": "Colisée.", "mode": "append"}, schema)[0])
        for arguments in ({"section": "activities"}, {"content": "x"}, {"section": "activities", "content": 12}):
            with self.subTest(arguments=arguments):
                self.assertFalse(is_schema_valid(arguments, schema)[0])
        self.open_trip()
        unknown = {"section": "meteo", "content": "Soleil."}
        self.assertTrue(is_schema_valid(unknown, schema)[0])
        self.assertRefused(document.document_edit(**unknown), "'meteo'")

    # Teste : les 2 outils sont enregistres dans le serveur MCP (lecture du code source, rien n'est lance).
    def test_every_m4_tool_is_registered_in_the_mcp_server(self):
        source = (ROOT / "src" / "tools" / "mcp_server.py").read_text(encoding="utf-8")
        registered = {
            node.args[0].id for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and node.args and isinstance(node.args[0], ast.Name)
        }
        self.assertLessEqual(set(M4_TOOLS), registered)


# 7. Commande /doc --------------------------------------------------------------------------------------------------

class DocCommandTests(CliTestCase, DocumentTestCase):
    MODEL = ["--model", "m"]

    def export_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    # Teste : /doc sans voyage enregistre ne contacte pas le modele et invite a decrire le voyage d'abord.
    def test_doc_without_a_trip_asks_to_describe_the_trip(self):
        _, out, _, fake = self.run_cli(["/doc"], [], self.MODEL)
        self.assertIn("aucun document", out)
        self.assertEqual(fake.requests, [])

    # Teste : /doc affiche le document en Markdown, tel que l'export, sans contacter le modele ; la commande ne depend pas des
    # majuscules.
    def test_doc_prints_the_document_without_calling_the_model(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")
        for command in ("/doc", "/DOC"):
            with self.subTest(command=command):
                _, out, _, fake = self.run_cli([command], [], self.MODEL)
                self.assertIn(self.exported(), out)
                self.assertEqual(fake.requests, [])

    # Teste : /doc FICHIER ecrit le document dans le fichier (le meme Markdown que l'affichage) et le confirme.
    def test_doc_with_a_file_name_exports_the_document(self):
        self.open_trip()
        self.edit("lodging", "Hôtel Colosseo.")
        target = self.export_dir() / "itineraire.md"
        _, out, _, _ = self.run_cli([f"/doc {target}"], [], self.MODEL)
        self.assertEqual(target.read_text(encoding="utf-8"), self.exported())
        self.assertIn(f"Document écrit dans {target}", out)

    # Teste : /doc FICHIER n'ecrase jamais un fichier qui existe deja : il est signale, et son contenu reste intact.
    def test_doc_never_overwrites_an_existing_file(self):
        self.open_trip()
        target = self.export_dir() / "itineraire.md"
        target.write_text("mon travail", encoding="utf-8")
        _, out, _, _ = self.run_cli([f"/doc {target}"], [], self.MODEL)
        self.assertIn("existe déjà", out)
        self.assertEqual(target.read_text(encoding="utf-8"), "mon travail")

    # Teste : un fichier impossible a ecrire (dossier absent) donne une erreur claire, sans traceback (verifie par le harnais).
    def test_doc_reports_a_file_that_cannot_be_written(self):
        self.open_trip()
        target = self.export_dir() / "absent" / "itineraire.md"
        _, out, _, _ = self.run_cli([f"/doc {target}"], [], self.MODEL)
        self.assertIn("écriture impossible", out)

    # Teste : si la memoire du voyage est desactivee (trip_db a null), /doc l'explique au lieu d'echouer.
    def test_doc_reports_a_disabled_trip_memory(self):
        trip_db.set_db_path(None)
        _, out, _, _ = self.run_cli(["/doc"], [], self.MODEL, config_overrides={"trip_db": None})
        self.assertIn("désactivée", out)

    # Teste : /doc ne remplace pas un message ordinaire : un message qui commence par un autre mot part au modele.
    def test_a_message_that_only_looks_like_the_command_goes_to_the_model(self):
        self.open_trip()
        _, _, _, fake = self.run_cli(["/documents ?"], [reply("ok")], self.MODEL)
        self.assertEqual(len(fake.requests), 1)


class ClearCommandTests(CliTestCase, DocumentTestCase):
    MODEL = ["--model", "m"]
    TABLES = ("trips", "expenses", "places", "document_sections", "document_edits")

    def fill_memory(self):
        self.open_trip()
        memory.expense_save(label="Vol Paris-Rome", amount=120, category="transport")
        memory.place_save(name="Colisée", decision="kept")
        self.edit("transport", "Vol Paris-Rome.")

    def test_clear_with_a_yes_erases_the_whole_trip_memory_without_calling_the_model(self):
        self.fill_memory()
        _, out, _, fake = self.run_cli(["/clear", "o"], [], self.MODEL)
        self.assertIn("Mémoire du voyage effacée (1 voyage(s))", out)
        self.assertEqual(fake.requests, [])
        for table in self.TABLES:
            with self.subTest(table=table):
                self.assertEqual(self.count(table), 0)

    def test_the_command_and_the_answer_ignore_case(self):
        self.fill_memory()
        self.run_cli(["/CLEAR", " OUI "], [], self.MODEL)
        self.assertEqual(self.count("trips"), 0)

    def test_any_answer_other_than_yes_keeps_everything(self):
        self.fill_memory()
        for answer in ("", "n", "non", "peut-être"):
            with self.subTest(answer=answer):
                _, out, _, _ = self.run_cli(["/clear", answer], [], self.MODEL)
                self.assertIn("annulé", out)
                self.assertEqual(self.count("trips"), 1)
                self.assertEqual(self.count("expenses"), 1)
                self.assertEqual(self.count("document_sections"), 1)

    def test_end_of_input_at_the_confirmation_keeps_everything(self):
        self.fill_memory()
        _, out, _, _ = self.run_cli(["/clear", EOFError()], [], self.MODEL)
        self.assertIn("annulé", out)
        self.assertEqual(self.count("trips"), 1)

    def test_clearing_an_empty_memory_says_so_and_creates_no_database(self):
        _, out, _, _ = self.run_cli(["/clear", "o"], [], self.MODEL)
        self.assertIn("déjà vide", out)
        self.assertFalse(self.db.exists())

    def test_a_new_trip_starts_clean_after_clear(self):
        self.fill_memory()
        self.run_cli(["/clear", "o"], [], self.MODEL)
        self.open_trip(destination="Lisbonne")
        self.assertEqual(memory.trip_get()["expenses"], [])
        self.assertEqual(document.read_document()["trip"]["destination"], "Lisbonne")
        self.assertEqual(self.count("document_edits"), 0)

    def test_clear_reports_a_disabled_trip_memory(self):
        trip_db.set_db_path(None)
        _, out, _, _ = self.run_cli(["/clear", "o"], [], self.MODEL, config_overrides={"trip_db": None})
        self.assertIn("désactivée", out)

    def test_clear_leaves_the_conversation_alone_and_points_to_new(self):
        self.fill_memory()
        _, out, _, fake = self.run_cli(["bonjour", "/clear", "o", "salut"], [reply("a"), reply("b")], self.MODEL)
        self.assertIn("/new repart d'une conversation vierge", out)
        self.assertEqual(len(fake.requests), 2)
        self.assertIn("bonjour", str(fake.requests[1]["messages"]))

    def test_clear_does_not_mention_new_when_the_conversation_is_empty(self):
        self.fill_memory()
        _, out, _, _ = self.run_cli(["/clear", "o"], [], self.MODEL)
        self.assertNotIn("repart d'une conversation vierge", out)


if __name__ == "__main__":
    unittest.main()
