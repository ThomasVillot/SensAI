import html
import re
import unicodedata

MAX_PROMPT_LENGTH = 3000


class PromptValidationError(ValueError):
    pass


PROMPT_INJECTION_PATTERNS = [
    # Tentative de remplacement des instructions
    r"ignore\s+(all\s+)?(previous|preceding|above|prior)\s+instructions",
    r"disregard\s+(all\s+)?(previous|preceding|above|prior)\s+instructions",
    r"forget\s+(all\s+)?(previous|preceding|above|prior)\s+instructions",
    r"ignore\s+(tes|les|toutes\s+les)\s+(instructions|consignes)",
    r"oublie\s+(tes|les|toutes\s+les)\s+(instructions|consignes)",
    r"remplace\s+(tes|les)\s+instructions",

    # Tentative de changement de rôle
    r"you\s+are\s+now\s+(a|an|the)",
    r"pretend\s+to\s+be",

    # Tentative d'extraction du prompt systeme
    r"(reveal|show|display|print|output|repeat)\s+(your\s+)?(system\s+prompt|hidden\s+instructions)",
    r"(revele|affiche|montre|imprime|repete)\s+(ton\s+)?(prompt|system prompt|instructions)",
    r"what\s+are\s+your\s+(system\s+)?instructions",
    r"repeat\s+everything\s+above",

    # Tentative de contournement
    r"developer\s+mode",
    r"jailbreak",
    r"bypass\s+(your\s+)?(rules|restrictions|safety)",
    r"disable\s+(your\s+)?(safety|filters|restrictions)",
    r"mode\s+sans\s+restriction",
    r"contourne\s+(tes\s+)?(regles|restrictions|instructions)",

    # Tentative de manipulation par faux messages systeme
    r"<\s*/?\s*system\s*>",
    r"<\s*/?\s*developer\s*>",
    r"\[SYSTEM\]",
    r"\[INST\]",
    r"###\s*(system|developer)\s*:",
    
    # Tentative de detournement des outils
    r"call\s+(the\s+)?(function|tool)",
    r"execute\s+(this\s+)?(function|command)",
    r"appelle\s+(la\s+)?fonction",
    r"execute\s+(cet\s+)?outil",

    # Demandes directes
    r"\b(reveal|disclose|expose|leak|show|display|print|output|dump)\b.{0,60}\b(system\s*prompt|system\s*message|hidden\s*prompt|initial\s*prompt)\b",
    r"\b(show|print|repeat|reproduce|quote|copy|return)\b.{0,80}\b(everything|all|the\s+text)\b.{0,40}\b(above|before|preceding)\b",
    r"\bwhat\s+(exactly\s+)?(is|are)\s+(your|the)\s+(system\s+prompt|hidden\s+instructions)\b",
    r"\b(give|send|provide|share|export)\b.{0,50}\b(your|the)\s+(system\s+prompt|internal\s+instructions)\b",

    # Extraction progressive
    r"\b(first|last|next|previous)\s+\d+\s+(words|sentences|lines|paragraphs)\b.{0,50}\b(prompt|instructions)\b",
    r"\b(continue|resume)\b.{0,40}\b(system\s+prompt|hidden\s+instructions)\b",
    r"\bcomplete\s+(the\s+following\s+)?(text|sentence|prompt)\b.{0,50}\b(system|instructions)\b",

    # Traduction et encodage du prompt
    r"\b(translate|encode|decode|base64|hex|reverse)\b.{0,60}\b(system\s+prompt|hidden\s+instructions)\b",
    r"\b(convert|transform)\b.{0,50}\b(your\s+)?(system\s+prompt|instructions)\b",
    r"\bwrite\s+(your\s+)?(system\s+prompt|instructions)\b.{0,50}\b(backwards|in\s+reverse|in\s+base64)\b",

    # Demandes en français
    r"\b(r[ee]v[ee]le|divulgue|expose|affiche|montre|imprime|reproduis|recopie|exporte)\b.{0,80}\b(prompt|instructions|consignes|message\s+syst[ee]me)\b",
    r"\b(donne|envoie|fournis|partage)\b.{0,60}\b(ton|tes|vos|les)\b.{0,40}\b(instructions|consignes|prompt)\b",
    r"\b(que\s+contient|quelles\s+sont)\b.{0,60}\b(ton|tes|vos)\b.{0,40}\b(instructions|prompt)\b",
    r"\b(r[ee]p[ee]te|restitue|reconstruis)\b.{0,60}\b(ton|tes)\b.{0,40}\b(prompt|instructions)\b",

    # Demandes generales
    r"\b(list|show|display|print|dump|export|reveal|describe)\b.{0,60}\b(all\s+)?(available\s+)?(tools|functions|function\s+calls|tool\s+definitions)\b",
    r"\b(give|provide|return|output)\b.{0,60}\b(tool|function)\s+(definitions|schemas|descriptions|specifications)\b",
    r"\b(show|reveal|print|dump)\b.{0,60}\b(tool\s+registry|function\s+registry|tool\s+catalog)\b",

    # Schemas et parametres
    r"\b(show|print|reveal|export|dump)\b.{0,60}\b(json\s+schema|parameter\s+schema|function\s+schema|tool\s+schema)\b",
    r"\b(list|enumerate|explain|describe)\b.{0,60}\b(all\s+)?(tool|function)\s+parameters\b",
    r"\b(what|which)\b.{0,40}\b(parameters|arguments|properties)\b.{0,50}\b(tools|functions)\b",
    r"\b(extract|retrieve|return|display)\b.{0,60}\b(required\s+parameters|function\s+arguments)\b",

    # Appels internes
    r"\b(show|reveal|print|dump)\b.{0,60}\b(tool_calls|function_calls|available_functions)\b",
    r"\b(reveal|display|export)\b.{0,60}\b(internal\s+tool\s+configuration)\b",

    # Français
    r"\b(affiche|montre|liste|exporte|revele|imprime|divulgue)\b.{0,60}\b(tous\s+les\s+)?(outils|fonctions|tools)\b",
    r"\b(schema|structure|definition|description)\b.{0,60}\b(des\s+)?(outils|fonctions)\b",
    r"\b(parametres|arguments)\b.{0,60}\b(des\s+)?(outils|fonctions)\b",
    r"\b(donne|extrais|recupere)\b.{0,60}\b(les\s+)?(definitions|schemas)\b.{0,40}\b(outils|fonctions)\b",

    # Marqueurs de jailbreak
    r"\[\s*START\s+OUTPUT\s*\]",
    r"\[\s*END\s+OUTPUT\s*\]",
    r"\[\s*START\s+SYSTEM\s*\]",
    r"\[\s*END\s+SYSTEM\s*\]",
    r"\[\s*SYSTEM\s*\]",
    r"\[\s*DEVELOPER\s*\]",
    r"\[\s*ADMIN\s*\]",

    # Godmode et jailbreak
    r"\bgod[\W_]*mode\b",
    r"\bjailbreak\b",
    r"\bdeveloper[\W_]*mode\b",
    r"\broot[\W_]*mode\b",
    r"\bunrestricted[\W_]*mode\b",

    # Tentatives de modification des instructions
    r"\bignore[\W_]+(?:all[\W_]+)?previous[\W_]+instructions\b",
    r"\bdisregard[\W_]+(?:all[\W_]+)?instructions\b",
    r"\boverride[\W_]+(?:system[\W_]+)?instructions\b",
    r"\bforget[\W_]+(?:all[\W_]+)?(?:previous[\W_]+)?instructions\b",

    # Tentatives de revelation du prompt
    r"\breveal[\W_]+(?:your[\W_]+)?system[\W_]+prompt\b",
    r"\bshow[\W_]+(?:me[\W_]+)?(?:your[\W_]+)?system[\W_]+prompt\b",
    r"\bprint[\W_]+(?:your[\W_]+)?hidden[\W_]+instructions\b",

    # Tentatives de changement de rôle
    r"\byou[\W_]+are[\W_]+now\b",
    r"\bact[\W_]+as[\W_]+(?:an?[\W_]+)?(?:unrestricted|uncensored)\b",
    r"\bpretend[\W_]+(?:to[\W_]+be|you[\W_]+are)\b",

    # Contournement des securites
    r"\bbypass[\W_]+(?:your[\W_]+)?(?:safety|security|restrictions)\b",
    r"\bdisable[\W_]+(?:your[\W_]+)?(?:safety|security|filters)\b",
    r"\bno[\W_]+restrictions\b",
    r"\bwithout[\W_]+(?:any[\W_]+)?restrictions\b",

    # Extraction des donnees internes
    r"\bsystem[\W_]+prompt\b",
    r"\bhidden[\W_]+instructions\b",
    r"\bdeveloper[\W_]+instructions\b"
]

