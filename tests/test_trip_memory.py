# M3 : Memoire du voyage - src/tools/trip_memory.py (les 6 outils) et src/tools/trip_db.py (la base SQLite).
#
# Le modele ecrit lui-meme ce qui est DECIDE (destination, dates, budget, depenses, lieux retenus ou refuses) en appelant
# des outils. Ce fichier appelle ces outils directement, sur une base temporaire : aucun modele, aucun serveur MCP, et
# jamais le vrai data/trips.db.
#
#   1. Voyage          : creer, relire, mettre a jour, ouvrir un autre voyage, valider les valeurs.
#   2. Depenses        : ajouter, corriger sans doublon, supprimer, refuser les valeurs invalides.
#   3. Budget          : le reste est calcule par SQL, en centimes entiers ; depassement signale.
#   4. Lieux           : retenir ou refuser un lieu, la derniere decision l'emporte.
#   5. Base de donnees : contraintes SQL (dernier filet), fichier, version du schema.
#   6. Configuration   : chemin de la base, memoire desactivable.
#   7. Branchement     : coherence entre config/tools.yaml, le code, le serveur MCP et le prompt.
#
# Au-dessus de chaque test, la ligne "Teste :" dit ce qui est verifie.

import ast
import inspect
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from src.config import ROOT, load_config, set_active_config
from src.tools import trip_db
from src.tools import trip_document as document
from src.tools import trip_memory as memory
from src.tools.trip_db import CATEGORIES, DECISIONS, EXPENSE_STATUSES, PLACE_KINDS, SCHEMA_VERSION

M3_TOOLS = ("trip_save", "trip_get", "expense_save", "expense_delete", "place_save", "budget_summary")


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


class TripTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "data" / "trips.db"  # le dossier data/ n'existe pas : il est cree au premier appel
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

    def count(self, table):
        return self.query(f"SELECT COUNT(*) FROM {table}")[0][0] if self.db.exists() else 0

    def open_trip(self, **overrides):
        arguments = {"destination": "Rome", "currency": "EUR", "start_date": in_days(60), "end_date": in_days(67), "budget": 1500, **overrides}
        result = memory.trip_save(**arguments)
        self.assertTrue(result.get("ok"), result)
        return result

    def expense(self, label="Pizza", amount=12, category="food", **extra):
        result = memory.expense_save(label=label, amount=amount, category=category, **extra)
        self.assertTrue(result.get("ok"), result)
        return result

    def assertRefused(self, result, fragment=""):
        self.assertNotIn("ok", result, result)
        self.assertIn(fragment, result["error"])


# 1. Voyage ---------------------------------------------------------------------------------------------------------

