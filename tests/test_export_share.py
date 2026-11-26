import contextlib
import http.server
import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from src import cli, pdf_export, share_server
from src.config import ROOT, ConfigError, load_config, set_active_config
from src.export import (
    DOCUMENT_FORMAT,
    EXPORT_FORMAT,
    EXPORT_VERSION,
    MARKDOWN,
    document_to_json,
    to_html,
    to_json,
    to_markdown,
)
from src.pdf_export import MISSING_GLYPH, PdfError, to_pdf
from src.session import Session
from src.share_server import (
    EXTERNAL,
    RUNNING,
    STARTED,
    ShareServerError,
    background_running,
    serve_in_background,
    stop_background,
)
from src.sharing import (
    KIND_CONVERSATION,
    KIND_DOCUMENT,
    TOKEN_PATTERN,
    ShareError,
    create_document_share,
    create_share,
    load_share,
    render_page,
    revoke_conversation,
    revoke_share,
    share_url,
    token_from,
)
from src.tools.trip_document import render_markdown
from tests.harness import CliTestCase
from tests.helpers import reply
from tests.test_config import ConfigTestCase
from tests.test_itinerary_document import DocumentTestCase

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
TOKEN_A = "A" * 43
TOKEN_B = "B" * 43


def turn(question, *replies):
    return [{"role": "user", "content": question}, *replies]


def answer(text, model="qwen3:8b"):
    return {"role": "assistant", "content": text, "model": model}


def tool_call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def tool_result(name, value):
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return {"role": "tool", "tool_name": name, "content": content}


ROME = [
    *turn(
        "Voyage à Rome en mars ?",
        tool_call("weather", {"city": "Rome"}),
        tool_result("weather", {"temp": 15}),
        answer("Il fera **15 °C**."),
    ),
    *turn(
        "Que visiter ?",
        tool_call("web_search", {"query": "Rome visites"}),
        tool_result("web_search", [
            {"title": "Colisée [officiel]", "url": "https://colosseo.it/", "content": "voir https://pub.example/x"},
            {"title": "Vatican", "url": "https://www.museivaticani.va/"},
        ]),
        answer("## Jour 1\n\n- Colisée\n- Forum\n\n```python\nprint('Roma')\n```"),
    ),
]


DOC = {
    "trip": {"destination": "Rome", "start_date": "2027-03-12", "end_date": "2027-03-19", "budget": 1500.0,
             "currency": "EUR", "travelers": 2},
    "sections": [
        {"name": "overview", "title": "Aperçu", "content": "Sept jours à Rome.", "updated_at": "2026-10-30T10:00:00"},
        {"name": "transport", "title": "Transport", "content": "Vol Paris-Rome (réservé)", "updated_at": "2026-10-30T10:05:00"},
        {"name": "lodging", "title": "Hébergement", "content": None, "updated_at": None},
    ],
}

TRICKY = [
    *turn("Emoji 🍕🇮🇹, <script>alert(1)</script> & « € » l’entrée", answer("```python\nprint('<b>')")),
    *turn("Un tableau ?", answer("| A | B |\n|---|---|\n| 1 | 2 |\n\n- a\n  - b\n    - c\n\n" + "très" * 80)),
    *turn("Rien ?", tool_call("weather", {"city": "Rome"})),
]


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)