COMPILED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in PROMPT_INJECTION_PATTERNS
]

def detect_prompt_injection(text: str) -> list:
    if not isinstance(text, str):
        return []

    return [
        pattern.pattern
        for pattern in COMPILED_PATTERNS
        if pattern.search(text)
    ]


def neutralize_prompt_injection(text: str) -> str:
    if not isinstance(text, str):
        return text

    matches: list[tuple[int, int]] = []
    for pattern in COMPILED_PATTERNS:
        matches.extend(match.span() for match in pattern.finditer(text))

    if not matches:
        return text

    merged: list[list[int]] = []
    for start, end in sorted(matches):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    pieces = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append("[DONNEE_NEUTRALISEE]")
        cursor = end

    pieces.append(text[cursor:])
    return "".join(pieces)


def remove_accents(text: str) -> str:
    text_normalized = unicodedata.normalize('NFD', text)

    return ''.join(
        caractere
        for caractere in text_normalized
        if unicodedata.category(caractere) != 'Mn'
    )

def normalize_prompt(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("Le prompt doit être une chaîne de caractères.")
    text = html.unescape(text)
    text = remove_accents(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.lstrip("﻿")
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    return text.strip()


def validate_prompt(text: str, *, max_length: int = MAX_PROMPT_LENGTH) -> str:
    normalized = normalize_prompt(text)
    if max_length is not None and len(normalized) >= max_length:
        raise PromptValidationError("message trop long : message refusé")
    if detect_prompt_injection(normalized.lower()):
        raise PromptValidationError("injection détectée : message refusé, tentative de manipulation du prompt")
    return normalized