class TripTests(TripTestCase):
    # Teste : creer un voyage exige une destination et une devise ; le refus nomme les champs manquants, donne un indice et
    # le nom de l'outil.
    def test_creating_a_trip_needs_a_destination_and_a_currency(self):
        result = memory.trip_save(budget=900)
        self.assertRefused(result, "destination, currency")
        self.assertIn("hint", result)
        self.assertEqual(result["tool"], "trip_save")

    # Teste : un voyage cree est relu tel quel par trip_get (budget en unites, pas en centimes).
    def test_a_trip_is_created_then_read_back(self):
        created = self.open_trip(travelers=2)
        self.assertEqual(created["action"], "created")
        self.assertEqual(memory.trip_get()["trip"], {
            "destination": "Rome", "start_date": in_days(60), "end_date": in_days(67),
            "budget": 1500.0, "currency": "EUR", "travelers": 2,
        })

    # Teste : sans voyage enregistre, trip_get repond "aucun voyage" (trip a None) au lieu d'echouer.
    def test_with_no_trip_trip_get_says_so_instead_of_failing(self):
        result = memory.trip_get()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["trip"])
        self.assertIn("Aucun voyage", result["message"])

    # Teste : une date qui change met a jour le voyage (le retour donne l'ancienne et la nouvelle valeur) sans creer de doublon.
    def test_a_date_that_changes_updates_the_trip_and_never_duplicates_it(self):
        self.open_trip()
        result = memory.trip_save(start_date=in_days(63))
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["changes"], {"start_date": {"from": in_days(60), "to": in_days(63)}})
        self.assertEqual(self.count("trips"), 1)

    # Teste : enregistrer deux fois les memes valeurs ne change rien (action "unchanged").
    def test_saving_the_same_values_again_changes_nothing(self):
        self.open_trip()
        self.assertEqual(memory.trip_save(destination="Rome")["action"], "unchanged")

    # Teste : les cles vides ("changes", "warnings") sont absentes du resultat, pour que le modele ne recopie pas
    # "aucun avertissement" dans sa reponse.
    def test_empty_changes_and_warnings_are_left_out_of_results(self):
        self.open_trip()
        result = memory.trip_save(destination="Rome")
        self.assertNotIn("changes", result)
        self.assertNotIn("warnings", result)

    # Teste : une nouvelle destination remplace celle du voyage en cours (elle ne cree pas un second voyage), sauf si
    # new_trip est demande.
    def test_a_new_destination_replaces_the_current_one_unless_new_trip_is_asked(self):
        self.open_trip()
        memory.trip_save(destination="Lisbonne")
        self.assertEqual(self.count("trips"), 1)
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Lisbonne")

    # Teste : new_trip=true ouvre un second voyage ; l'ancien est conserve mais plus actif (un seul voyage en cours).
    def test_new_trip_opens_a_second_trip_and_keeps_only_one_active(self):
        self.open_trip()
        result = memory.trip_save(destination="Paris", currency="EUR", new_trip=True)
        self.assertEqual(result["action"], "created")
        self.assertIn("Rome", result["note"])
        self.assertEqual(self.count("trips"), 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM trips WHERE active = 1")[0][0], 1)
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Paris")

    def test_a_new_destination_is_refused_once_the_trip_has_expenses(self):
        self.open_trip()
        self.expense("Hotel Colosseo", 300, "lodging", status="booked")
        result = memory.trip_save(destination="Paris")
        self.assertRefused(result, "Rome")
        self.assertIn("new_trip=true", result["hint"])
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Rome")
        self.assertEqual(self.count("trips"), 1)

    def test_a_new_destination_is_refused_once_the_trip_has_places(self):
        self.open_trip()
        memory.place_save(name="Colisee", decision="kept")
        self.assertRefused(memory.trip_save(destination="Paris"), "Rome")
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Rome")

    def test_a_new_destination_is_refused_once_the_trip_has_a_document(self):
        self.open_trip()
        self.assertTrue(document.document_edit(section="transport", content="Vol Paris-Rome")["ok"])
        self.assertRefused(memory.trip_save(destination="Paris"), "Rome")
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Rome")

    def test_the_same_destination_written_differently_is_not_refused(self):
        self.open_trip()
        self.expense()
        result = memory.trip_save(destination="ROME")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.count("trips"), 1)

    def test_new_trip_still_works_after_a_refused_destination(self):
        self.open_trip()
        self.expense()
        self.assertRefused(memory.trip_save(destination="Paris"), "Rome")
        result = memory.trip_save(destination="Paris", currency="EUR", new_trip=True)
        self.assertEqual(result["action"], "created")
        self.assertEqual(self.count("trips"), 2)
        self.assertEqual(self.count("expenses"), 1)

    # Teste : une date mal formee ou qui n'existe pas est refusee, et rien n'est ecrit.
    def test_dates_are_checked_before_anything_is_written(self):
        self.assertRefused(memory.trip_save(destination="Rome", currency="EUR", start_date="20271231"), "YYYY-MM-DD")
        self.assertRefused(memory.trip_save(destination="Rome", currency="EUR", start_date="2027-02-30"), "n'est pas une date qui existe")
        self.assertEqual(self.count("trips"), 0)

    # Teste : une date dans le passe est refusee, et l'indice donne la date du jour pour que le modele corrige l'annee.
    def test_a_past_date_error_tells_the_model_todays_date(self):
        result = memory.trip_save(destination="Rome", currency="EUR", start_date="2020-01-01")
        self.assertRefused(result, "dans le passé")
        self.assertIn(date.today().isoformat(), result["hint"])

    # Teste : une date de fin avant la date de debut deja enregistree est refusee, et le voyage reste intact.
    def test_an_end_date_before_the_stored_start_date_is_refused_and_the_trip_is_untouched(self):
        self.open_trip()
        self.assertRefused(memory.trip_save(end_date=in_days(10)), "précède")
        self.assertEqual(memory.trip_get()["trip"]["end_date"], in_days(67))

    # Teste : des valeurs invalides (budget nul, negatif, texte, trois decimales ; voyageurs hors 1-50 ; devise mal formee)
    # sont refusees, et aucun voyage n'est cree.
    def test_invalid_values_are_refused(self):
        invalid = [{"budget": 0}, {"budget": -5}, {"budget": "abc"}, {"budget": 12.345}, {"budget": True},
                   {"travelers": 0}, {"travelers": 51}, {"travelers": True}, {"currency": "euro"}, {"currency": "EU"}]
        for bad in invalid:
            with self.subTest(bad=bad):
                result = memory.trip_save(**{"destination": "Rome", "currency": "EUR", **bad})
                self.assertNotIn("ok", result)
                self.assertIn("error", result)
        self.assertEqual(self.count("trips"), 0)

    # Teste : la devise est mise en majuscules, et un parametre facultatif vide ("", None, espaces), que les petits modeles
    # envoient parfois, est traite comme absent.
    def test_the_currency_is_normalised_and_blank_optionals_are_ignored(self):
        self.open_trip(currency="eur")
        self.assertEqual(memory.trip_get()["trip"]["currency"], "EUR")
        blank = memory.trip_save(destination="Rome", currency="EUR", start_date="", end_date=None, budget="  ", travelers=None)
        self.assertTrue(blank["ok"])

    # Teste : la devise ne peut plus changer des qu'une depense est enregistree (aucune conversion automatique).
    def test_the_currency_cannot_change_once_expenses_exist(self):
        self.open_trip()
        self.expense()
        self.assertRefused(memory.trip_save(currency="USD"), "ne peut plus changer")

    # Teste : tant qu'aucune depense n'est enregistree, la devise peut changer.
    def test_the_currency_can_change_while_there_are_no_expenses(self):
        self.open_trip()
        self.assertEqual(memory.trip_save(currency="USD")["action"], "updated")

    # Teste : abaisser le budget sous ce qui est deja engage est accepte, avec un avertissement de depassement.
    def test_lowering_the_budget_under_what_is_committed_is_accepted_with_a_warning(self):
        self.open_trip(budget=1500)
        self.expense("Vol", 1000, "transport", status="booked")
        result = memory.trip_save(budget=800)
        self.assertEqual(result["action"], "updated")
        self.assertIn("dépassé", result["warnings"][0])