class MarkdownExportTests(unittest.TestCase):
    def test_each_turn_has_a_heading_the_question_and_the_answer(self):
        markdown = to_markdown(ROME)
        positions = [
            markdown.index(text)
            for text in ("## Échange 1", "> Voyage à Rome en mars ?", "Il fera **15 °C**.", "## Échange 2", "> Que visiter ?")
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(markdown.startswith("# Conversation SensAI\n\n"))

    def test_titles_lists_and_code_blocks_are_kept_as_written(self):
        self.assertIn("## Jour 1\n\n- Colisée\n- Forum\n\n```python\nprint('Roma')\n```", to_markdown(ROME))

    def test_the_summary_counts_the_turns_and_names_the_models(self):
        self.assertEqual(to_markdown(ROME).splitlines()[2], "2 échanges · modèle : qwen3:8b")
        mixed = [*turn("a", answer("b", model="m1")), *turn("c", answer("d", model="m2"))]
        self.assertEqual(to_markdown(mixed).splitlines()[2], "2 échanges · modèles : m1, m2")
        self.assertEqual(to_markdown(turn("a", {"role": "assistant", "content": "b"})).splitlines()[2], "1 échange")

    def test_tool_calls_are_listed_with_their_arguments(self):
        markdown = to_markdown(ROME)
        self.assertIn('Outils utilisés :\n\n- `weather(city="Rome")`', markdown)
        self.assertIn('- `web_search(query="Rome visites")`', markdown)

    def test_tool_arguments_given_as_a_json_string_are_shown_the_same_way(self):
        messages = turn("q", tool_call("weather", '{"city": "Rome"}'), answer("ok"))
        self.assertIn('- `weather(city="Rome")`', to_markdown(messages))

    def test_the_sources_of_the_tool_results_are_kept(self):
        markdown = to_markdown(ROME)
        self.assertIn("Pages consultées par les outils :\n\n- [Colisée \\[officiel\\]](<https://colosseo.it/>)", markdown)
        self.assertIn("- [Vatican](<https://www.museivaticani.va/>)", markdown)
        self.assertNotIn("pub.example", markdown)

    def test_the_sources_of_a_real_web_search_result_are_kept(self):
        pages = [
            {"title": "Horaires du Colisée", "url": "https://www.colosseodiroma.it/fr/horaires-colisee", "content": "Ouvert"},
            {"title": "Billets", "url": "https://coliseerome.it", "content": "Tarifs"},
        ]
        content = json.dumps([json.dumps(page, ensure_ascii=False, indent=2) for page in pages])
        messages = turn("q", tool_call("web_search_basic", {"query": "Colisée"}), tool_result("web_search_basic", content), answer("ok"))
        markdown = to_markdown(messages)
        self.assertIn("- [Horaires du Colisée](<https://www.colosseodiroma.it/fr/horaires-colisee>)", markdown)
        self.assertIn("- [Billets](<https://coliseerome.it>)", markdown)

    def test_the_urls_of_a_plain_text_result_are_sources(self):
        messages = turn("q", tool_call("x", {}), tool_result("x", "Voir https://a.example/page. Merci"), answer("ok"))
        self.assertIn("- <https://a.example/page>", to_markdown(messages))

    def test_a_source_found_twice_is_listed_once(self):
        result = [{"title": "Colisée", "url": "https://colosseo.it/"}]
        messages = turn(
            "q", tool_call("x", {}), tool_result("x", result), tool_call("x", {}), tool_result("x", result), answer("ok")
        )
        self.assertEqual(to_markdown(messages).count("https://colosseo.it/"), 1)

    def test_an_unclosed_code_block_does_not_swallow_the_next_turn(self):
        messages = [*turn("code ?", answer("```python\nprint(1)")), *turn("suite", answer("fin"))]
        html = MARKDOWN.render(to_markdown(messages))
        self.assertIn("<h2>Échange 2</h2>", html)
        self.assertIn("<p>fin</p>", html)

    def test_backticks_in_tool_arguments_stay_inside_the_inline_code(self):
        messages = turn("q", tool_call("web_search", {"query": "a `b` c"}), answer("ok"))
        html = MARKDOWN.render(to_markdown(messages))
        self.assertIn("<code>web_search(query=&quot;a `b` c&quot;)</code>", html)

    def test_special_characters_survive_the_export(self):
        question = "Café & crème <b>gras</b> 🍕 | \\ * « guillemets »"
        reply_text = "Prix : 5 € < 10 € & `code` 🇮🇹"
        markdown = to_markdown(turn(question, answer(reply_text)))
        self.assertIn(f"> {question}", markdown)
        self.assertIn(reply_text, markdown)
        html = MARKDOWN.render(markdown)
        self.assertIn("&lt;b&gt;gras&lt;/b&gt;", html)
        self.assertIn("🍕", html)
        self.assertIn("<code>code</code> 🇮🇹", html)

    def test_markdown_typed_by_the_user_stays_inside_the_quote(self):
        messages = [*turn("# Pas un titre\n```\ncode ouvert", answer("ok")), *turn("suite", answer("fin"))]
        html = MARKDOWN.render(to_markdown(messages))
        self.assertIn("<blockquote>\n<h1>Pas un titre</h1>", html)
        self.assertIn("<h2>Échange 2</h2>", html)

    def test_a_turn_without_a_written_answer_says_so(self):
        messages = turn("q", tool_call("weather", {"city": "Rome"}), tool_result("weather", {"temp": 15}))
        self.assertIn("_Pas de réponse écrite._", to_markdown(messages))

    def test_text_written_between_two_tool_calls_keeps_its_place(self):
        messages = turn(
            "q",
            tool_call("weather", {"city": "Rome"}),
            {**tool_call("weather", {"city": "Milan"}), "content": "Je regarde aussi Milan."},
            answer("Fini."),
        )
        markdown = to_markdown(messages)
        positions = [markdown.index(text) for text in ('city="Rome"', "Je regarde aussi Milan.", 'city="Milan"', "Fini.")]
        self.assertEqual(positions, sorted(positions))


    def test_images_become_plain_links(self):
        html = to_html("Voir ![le plan](https://example.com/plan.png) et ![](https://example.com/x.png).")
        self.assertIn('<a href="https://example.com/plan.png">le plan</a>', html)
        self.assertIn('<a href="https://example.com/x.png">https://example.com/x.png</a>', html)
        self.assertNotIn("<img", html)


class JsonExportTests(unittest.TestCase):
    def test_the_itinerary_document_exports_to_json(self):
        data = json.loads(document_to_json(DOC))
        self.assertEqual(data["format"], DOCUMENT_FORMAT)
        self.assertEqual(data["version"], EXPORT_VERSION)
        self.assertEqual(data["trip"], DOC["trip"])
        self.assertEqual(data["sections"], DOC["sections"])

    def test_the_json_export_keeps_the_messages_as_saved(self):
        data = json.loads(to_json(ROME, "2026-10-30_11-00-22"))
        self.assertEqual(data["format"], EXPORT_FORMAT)
        self.assertEqual(data["version"], EXPORT_VERSION)
        self.assertEqual(data["conversation"], "2026-10-30_11-00-22")
        self.assertEqual(data["turns"], 2)
        self.assertEqual(data["messages"], ROME)

    def test_special_characters_give_valid_json(self):
        messages = turn('Des "guillemets", un \\ et\nune ligne 🍕', answer("```\n}{\n```"))
        self.assertEqual(json.loads(to_json(messages))["messages"], messages)


class PdfExportTests(unittest.TestCase):
    def test_a_conversation_becomes_a_pdf_file(self):
        pdf = to_pdf(to_markdown(ROME), "Conversation SensAI")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))

    def test_the_itinerary_document_becomes_a_pdf_file(self):
        self.assertTrue(to_pdf(render_markdown(DOC), "Document d'itinéraire").startswith(b"%PDF-"))

    def test_the_title_is_in_the_pdf_metadata(self):
        self.assertIn(b"/Title (Conversation SensAI)", to_pdf(to_markdown(ROME), "Conversation SensAI"))

    def test_links_stay_clickable_in_the_pdf(self):
        self.assertIn(b"/URI (https://colosseo.it/)", to_pdf(to_markdown(ROME), "Conversation SensAI"))

    def test_tricky_content_never_breaks_the_pdf(self):
        self.assertTrue(to_pdf(to_markdown(TRICKY), "Conversation SensAI").startswith(b"%PDF-"))

    def test_characters_missing_from_the_font_become_a_visible_square(self):
        self.assertEqual(pdf_export._printable("a🍕b"), f"a{MISSING_GLYPH}b")
        self.assertEqual(pdf_export._printable("a🍕\U000e0067b"), f"a{MISSING_GLYPH}b")
        self.assertEqual(pdf_export._printable("é € « » ’ \n\t"), "é € « » ’ \n\t")

    def test_a_missing_font_is_a_clear_error(self):
        with mock.patch.object(pdf_export, "FONTS_DIR", Path("dossier-absent")):
            with self.assertRaises(PdfError) as caught:
                to_pdf("# Titre\n", "Titre")
        self.assertIn("police introuvable", str(caught.exception))


