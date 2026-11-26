from __future__ import annotations

import logging
import unicodedata
from functools import lru_cache
from pathlib import Path

import matplotlib
from fontTools.ttLib import TTFont
from fpdf import FPDF
from fpdf.fonts import FontFace, TextStyle

from .export import to_html

FONTS_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
FONT_FILES = {
    ("DejaVu", ""): "DejaVuSans.ttf",
    ("DejaVu", "B"): "DejaVuSans-Bold.ttf",
    ("DejaVu", "I"): "DejaVuSans-Oblique.ttf",
    ("DejaVu", "BI"): "DejaVuSans-BoldOblique.ttf",
    ("DejaVuMono", ""): "DejaVuSansMono.ttf",
    ("DejaVuMono", "B"): "DejaVuSansMono-Bold.ttf",
    ("DejaVuMono", "I"): "DejaVuSansMono-Oblique.ttf",
    ("DejaVuMono", "BI"): "DejaVuSansMono-BoldOblique.ttf",
}
MISSING_GLYPH = "□"
INVISIBLE_CATEGORIES = {"Mn", "Me", "Cf"}
TEXT_COLOR = "#1f2328"
TEXT_RGB = (31, 35, 40)
TAG_STYLES = {
    "h1": TextStyle(font_size_pt=20, color=TEXT_COLOR, t_margin=0, b_margin=0.5),
    "h2": TextStyle(font_size_pt=15, color=TEXT_COLOR, t_margin=4, b_margin=0.5),
    "h3": TextStyle(font_size_pt=13, color=TEXT_COLOR, t_margin=3, b_margin=0.5),
    "h4": TextStyle(font_size_pt=12, color=TEXT_COLOR, t_margin=2, b_margin=0.5),
    "blockquote": TextStyle(color="#444444", l_margin=6, t_margin=1, b_margin=1),
    "pre": TextStyle(font_family="DejaVuMono", t_margin=2, b_margin=2),
    "code": FontFace(family="DejaVuMono"),
    "a": FontFace(color="#0b6bcb", emphasis="UNDERLINE"),
}

logging.getLogger("fpdf").setLevel(logging.ERROR)


class PdfError(Exception):
    pass


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("DejaVu", size=8)
        self.set_text_color(110, 118, 129)
        self.cell(0, 6, f"SensAI · page {self.page_no()}/{{nb}}", align="C")


def to_pdf(markdown: str, title: str, breaks: bool = False) -> bytes:
    pdf = _Pdf(format="A4")
    try:
        for (family, style), name in FONT_FILES.items():
            pdf.add_font(family, style, FONTS_DIR / name)
    except OSError as exc:
        raise PdfError(f"police introuvable ({exc})") from None
    pdf.set_title(title)
    pdf.set_creator("SensAI")
    pdf.set_margins(18, 16, 18)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()
    pdf.set_font("DejaVu", size=10.5)
    try:
        pdf.write_html(
            to_html(_printable(markdown), breaks=breaks),
            font_family="DejaVu",
            tag_styles=TAG_STYLES,
            li_prefix_color=TEXT_RGB,
        )
        return bytes(pdf.output())
    except Exception as exc:
        raise PdfError(f"{type(exc).__name__}: {exc}") from None


@lru_cache(maxsize=1)
def _supported_characters() -> frozenset[int]:
    font = TTFont(FONTS_DIR / FONT_FILES[("DejaVu", "")], lazy=True)
    try:
        return frozenset(font.getBestCmap())
    finally:
        font.close()


def _printable(text: str) -> str:
    try:
        supported = _supported_characters()
    except OSError as exc:
        raise PdfError(f"police introuvable ({exc})") from None
    kept = []
    for character in text:
        if ord(character) in supported or character in "\n\r\t":
            kept.append(character)
        elif unicodedata.category(character) not in INVISIBLE_CATEGORIES:
            kept.append(MISSING_GLYPH)
    return "".join(kept)