# 2. Depenses -------------------------------------------------------------------------------------------------------

class ExpenseTests(TripTestCase):
    # Teste : enregistrer une depense sans voyage est refuse, et l'indice dit d'appeler trip_save d'abord.
    def test_an_expense_needs_a_trip(self):
        result = memory.expense_save(label="Pizza", amount=12, category="food")
        self.assertRefused(result, "Aucun voyage")
        self.assertIn("trip_save", result["hint"])

    # Teste : la meme depense redite (meme categorie, meme libelle) met a jour son montant sans creer de doublon.
    def test_the_same_expense_said_twice_is_updated_not_duplicated(self):
        self.open_trip()
        self.expense("Vol Paris Rome", 200, "transport")
        result = self.expense("Vol Paris Rome", 220, "transport")
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["changes"], {"amount": {"from": 200.0, "to": 220.0}})
        self.assertEqual(self.count("expenses"), 1)

    # Teste : un libelle reformule qui contient tous les mots d'une depense existante est refuse, et l'indice donne le
    # libelle exact a reprendre ou la facon de confirmer une autre depense (distinct=true).
    def test_a_reworded_label_is_refused_with_the_existing_label_in_the_hint(self):
        self.open_trip()
        self.expense("Vol Paris Rome", 200, "transport")
        result = memory.expense_save(label="Vol aller retour Paris Rome", amount=200, category="transport")
        self.assertRefused(result, "Une dépense proche existe déjà")
        self.assertIn("Vol Paris Rome", result["hint"])
        self.assertIn("distinct=true", result["hint"])
        self.assertEqual(self.count("expenses"), 1)

    # Teste : apres l'alerte, reprendre le libelle exact met a jour la depense existante.
    def test_reusing_the_exact_label_after_the_alert_updates_the_expense(self):
        self.open_trip()
        self.expense("Vol Paris Rome", 200, "transport")
        self.expense("Vol Paris Rome", 250, "transport")
        self.assertEqual(self.query("SELECT amount_cents FROM expenses")[0][0], 25000)

    # Teste : distinct=true confirme qu'il s'agit bien d'une autre depense, qui est alors ajoutee.
    def test_distinct_confirms_that_it_is_really_another_expense(self):
        self.open_trip()
        self.expense("Vol Paris Rome", 200, "transport")
        self.expense("Vol aller retour Paris Rome", 150, "transport", distinct=True)
        self.assertEqual(self.count("expenses"), 2)

    # Teste : les memes mots dans un autre ordre sont signales comme une depense proche.
    def test_the_same_words_in_another_order_are_flagged(self):
        self.open_trip()
        self.expense("Vol Paris Rome", 200, "transport")
        self.assertRefused(memory.expense_save(label="Rome Paris vol", amount=200, category="transport"), "proche")

    # Teste : l'alerte ignore les autres categories et les libelles d'un seul mot : "Hotel" apres "Hotel Roma" est accepte.
    def test_the_guard_ignores_other_categories_and_one_word_labels(self):
        self.open_trip()
        self.expense("Hotel Roma", 300, "lodging")
        self.expense("Hotel", 50, "lodging")
        self.expense("Hotel Roma Centre", 100, "food")
        self.assertEqual(self.count("expenses"), 3)

    # Teste : le meme libelle dans une autre categorie est une autre depense.
    def test_the_same_label_in_another_category_is_another_expense(self):
        self.open_trip()
        self.expense("Pizza", 12, "food")
        self.expense("Pizza", 12, "other")
        self.assertEqual(self.count("expenses"), 2)

    # Teste : omettre le statut lors d'une correction de montant conserve le statut actuel (une depense reservee reste reservee).
    def test_omitting_the_status_keeps_the_current_one(self):
        self.open_trip()
        self.expense("Vol", 200, "transport", status="booked")
        result = self.expense("Vol", 250, "transport")
        self.assertEqual(result["expense"]["status"], "booked")

    # Teste : une nouvelle depense sans statut est "planned" (simple estimation).
    def test_a_new_expense_is_planned_by_default(self):
        self.open_trip()
        self.assertEqual(self.expense()["expense"]["status"], "planned")

    # Teste : des depenses invalides (montant nul, negatif, texte, NaN, enorme, trois decimales ; categorie, statut ou
    # libelle invalides) sont refusees, et rien n'est enregistre.
    def test_invalid_expenses_are_refused(self):
        self.open_trip()
        invalid = [{"amount": 0}, {"amount": -1}, {"amount": "abc"}, {"amount": float("nan")}, {"amount": 1e12},
                   {"amount": 12.345}, {"amount": True}, {"category": "inconnue"}, {"status": "inconnu"},
                   {"label": ""}, {"label": "!!!"}]
        for bad in invalid:
            with self.subTest(bad=bad):
                result = memory.expense_save(**{"label": "Pizza", "amount": 12, "category": "food", **bad})
                self.assertNotIn("ok", result)
                self.assertIn("error", result)
        self.assertEqual(self.count("expenses"), 0)

    # Teste : une depense dans une autre devise que celle du voyage est refusee, sans conversion automatique.
    def test_an_amount_in_another_currency_is_refused_without_conversion(self):
        self.open_trip()
        result = memory.expense_save(label="Taxi", amount=20, category="transport", currency="USD")
        self.assertRefused(result, "aucune conversion")

    # Teste : une depense se supprime par son libelle, sans tenir compte des majuscules ni des accents.
    def test_an_expense_can_be_deleted_by_label(self):
        self.open_trip()
        self.expense("Pizza", 12, "food")
        self.assertEqual(memory.expense_delete(label="PIZZA")["action"], "deleted")
        self.assertEqual(self.count("expenses"), 0)

    # Teste : supprimer une depense inconnue est refuse, et l'indice liste les depenses existantes.
    def test_deleting_an_unknown_expense_lists_the_existing_ones(self):
        self.open_trip()
        self.expense("Pizza", 12, "food")
        self.assertIn("Pizza", memory.expense_delete(label="Sushi")["hint"])

    # Teste : si deux depenses portent le meme libelle dans deux categories, la suppression exige la categorie.
    def test_an_ambiguous_label_needs_a_category_to_be_deleted(self):
        self.open_trip()
        self.expense("Pizza", 12, "food")
        self.expense("Pizza", 12, "other")
        self.assertRefused(memory.expense_delete(label="Pizza"), "Plusieurs")
        self.assertEqual(memory.expense_delete(label="Pizza", category="food")["action"], "deleted")
        self.assertEqual(self.count("expenses"), 1)