class SessionConversationTests(TempDirTestCase):
    def test_the_conversation_includes_the_turns_that_were_not_reloaded(self):
        path = self.dir / "voyage.json"
        session = Session("sys", path)
        for n in range(3):
            session.add_turn(f"question {n}", f"réponse {n}")
        reloaded = Session.load(path, "sys", max_turns=1)
        self.assertEqual([m["content"] for m in reloaded.messages if m["role"] == "user"], ["question 2"])
        self.assertEqual(
            [m["content"] for m in reloaded.conversation if m["role"] == "user"], ["question 0", "question 1", "question 2"]
        )

    def test_the_conversation_never_contains_the_system_prompt(self):
        session = Session("prompt secret")
        session.add_turn("salut", "bonjour")
        self.assertEqual([m["role"] for m in session.conversation], ["user", "assistant"])
        self.assertNotIn("prompt secret", to_markdown(session.conversation))


class ShareStoreTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.shares = self.dir / "shares"

    def share(self, conversation="c", messages=ROME, ttl_days=7, now=NOW):
        return create_share(self.shares, conversation, list(messages), ttl_days, now)

    def test_a_link_is_a_snapshot_of_the_conversation(self):
        messages = list(ROME)
        token = create_share(self.shares, "c", messages, 7, NOW)["token"]
        messages.extend(turn("plus tard", answer("après le partage")))
        self.assertEqual(load_share(self.shares, token, NOW)["messages"], ROME)

    def test_tokens_are_long_random_and_unique(self):
        tokens = {self.share()["token"] for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        for token in tokens:
            self.assertRegex(token, TOKEN_PATTERN)
            self.assertEqual(len(token), 43)

    def test_a_link_expires_after_the_configured_number_of_days(self):
        token = self.share(ttl_days=7)["token"]
        self.assertIsNotNone(load_share(self.shares, token, NOW + timedelta(days=7) - timedelta(minutes=1)))
        self.assertIsNone(load_share(self.shares, token, NOW + timedelta(days=7)))
        self.assertFalse((self.shares / f"{token}.json").exists())

    def test_a_revoked_link_no_longer_opens(self):
        token = self.share()["token"]
        self.assertTrue(revoke_share(self.shares, token))
        self.assertIsNone(load_share(self.shares, token, NOW))
        self.assertFalse(revoke_share(self.shares, token))

    def test_revoking_a_conversation_only_removes_its_own_links(self):
        mine = [self.share("mienne")["token"] for _ in range(2)]
        other = self.share("autre")["token"]
        self.assertEqual(revoke_conversation(self.shares, "mienne"), 2)
        for token in mine:
            self.assertIsNone(load_share(self.shares, token, NOW))
        self.assertIsNotNone(load_share(self.shares, other, NOW))

    def test_a_token_that_is_not_a_link_never_reaches_the_file_system(self):
        token = self.share()["token"]
        victim = self.dir / "victim.json"
        victim.write_text((self.shares / f"{token}.json").read_text(encoding="utf-8"), encoding="utf-8")
        for bad in ("../victim", "..\\victim", "", "A" * 42, "A" * 44, "A" * 42 + ".", "A" * 42 + "/"):
            with self.subTest(token=bad):
                self.assertIsNone(load_share(self.shares, bad, NOW))
                self.assertFalse(revoke_share(self.shares, bad))
        self.assertTrue(victim.exists())

    def test_an_unreadable_or_foreign_file_is_refused(self):
        self.shares.mkdir()
        contents = ("pas du json", "[]", json.dumps({"version": 99, "messages": [], "expires_at": "2099-01-01T00:00:00+00:00"}),
                    json.dumps({"version": 1, "messages": "x", "expires_at": "2099-01-01T00:00:00+00:00"}),
                    json.dumps({"version": 1, "messages": [], "expires_at": "bientôt"}))
        for content in contents:
            with self.subTest(content=content):
                (self.shares / f"{TOKEN_A}.json").write_text(content, encoding="utf-8")
                self.assertIsNone(load_share(self.shares, TOKEN_A, NOW))

    def test_expired_links_are_deleted_when_a_new_one_is_created(self):
        old = self.share(ttl_days=1)["token"]
        new = self.share(now=NOW + timedelta(days=2))["token"]
        self.assertEqual(sorted(path.stem for path in self.shares.glob("*.json")), [new])
        self.assertNotEqual(old, new)

    def test_a_link_can_be_given_as_a_full_url_or_as_a_token(self):
        url = share_url("127.0.0.1", 8766, TOKEN_A)
        self.assertEqual(url, f"http://127.0.0.1:8766/share/{TOKEN_A}")
        for link in (url, url + "/", TOKEN_A, f"  {TOKEN_A} "):
            with self.subTest(link=link):
                self.assertEqual(token_from(link), TOKEN_A)

    def test_a_server_open_to_the_network_gives_a_link_with_the_machine_address(self):
        with mock.patch("socket.socket") as fake_socket:
            fake_socket.return_value.__enter__.return_value.getsockname.return_value = ("192.168.1.42", 5000)
            self.assertEqual(share_url("0.0.0.0", 8766, TOKEN_A), f"http://192.168.1.42:8766/share/{TOKEN_A}")

    def test_without_network_the_link_stays_on_this_machine(self):
        with mock.patch("socket.socket") as fake_socket:
            fake_socket.return_value.__enter__.return_value.connect.side_effect = OSError
            self.assertEqual(share_url("0.0.0.0", 8766, TOKEN_A), f"http://127.0.0.1:8766/share/{TOKEN_A}")

    def test_a_document_link_keeps_a_snapshot_of_the_itinerary(self):
        document = json.loads(json.dumps(DOC))
        share = create_document_share(self.shares, "c", document, 7, NOW)
        document["sections"][0]["content"] = "Changé après le partage"
        loaded = load_share(self.shares, share["token"], NOW)
        self.assertEqual(loaded["kind"], KIND_DOCUMENT)
        self.assertEqual(loaded["document"], DOC)

    def test_a_conversation_link_has_the_conversation_kind(self):
        self.assertEqual(load_share(self.shares, self.share()["token"], NOW)["kind"], KIND_CONVERSATION)

    def test_a_malformed_document_link_is_refused(self):
        self.shares.mkdir()
        base = {"version": 1, "kind": "document", "expires_at": "2099-01-01T00:00:00+00:00"}
        for document in (None, "texte", {"sections": []}, {"trip": {}, "sections": "x"}, {"trip": {}, "sections": ["x"]}):
            with self.subTest(document=document):
                (self.shares / f"{TOKEN_A}.json").write_text(json.dumps({**base, "document": document}), encoding="utf-8")
                self.assertIsNone(load_share(self.shares, TOKEN_A, NOW))
        (self.shares / f"{TOKEN_A}.json").write_text(json.dumps({**base, "kind": "autre", "messages": []}), encoding="utf-8")
        self.assertIsNone(load_share(self.shares, TOKEN_A, NOW))

    def test_revoking_a_conversation_also_revokes_its_document_links(self):
        self.share("mienne")
        create_document_share(self.shares, "mienne", DOC, 7, NOW)
        self.assertEqual(revoke_conversation(self.shares, "mienne"), 2)

    def test_a_folder_that_cannot_be_written_is_a_clear_error(self):
        self.shares.write_text("un fichier, pas un dossier", encoding="utf-8")
        with self.assertRaises(ShareError) as caught:
            self.share()
        self.assertIn("impossible", str(caught.exception))


class ShareServerTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        set_active_config(replace(load_config(), shares_dir=self.dir))
        self.addCleanup(set_active_config, None)
        self.http = TestClient(share_server.app)

    def open(self, messages=ROME, **kwargs):
        token = create_share(self.dir, "c", list(messages), 7, **kwargs)["token"]
        return self.http.get(f"/share/{token}")

    def test_a_link_opens_in_a_browser_without_authentication(self):
        response = self.open()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        self.assertIn("<blockquote>\n<p>Voyage à Rome en mars ?</p>", response.text)
        self.assertIn("<h2>Jour 1</h2>", response.text)
        self.assertIn("expire le", response.text)

    def test_the_page_shows_exactly_the_markdown_export(self):
        self.assertIn(MARKDOWN.render(to_markdown(ROME)), self.open().text)

    def test_html_written_in_the_conversation_is_not_executed(self):
        messages = turn(
            "<script>alert('question')</script>",
            answer("<img src=x onerror=alert(1)> [clic](javascript:alert(2))"),
        )
        page = self.open(messages).text
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img", page)
        self.assertNotIn('href="javascript', page)
        self.assertIn("&lt;script&gt;", page)

    def test_the_page_is_protected_by_security_headers(self):
        response = self.open()
        self.assertTrue(response.headers["content-security-policy"].startswith("default-src 'none'"))
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertIn("noindex", response.headers["x-robots-tag"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('<meta name="robots" content="noindex, nofollow">', response.text)

    def test_unknown_expired_and_revoked_links_all_give_the_same_404(self):
        expired = create_share(self.dir, "c", ROME, 7, NOW - timedelta(days=30))["token"]
        revoked = create_share(self.dir, "c", ROME, 7)["token"]
        revoke_share(self.dir, revoked)
        responses = [self.http.get(f"/share/{token}") for token in (TOKEN_B, expired, revoked, "A" * 42)]
        self.assertEqual({response.status_code for response in responses}, {404})
        self.assertEqual(len({response.text for response in responses}), 1)
        self.assertIn("n'existe pas, a expiré ou a été révoqué", responses[0].text)
        for path in ("/share/..%2Fconfig", "/share/../config/config.json", "/share/%2E%2E%2Fsessions"):
            with self.subTest(path=path):
                self.assertEqual(self.http.get(path).status_code, 404)

    def test_the_server_only_serves_shared_links(self):
        for path in ("/", "/docs", "/redoc", "/openapi.json", "/api/conversations", "/api/health", "/share"):
            with self.subTest(path=path):
                self.assertEqual(self.http.get(path).status_code, 404)

    def test_a_document_link_shows_the_itinerary(self):
        token = create_document_share(self.dir, "c", DOC, 7)["token"]
        response = self.http.get(f"/share/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Document d'itinéraire partagé en lecture seule", response.text)
        self.assertIn(to_html(render_markdown(DOC), breaks=True), response.text)

    def test_each_line_of_a_document_section_stays_on_its_own_line(self):
        document = json.loads(json.dumps(DOC))
        document["sections"][0]["content"] = "Colisée (lundi)\nVatican (mardi)"
        token = create_document_share(self.dir, "c", document, 7)["token"]
        self.assertIn("Colisée (lundi)<br />\nVatican (mardi)", self.http.get(f"/share/{token}").text)
        conversation = self.open(turn("q", answer("ligne 1\nligne 2"))).text
        self.assertIn("<p>ligne 1\nligne 2</p>", conversation)

    def test_a_link_that_cannot_be_rendered_gives_the_404_page(self):
        broken = {"version": 1, "kind": "document", "conversation": "c", "expires_at": "2099-01-01T00:00:00+00:00",
                  "document": {"trip": {}, "sections": []}}
        (self.dir / f"{TOKEN_A}.json").write_text(json.dumps(broken), encoding="utf-8")
        response = self.http.get(f"/share/{TOKEN_A}")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Lien indisponible", response.text)

    def test_without_shares_dir_every_link_is_unavailable(self):
        token = create_share(self.dir, "c", ROME, 7)["token"]
        set_active_config(replace(load_config(), shares_dir=None))
        self.assertEqual(self.http.get(f"/share/{token}").status_code, 404)


class ShareConfigTests(ConfigTestCase):
    def test_the_share_settings_come_from_config_json(self):
        config = load_config()
        self.assertEqual(
            (config.shares_dir, config.share_ttl_days, config.share_host, config.share_port),
            (ROOT / "shares", 7, "0.0.0.0", 8766),
        )

    def test_without_shares_dir_sharing_is_disabled(self):
        for value in (None, self.DROP):
            with self.subTest(shares_dir=value):
                config = load_config(self.write_config("{}\n", shares_dir=value))
                self.assertEqual(
                    (config.shares_dir, config.share_ttl_days, config.share_host, config.share_port), (None,) * 4
                )

    def test_invalid_share_settings_are_refused(self):
        invalid = (
            {"share_ttl_days": 0}, {"share_ttl_days": "7"}, {"share_ttl_days": True}, {"share_ttl_days": self.DROP},
            {"share_host": ""}, {"share_host": 3}, {"share_host": self.DROP},
            {"share_port": 0}, {"share_port": 70000}, {"share_port": "8766"}, {"share_port": True},
        )
        for overrides in invalid:
            key = next(iter(overrides))
            with self.subTest(overrides=overrides):
                with self.assertRaises(ConfigError) as caught:
                    load_config(self.write_config("{}\n", **overrides))
                self.assertIn(key, str(caught.exception))


class ExportCommandTests(CliTestCase, TempDirTestCase):
    MODEL = ["--model", "m"]

    def test_export_prints_the_conversation_in_markdown_without_calling_the_model(self):
        _, out, _, fake = self.run_cli(["salut", "/export"], [reply("Bonjour !")], self.MODEL)
        self.assertIn("# Conversation SensAI", out)
        self.assertIn("> salut", out)
        self.assertIn("1 échange · modèle : m", out)
        self.assertEqual(len(fake.requests), 1)

    def test_export_writes_markdown_or_json_according_to_the_extension(self):
        markdown_file, json_file = self.dir / "conv.md", self.dir / "conv.JSON"
        _, out, _, _ = self.run_cli(
            ["salut", f"/export {markdown_file}", f"/EXPORT {json_file}"], [reply("Bonjour !")], self.MODEL
        )
        self.assertTrue(markdown_file.read_text(encoding="utf-8").startswith("# Conversation SensAI"))
        data = json.loads(json_file.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], EXPORT_FORMAT)
        self.assertEqual([m["content"] for m in data["messages"]], ["salut", "Bonjour !"])
        self.assertIn(f"Conversation exportée dans {markdown_file}", out)
        self.assertIn(f"Conversation exportée dans {json_file}", out)

    def test_export_writes_a_pdf_of_the_conversation(self):
        target = self.dir / "conv.pdf"
        _, out, _, _ = self.run_cli(["salut", f"/export {target}"], [reply("Bonjour !")], self.MODEL)
        self.assertTrue(target.read_bytes().startswith(b"%PDF-"))
        self.assertIn(f"Conversation exportée dans {target}", out)

    def test_a_pdf_that_cannot_be_built_is_reported_and_nothing_is_written(self):
        target = self.dir / "conv.pdf"
        with mock.patch.object(pdf_export, "FONTS_DIR", Path("dossier-absent")):
            _, out, _, _ = self.run_cli(["salut", f"/export {target}"], [reply("Bonjour !")], self.MODEL)
        self.assertIn("PDF impossible", out)
        self.assertFalse(target.exists())

    def test_export_never_overwrites_an_existing_file(self):
        target = self.dir / "conv.md"
        target.write_text("mon travail", encoding="utf-8")
        _, out, _, _ = self.run_cli(["salut", f"/export {target}"], [reply("Bonjour !")], self.MODEL)
        self.assertIn("existe déjà", out)
        self.assertEqual(target.read_text(encoding="utf-8"), "mon travail")

    def test_export_of_an_empty_conversation_says_there_is_nothing_to_export(self):
        _, out, _, _ = self.run_cli(["/export"], [], self.MODEL)
        self.assertIn("rien à exporter", out)

    def test_export_of_a_resumed_session_includes_the_turns_that_were_not_reloaded(self):
        session = Session("", self.dir / "voyage.json")
        for n in range(3):
            session.add_turn(f"question {n}", f"réponse {n}")
        _, out, _, _ = self.run_cli(
            ["/export"], [], [*self.MODEL, "--session", "voyage"],
            config_overrides={"sessions_dir": self.dir, "max_reloaded_turns": 1},
        )
        for n in range(3):
            self.assertIn(f"> question {n}", out)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fetch(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


class BackgroundServerTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        set_active_config(replace(load_config(), shares_dir=self.dir))
        self.addCleanup(set_active_config, None)
        self.addCleanup(stop_background)
        self.port = free_port()

    def test_the_server_starts_once_and_serves_the_links(self):
        self.assertEqual(serve_in_background("127.0.0.1", self.port), STARTED)
        token = create_share(self.dir, "c", ROME, 7)["token"]
        status, page = fetch(self.port, f"/share/{token}")
        self.assertEqual(status, 200)
        self.assertIn("Voyage à Rome en mars ?", page)
        self.assertEqual(fetch(self.port, f"/share/{TOKEN_B}")[0], 404)
        self.assertEqual(serve_in_background("127.0.0.1", self.port), RUNNING)
        self.assertTrue(background_running())

    def test_stopping_closes_the_port(self):
        serve_in_background("127.0.0.1", self.port)
        self.assertTrue(stop_background())
        self.assertFalse(background_running())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=2).close()
        self.assertFalse(stop_background())

    def test_a_share_server_started_by_another_program_is_reused(self):
        serve_in_background("127.0.0.1", self.port)
        mine = share_server._background
        share_server._background = None
        try:
            self.assertEqual(serve_in_background("127.0.0.1", self.port), EXTERNAL)
        finally:
            share_server._background = mine

    def test_a_port_taken_by_another_program_is_a_clear_error(self):
        other = http.server.HTTPServer(("127.0.0.1", self.port), http.server.BaseHTTPRequestHandler)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        with self.assertRaises(ShareServerError) as caught:
            serve_in_background("127.0.0.1", self.port)
        self.assertIn(f"le port {self.port} est déjà pris par un autre programme", str(caught.exception))
        self.assertFalse(background_running())


class StubbedLinkServerTestCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("src.cli.serve_in_background", return_value=STARTED)
        self.serve = patcher.start()
        self.addCleanup(patcher.stop)


class ShareCommandTests(CliTestCase, StubbedLinkServerTestCase):
    MODEL = ["--model", "m"]

    def share_cli(self, inputs, script=None, shares_dir="default"):
        shares = self.dir if shares_dir == "default" else shares_dir
        script = [reply("Bonjour !")] if script is None else script
        return self.run_cli(inputs, script, self.MODEL, config_overrides={"shares_dir": shares, "share_host": "127.0.0.1"})

    def links(self):
        return sorted(path.stem for path in self.dir.glob("*.json"))

    def test_share_warns_that_anyone_with_the_link_can_read_it(self):
        session = Session("")
        session.add_turn("salut", "bonjour")
        config = replace(load_config(), shares_dir=self.dir)
        with mock.patch("builtins.input", return_value="n") as asked, contextlib.redirect_stdout(io.StringIO()):
            cli.run_share(config, session, "c", "")
        question = asked.call_args.args[0]
        self.assertIn("Créer un lien public vers cette conversation ?", question)
        self.assertIn("pourra la lire pendant 7 jour(s), sans se connecter", question)
        self.assertEqual(self.links(), [])

    def test_share_prints_a_link_once_confirmed(self):
        _, out, _, _ = self.share_cli(["salut", "/share", "o"])
        [token] = self.links()
        self.assertIn(share_url("127.0.0.1", 8766, token), out)
        self.assertEqual([m["content"] for m in load_share(self.dir, token)["messages"]], ["salut", "Bonjour !"])

    def test_share_starts_the_link_server_itself(self):
        _, out, _, _ = self.share_cli(["salut", "/share", "o"])
        self.serve.assert_called_once_with("127.0.0.1", 8766)
        self.assertIn("Serveur des liens démarré sur http://127.0.0.1:8766", out)
        self.assertIn("tant que SensAI reste ouvert", out)

    def test_a_link_server_already_running_is_not_announced_again(self):
        for state in (RUNNING, EXTERNAL):
            with self.subTest(state=state):
                self.serve.return_value = state
                _, out, _, _ = self.share_cli(["salut", "/share", "o"])
                self.assertNotIn("Serveur des liens démarré", out)
                self.assertIn("Pour le révoquer : /unshare LIEN", out)

    def test_a_link_server_that_cannot_start_is_reported_and_the_link_is_kept(self):
        self.serve.side_effect = ShareServerError("le port 8766 est déjà pris par un autre programme")
        _, out, _, _ = self.share_cli(["salut", "/share", "o"])
        self.assertIn("[avertissement] le port 8766 est déjà pris par un autre programme", out)
        self.assertIn("lance python share.py", out)
        [token] = self.links()
        self.assertIn(share_url("127.0.0.1", 8766, token), out)

    def test_quitting_says_that_the_links_stop_with_sensai_and_stops_the_server(self):
        with mock.patch("src.cli.background_running", return_value=True), \
                mock.patch("src.cli.stop_background") as stop:
            _, out, _, _ = self.share_cli(["salut", "/share", "o"])
        self.assertIn("le serveur des liens s'arrête avec SensAI", out)
        stop.assert_called_once_with()

    def test_only_share_starts_the_link_server(self):
        self.share_cli(["salut", "/export", "/unshare", "/share", "n"])
        self.serve.assert_not_called()

    def test_the_link_printed_by_share_opens_on_the_share_server(self):
        self.share_cli(["salut", "/share", "o"])
        [token] = self.links()
        set_active_config(replace(load_config(), shares_dir=self.dir))
        response = TestClient(share_server.app).get(f"/share/{token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("<p>Bonjour !</p>", response.text)

    def test_share_without_confirmation_creates_nothing(self):
        for answer_text in ("n", "", "plus tard"):
            with self.subTest(answer=answer_text):
                _, out, _, _ = self.share_cli(["salut", "/share", answer_text])
                self.assertIn("aucun lien créé", out)
                self.assertEqual(self.links(), [])

    def test_unshare_revokes_the_links_of_the_conversation(self):
        _, out, _, _ = self.share_cli(["salut", "/share", "o", "/share", "o", "/unshare"])
        self.assertIn("2 lien(s) créé(s) dans cette conversation révoqué(s)", out)
        self.assertEqual(self.links(), [])

    def test_unshare_with_a_link_revokes_only_that_link(self):
        with mock.patch("src.sharing.secrets.token_urlsafe", side_effect=[TOKEN_A, TOKEN_B]):
            _, out, _, _ = self.share_cli(
                ["salut", "/share", "o", "/share", "o", f"/unshare {share_url('127.0.0.1', 8766, TOKEN_A)}"]
            )
        self.assertIn("Lien révoqué", out)
        self.assertEqual(self.links(), [TOKEN_B])

    def test_unshare_of_an_unknown_link_says_so(self):
        _, out, _, _ = self.share_cli([f"/unshare {TOKEN_A}"], script=[])
        self.assertIn("lien inconnu", out)

    def test_unshare_without_any_link_says_so(self):
        _, out, _, _ = self.share_cli(["/unshare"], script=[])
        self.assertIn("aucun lien actif", out)

    def test_share_of_an_empty_conversation_says_there_is_nothing_to_share(self):
        _, out, _, _ = self.share_cli(["/share"], script=[])
        self.assertIn("rien à partager", out)

    def test_share_and_unshare_explain_that_sharing_is_disabled(self):
        _, out, _, _ = self.share_cli(["salut", "/share", "/unshare"], shares_dir=None)
        self.assertEqual(out.count("partage désactivé"), 2)
        self.assertEqual(self.links(), [])

    def test_a_message_that_only_looks_like_a_command_goes_to_the_model(self):
        _, _, _, fake = self.share_cli(["/shared ?", "/exporter ?"], script=[reply("ok"), reply("ok")])
        self.assertEqual(len(fake.requests), 2)

    def test_share_with_an_unknown_argument_explains_the_choices(self):
        _, out, _, fake = self.share_cli(["/share truc"], script=[])
        self.assertIn("/share doc le document d'itinéraire", out)
        self.assertEqual(fake.requests, [])
        self.assertEqual(self.links(), [])


class DocumentCommandTests(CliTestCase, StubbedLinkServerTestCase, DocumentTestCase):
    MODEL = ["--model", "m"]

    def setUp(self):
        super().setUp()
        self.shares = self.dir / "shares"

    def run_doc_cli(self, inputs, script=()):
        return self.run_cli(inputs, list(script), self.MODEL, config_overrides={"shares_dir": self.shares, "share_host": "127.0.0.1"})

    def fill_document(self):
        self.open_trip()
        self.edit("transport", "Vol Paris-Rome.")

    def test_doc_exports_the_document_to_json(self):
        self.fill_document()
        target = self.dir / "doc.json"
        _, out, _, _ = self.run_doc_cli([f"/doc {target}"])
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["format"], DOCUMENT_FORMAT)
        self.assertEqual(data["trip"]["destination"], "Rome")
        self.assertEqual({s["name"]: s["content"] for s in data["sections"]}["transport"], "Vol Paris-Rome.")
        self.assertIn(f"Document écrit dans {target}", out)

    def test_doc_exports_the_document_to_pdf(self):
        self.fill_document()
        target = self.dir / "doc.PDF"
        _, out, _, fake = self.run_doc_cli([f"/doc {target}"])
        self.assertTrue(target.read_bytes().startswith(b"%PDF-"))
        self.assertIn(f"Document écrit dans {target}", out)
        self.assertEqual(fake.requests, [])

    def test_doc_still_exports_markdown_for_any_other_name(self):
        self.fill_document()
        target = self.dir / "itineraire.txt"
        self.run_doc_cli([f"/doc {target}"])
        self.assertEqual(target.read_text(encoding="utf-8"), self.exported())

    def test_share_doc_creates_a_link_to_the_itinerary(self):
        self.fill_document()
        _, out, _, fake = self.run_doc_cli(["/share doc", "o"])
        [path] = self.shares.glob("*.json")
        share = load_share(self.shares, path.stem)
        self.assertEqual(share["kind"], KIND_DOCUMENT)
        self.assertIn(share_url("127.0.0.1", 8766, path.stem), out)
        self.assertIn("Vol Paris-Rome.", render_page(share))
        self.assertEqual(fake.requests, [])

    def test_share_doc_asks_about_the_itinerary(self):
        self.fill_document()
        config = replace(load_config(), shares_dir=self.shares)
        with mock.patch("builtins.input", return_value="n") as asked, contextlib.redirect_stdout(io.StringIO()):
            cli.run_share(config, Session(""), "c", "doc")
        question = asked.call_args.args[0]
        self.assertIn("Créer un lien public vers le document d'itinéraire ?", question)
        self.assertIn("pourra le lire pendant 7 jour(s)", question)
        self.assertEqual(list(self.shares.glob("*.json")), [])

    def test_share_doc_without_a_trip_says_so(self):
        _, out, _, _ = self.run_doc_cli(["/share doc"])
        self.assertIn("aucun document", out)
        self.assertEqual(list(self.shares.glob("*.json")), [])

    def test_unshare_revokes_the_conversation_and_document_links(self):
        self.fill_document()
        _, out, _, _ = self.run_doc_cli(["salut", "/share", "o", "/SHARE DOC", "o", "/unshare"], [reply("Bonjour !")])
        self.assertIn("2 lien(s) créé(s) dans cette conversation révoqué(s)", out)
        self.assertEqual(list(self.shares.glob("*.json")), [])