# 3. Budget ---------------------------------------------------------------------------------------------------------

class BudgetTests(TripTestCase):
    def test_changing_the_budget_returns_the_up_to_date_summary(self):
        self.open_trip()
        self.expense("Vol Paris-Rome", 135, "transport", status="booked")
        self.expense("Hotel Colosseo", 300, "lodging", status="booked")
        result = memory.trip_save(budget=1200)
        self.assertEqual(result["budget"]["budget"], 1200.0)
        self.assertEqual(result["budget"]["remaining"], 765.0)
        self.assertEqual(result["budget"]["remaining"], memory.budget_summary()["remaining"])

    def test_changing_something_else_than_the_budget_returns_no_summary(self):
        self.open_trip()
        self.expense("Vol Paris-Rome", 135, "transport", status="booked")
        self.assertNotIn("budget", memory.trip_save(start_date=in_days(61)))

    # Teste : "combien il me reste ?" est un calcul SQL : reserve ou paye = engage, "planned" = estimation, et le reste est
    # le budget moins l'engage (puis moins l'estime), avec le detail par categorie.
    def test_how_much_is_left_is_a_sum_done_by_sql(self):
        self.open_trip(budget=1000)
        self.expense("Vol", 300, "transport", status="booked")
        self.expense("Hotel", 200, "lodging", status="paid")
        self.expense("Resto", 100, "food")
        summary = memory.budget_summary()
        self.assertEqual((summary["committed"], summary["planned"]), (500.0, 100.0))
        self.assertEqual((summary["remaining"], summary["remaining_after_planned"]), (500.0, 400.0))
        self.assertEqual(summary["by_category"]["transport"], {"committed": 300.0, "planned": 0.0})
        self.assertEqual(summary["by_category"]["food"], {"committed": 0.0, "planned": 100.0})

    # Teste : les montants sont stockes en centimes entiers, donc les sommes sont exactes (0,10 + 0,20 = 0,30 et non
    # 0,30000000000000004).
    def test_amounts_are_stored_in_cents_so_sums_are_exact(self):
        self.open_trip(budget=10)
        self.expense("Cafe", 0.1, "food", status="paid")
        self.expense("Pain", 0.2, "food", status="paid")
        self.assertEqual([row[0] for row in self.query("SELECT amount_cents FROM expenses ORDER BY id")], [10, 20])
        self.assertEqual(memory.budget_summary()["committed"], 0.3)

    # Teste : un budget depasse par les depenses reservees ou payees est signale par un avertissement chiffre, pas cache.
    def test_going_over_budget_is_reported_not_hidden(self):
        self.open_trip(budget=1500)
        self.expense("Vol", 1600, "transport", status="booked")
        self.assertIn("dépassé de 100.00 EUR", memory.budget_summary()["warnings"][0])

    # Teste : un budget qui ne serait depasse que par les depenses prevues (estimees) est signale comme tel.
    def test_going_over_budget_only_with_planned_expenses_is_reported(self):
        self.open_trip(budget=1500)
        self.expense("Vol", 1000, "transport", status="booked")
        self.expense("Hotel", 600, "lodging")
        self.assertIn("serait dépassé", memory.budget_summary()["warnings"][0])

    # Teste : sans budget defini, le resume le dit (note) et ne calcule pas de reste.
    def test_without_a_budget_the_summary_says_so(self):
        self.open_trip(budget=None)
        summary = memory.budget_summary()
        self.assertIn("Aucun budget", summary["note"])
        self.assertIsNone(summary["remaining"])

    # Teste : le resume du budget exige un voyage enregistre.
    def test_the_summary_needs_a_trip(self):
        self.assertRefused(memory.budget_summary(), "Aucun voyage")


# 4. Lieux ----------------------------------------------------------------------------------------------------------

class PlaceTests(TripTestCase):
    def test_rejected_places_lists_only_the_refused_places_of_the_current_trip(self):
        self.open_trip()
        memory.place_save(name="Colisee", decision="kept")
        memory.place_save(name="Vatican", decision="rejected", reason="trop de monde")
        self.assertEqual(
            memory.rejected_places(),
            [{"name": "Vatican", "kind": "other", "decision": "rejected", "reason": "trop de monde"}],
        )
        memory.place_save(name="Vatican", decision="kept")
        self.assertEqual(memory.rejected_places(), [])
        memory.place_save(name="Vatican", decision="rejected")
        memory.trip_save(destination="Paris", currency="EUR", new_trip=True)
        self.assertEqual(memory.rejected_places(), [])

    def test_rejected_places_is_empty_and_creates_nothing_without_a_database(self):
        self.assertEqual(memory.rejected_places(), [])
        self.assertFalse(self.db.exists())

    def test_rejected_places_is_empty_when_the_memory_is_disabled(self):
        trip_db.set_db_path(None)
        set_active_config(replace(load_config(), trip_db=None))
        self.addCleanup(set_active_config, None)
        self.assertEqual(memory.rejected_places(), [])

    # Teste : noter un lieu exige un voyage enregistre.
    def test_a_place_needs_a_trip(self):
        self.assertRefused(memory.place_save(name="Colisee", decision="kept"), "Aucun voyage")

    # Teste : un lieu retenu puis refuse reste une seule ligne : la derniere decision l'emporte.
    def test_a_kept_place_that_is_then_refused_flips_instead_of_duplicating(self):
        self.open_trip()
        memory.place_save(name="Colisee", decision="kept")
        result = memory.place_save(name="Colisee", decision="rejected")
        self.assertEqual(result["action"], "updated")
        self.assertEqual(result["changes"], {"decision": {"from": "kept", "to": "rejected"}})
        self.assertEqual(self.count("places"), 1)

    # Teste : "Colisée" et "colisee" designent le meme lieu (les accents et les majuscules sont ignores).
    def test_accents_and_case_do_not_create_a_second_place(self):
        self.open_trip()
        memory.place_save(name="Colisée", decision="kept")
        memory.place_save(name="colisee", decision="rejected")
        self.assertEqual(self.count("places"), 1)

    # Teste : le motif donne la premiere fois est conserve quand il n'est pas repete lors d'un changement de decision.
    def test_the_reason_is_kept_when_it_is_not_repeated(self):
        self.open_trip()
        memory.place_save(name="Colisee", decision="kept", reason="vue magnifique")
        result = memory.place_save(name="Colisee", decision="rejected")
        self.assertEqual(result["place"]["reason"], "vue magnifique")

    # Teste : des lieux invalides (decision ou type inconnus, nom vide, motif trop long) sont refuses, rien n'est enregistre.
    def test_invalid_places_are_refused(self):
        self.open_trip()
        invalid = [{"decision": "peut-etre"}, {"kind": "zoo"}, {"name": ""}, {"name": "!!!"}, {"reason": "x" * 301}]
        for bad in invalid:
            with self.subTest(bad=bad):
                result = memory.place_save(**{"name": "Colisee", "decision": "kept", **bad})
                self.assertNotIn("ok", result)
                self.assertIn("error", result)
        self.assertEqual(self.count("places"), 0)

    # Teste : trip_get rassemble tout (voyage, depenses, lieux, budget) et separe les lieux retenus des lieux refuses.
    def test_trip_get_gathers_everything_and_splits_places_by_decision(self):
        self.open_trip()
        self.expense("Pizza", 12, "food")
        memory.place_save(name="Colisee", decision="kept", kind="sight")
        memory.place_save(name="Vatican", decision="rejected", reason="trop de monde")
        everything = memory.trip_get()
        self.assertEqual(everything["trip"]["destination"], "Rome")
        self.assertEqual([e["label"] for e in everything["expenses"]], ["Pizza"])
        self.assertEqual([p["name"] for p in everything["places"]["kept"]], ["Colisee"])
        self.assertEqual(everything["places"]["rejected"][0]["reason"], "trop de monde")
        self.assertEqual(everything["budget"]["committed"], 0.0)


# 5. Base de donnees ------------------------------------------------------------------------------------------------

class DatabaseConstraintTests(TripTestCase):
    # On contourne volontairement la validation Python : c'est la base elle-meme qui doit refuser.
    def execute(self, sql, *params):
        with trip_db.connect() as conn:
            conn.execute(sql, params)

    def insert_trip(self, **overrides):
        values = {"destination": "Rome", "start_date": "2099-05-01", "end_date": "2099-05-08", "budget_cents": 100000,
                  "currency": "EUR", "travelers": 1, "active": 0, **overrides}
        self.execute(
            "INSERT INTO trips (destination, start_date, end_date, budget_cents, currency, travelers, active, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'now')", *values.values())

    def insert_expense(self, **overrides):
        values = {"trip_id": 1, "category": "food", "label": "Pizza", "label_key": "pizza", "amount_cents": 1200,
                  "status": "planned", **overrides}
        self.execute("INSERT INTO expenses (trip_id, category, label, label_key, amount_cents, status) VALUES (?, ?, ?, ?, ?, ?)",
                     *values.values())

    # Teste : la base refuse elle-meme un voyage invalide (budget negatif, devise en minuscules, date mal formee, fin avant
    # debut, voyageurs hors limites, destination vide), meme sans passer par les controles Python.
    def test_the_trip_rules_hold_even_without_the_python_checks(self):
        invalid = [{"budget_cents": -5}, {"currency": "eur"}, {"start_date": "15/05/2099"},
                   {"start_date": "2099-05-10", "end_date": "2099-05-01"}, {"travelers": 0}, {"destination": "  "}]
        for bad in invalid:
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.insert_trip(**bad)

    # Teste : la base n'accepte pas deux voyages actifs en meme temps.
    def test_only_one_trip_can_be_active(self):
        self.insert_trip(active=1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_trip(destination="Paris", active=1)

    # Teste : la base refuse elle-meme une depense invalide (montant nul, categorie ou statut inconnus, doublon de libelle
    # dans la meme categorie).
    def test_the_expense_rules_hold_even_without_the_python_checks(self):
        self.insert_trip(active=1)
        self.insert_expense()
        for bad in ({"amount_cents": 0}, {"category": "inconnue"}, {"status": "inconnu"}, {"label": "PIZZA"}):
            with self.subTest(bad=bad), self.assertRaises(sqlite3.IntegrityError):
                self.insert_expense(**bad)

    # Teste : supprimer un voyage supprime aussi ses depenses et ses lieux (cles etrangeres en cascade).
    def test_deleting_a_trip_deletes_what_belongs_to_it(self):
        self.open_trip()
        self.expense()
        memory.place_save(name="Colisee", decision="kept")
        self.execute("DELETE FROM trips")
        self.assertEqual((self.count("expenses"), self.count("places")), (0, 0))


class DatabaseFileTests(TripTestCase):
    # Teste : la base et son dossier sont crees au premier appel, avec le numero de version du schema.
    def test_the_database_and_its_folder_are_created_on_first_use(self):
        self.assertFalse(self.db.exists())
        self.open_trip()
        self.assertTrue(self.db.exists())
        self.assertEqual(self.query("PRAGMA user_version")[0][0], SCHEMA_VERSION)

    # Teste : la memoire survit a un redemarrage : apres avoir oublie le chemin et l'avoir redonne, le voyage est toujours la.
    def test_the_memory_survives_a_restart(self):
        self.open_trip()
        trip_db.set_db_path(None)
        trip_db.set_db_path(self.db)
        self.assertEqual(memory.trip_get()["trip"]["destination"], "Rome")

    # Teste : une base d'une autre version du schema est laissee intacte et signalee par une erreur claire.
    def test_a_database_of_another_version_is_left_alone_and_reported(self):
        self.db.parent.mkdir(parents=True)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE trips (x)")
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
        conn.close()
        self.assertRefused(memory.trip_get(), "version de schéma 7")
        self.assertEqual([row[0] for row in self.query("SELECT name FROM sqlite_master")], ["trips"])

    # Teste : un fichier SQLite qui n'est pas le notre (des tables, version 0) n'est pas modifie : l'ecriture est refusee.
    def test_a_foreign_sqlite_file_is_not_written_to(self):
        self.db.parent.mkdir(parents=True)
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE autre (x)")
        conn.commit()
        conn.close()
        self.assertIn("error", memory.trip_save(destination="Rome", currency="EUR"))
        self.assertEqual([row[0] for row in self.query("SELECT name FROM sqlite_master")], ["autre"])

    def test_clear_all_erases_every_table_and_keeps_the_schema(self):
        self.open_trip()
        self.expense()
        memory.place_save(name="Colisée", decision="kept")
        self.assertEqual(trip_db.clear_all(), 1)
        for table in ("trips", "expenses", "places", "document_sections", "document_edits"):
            with self.subTest(table=table):
                self.assertEqual(self.count(table), 0)
        self.assertEqual(self.query("PRAGMA user_version")[0][0], SCHEMA_VERSION)
        self.assertIsNone(memory.trip_get()["trip"])

    def test_clear_all_counts_the_old_trips_too(self):
        self.open_trip()
        self.open_trip(destination="Lisbonne", new_trip=True)
        self.assertEqual(self.count("trips"), 2)
        self.assertEqual(trip_db.clear_all(), 2)
        self.assertEqual(self.count("trips"), 0)

    def test_a_new_trip_can_be_saved_after_clear_all(self):
        self.open_trip()
        self.expense()
        trip_db.clear_all()
        self.open_trip(destination="Lisbonne")
        result = memory.trip_get()
        self.assertEqual(result["trip"]["destination"], "Lisbonne")
        self.assertEqual(result["expenses"], [])

    def test_clear_all_does_not_create_a_missing_database(self):
        self.assertEqual(trip_db.clear_all(), 0)
        self.assertFalse(self.db.exists())

    def test_clear_all_is_not_a_tool_the_model_can_call(self):
        self.assertNotIn("clear_all", inspect.getsource(memory))
        tools = (ROOT / "config" / "tools.yaml").read_text(encoding="utf-8")
        self.assertNotIn("clear", tools)

    # Teste : la cle de comparaison des libelles ignore les majuscules, les accents, la ponctuation et les espaces en trop.
    def test_normalize_key_ignores_case_accents_and_punctuation(self):
        self.assertEqual(trip_db.normalize_key("  Hôtel   Roma !"), trip_db.normalize_key("hotel roma"))
        self.assertEqual(trip_db.normalize_key("Hôtel   Roma !"), "hotel roma")


# 6. Configuration --------------------------------------------------------------------------------------------------

class ConfigurationTests(unittest.TestCase):
    def tearDown(self):
        set_active_config(None)
        trip_db.set_db_path(None)

    # Teste : le chemin de la base vient de config/config.json ("trip_db") et est absolu (resolu depuis la racine du depot).
    def test_the_database_path_comes_from_the_config_and_is_absolute(self):
        path = load_config().trip_db
        self.assertTrue(path.is_absolute())
        self.assertEqual(path, ROOT / "data" / "trips.db")

    # Teste : avec trip_db a null, la memoire du voyage est desactivee : les outils repondent par une erreur claire.
    def test_a_null_path_disables_the_memory(self):
        set_active_config(replace(load_config(), trip_db=None))
        result = memory.trip_get()
        self.assertIn("désactivée", result["error"])

    # Teste : sans chemin impose, les outils utilisent le chemin de la configuration active.
    def test_the_tools_use_the_path_of_the_active_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.db"
            set_active_config(replace(load_config(), trip_db=path))
            self.assertTrue(memory.trip_save(destination="Rome", currency="EUR")["ok"])
            self.assertTrue(path.exists())


# 7. Branchement ----------------------------------------------------------------------------------------------------

class WiringTests(TripTestCase):
    def declared(self):
        return {tool["function"]["name"]: tool["function"] for tool in load_config().tools}

    # Teste : les 6 outils de memoire sont declares dans config/tools.yaml, donc visibles du modele.
    def test_every_m3_tool_is_declared_for_the_model(self):
        self.assertLessEqual(set(M3_TOOLS), set(self.declared()))

    # Teste : pour chaque outil, les parametres declares dans tools.yaml sont ceux de la fonction Python, et les champs
    # obligatoires sont ceux qui n'ont pas de valeur par defaut.
    def test_declared_parameters_are_the_ones_of_the_python_function(self):
        declared = self.declared()
        for name in M3_TOOLS:
            with self.subTest(tool=name):
                parameters = inspect.signature(getattr(memory, name)).parameters
                spec = declared[name]["parameters"]
                self.assertEqual(set(spec.get("properties", {})), set(parameters))
                without_default = {n for n, p in parameters.items() if p.default is inspect.Parameter.empty}
                self.assertEqual(set(spec.get("required", [])), without_default)

    # Teste : les valeurs autorisees (enum) de tools.yaml sont exactement celles de la base : categories, statuts, types de
    # lieux, decisions.
    def test_enums_in_tools_yaml_are_the_ones_of_the_database(self):
        declared = self.declared()
        expected = {
            ("expense_save", "category"): CATEGORIES, ("expense_save", "status"): EXPENSE_STATUSES,
            ("expense_delete", "category"): CATEGORIES, ("place_save", "decision"): DECISIONS, ("place_save", "kind"): PLACE_KINDS,
        }
        for (tool, parameter), values in expected.items():
            with self.subTest(tool=tool, parameter=parameter):
                self.assertEqual(tuple(declared[tool]["parameters"]["properties"][parameter]["enum"]), values)

    # Teste : les 6 outils sont enregistres dans le serveur MCP (lecture du code source, rien n'est lance).
    def test_every_m3_tool_is_registered_in_the_mcp_server(self):
        source = (ROOT / "src" / "tools" / "mcp_server.py").read_text(encoding="utf-8")
        registered = {
            node.args[0].id for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and node.args and isinstance(node.args[0], ast.Name)
        }
        self.assertLessEqual(set(M3_TOOLS), registered)

    # Teste : meme avec des arguments absurdes (listes, objets, mauvais types), les outils renvoient une erreur au lieu de
    # lever une exception : le modele peut se corriger.
    def test_the_tools_never_raise_on_garbage_arguments(self):
        calls = (
            lambda: memory.trip_save(destination=[1], start_date={}, budget=object(), currency=3, travelers="x"),
            lambda: memory.expense_save(None, None, None),
            lambda: memory.expense_delete(12),
            lambda: memory.place_save(["a"], 5),
        )
        for call in calls:
            self.assertIn("error", call())


if __name__ == "__main__":
    unittest.main()
