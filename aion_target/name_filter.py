"""Nettoyage du texte OCR et filtre « est-ce plausiblement un nom de joueur ? »."""
import re
import unicodedata

# Longueur max d'un nom de personnage (marge de sécurité).
MAX_LEN = 16
MIN_LEN = 2

# Caractères que l'OCR ajoute souvent autour du nom (bordures, icônes, ponctuation).
_STRIP_CHARS = " \t\r\n|[](){}<>.,;:'\"`~_-=+*#@!?/\\"

# Un nom de joueur = lettres (tous alphabets, y compris CJK/cyrillique) et chiffres, sans espace.
_PLAYER_RE = re.compile(r"^[^\W_]+$", re.UNICODE)

# Préfixes de niveau que l'interface peut coller au nom : « Lv.45 », « Nv 45 », « 45 »...
_LEVEL_PREFIX_RE = re.compile(r"^(?:lv|nv|niv|lvl)\.?\s*\d{1,2}\s+|^\d{1,2}\s+", re.IGNORECASE)


def clean(raw: str) -> str:
    """Normalise le texte OCR : unicode NFC, retire le préfixe de niveau et les parasites."""
    text = unicodedata.normalize("NFC", raw or "").strip()
    text = _LEVEL_PREFIX_RE.sub("", text)
    return text.strip(_STRIP_CHARS)


# Ligne du nom : « 45 Nom », « 45Nom » (OCR qui colle les mots). Le reste commence par une lettre.
_LEVEL_LINE_RE = re.compile(r"^\s*(\d{1,2})\s*([^\W\d_].*?)\s*$")
# Distance affichée à droite du nom (« 2 m », « 12m », « 2m€ ») que l'OCR peut coller au nom.
_TRAILING_DISTANCE_RE = re.compile(r"\s*\d{1,3}(?:[.,]\d)?\s*m\W*$")
_DISTANCE_ONLY_RE = re.compile(r"^\s*\d{1,3}(?:[.,]\d)?\s*m\W*$", re.IGNORECASE)
_LEVEL_ONLY_RE = re.compile(r"^\s*\d{1,2}\s*$")


# Abysse : « leyndell [Isr] » (OCR : crochets parfois lus comme parenthèses).
_TAGGED_RE = re.compile(r"^\s*(\S+?)\s*[\[(\{]\s*([A-Za-z]{2,5})\s*[\])\}]")


def _bounds(box):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), max(xs), (min(ys) + max(ys)) / 2, max(ys) - min(ys)


def parse_target(lines: list[tuple[str, float, list]]) -> tuple[str, str | None]:
    """(nom de la cible, balise serveur Abysse ou None) à partir des lignes OCR.

    Cadre normal : légion, titre, « 45 Nom  12 m ».  Abysse : légion, rang, « Nom [Isr]  18 m ».
    Règles, dans l'ordre :
      1. ligne du NIVEAU « 45 Nom » : elle décide seule (texte non plausible → PNJ → rien) ;
      2. bloc « 45 » + bloc voisin à droite ;
      3. « Nom [Srv] » (Abysse) : nom + balise serveur ;
      4. bloc sur la même rangée que la DISTANCE (« 18m »), à sa gauche ;
      5. sinon la ligne plausible la plus BASSE (le nom est juste au-dessus de la barre de vie,
         la légion tout en haut : jamais la légion).
    """
    if not lines:
        return "", None
    lines = [(t, s, b) for t, s, b in lines if t and t.strip()]

    # 1. « 45 Nom »
    for text, _, _ in lines:
        m = _LEVEL_LINE_RE.match(_TRAILING_DISTANCE_RE.sub("", text))
        if m and not _TAGGED_RE.match(m.group(2)):
            name = clean(m.group(2))
            return (name if is_plausible_player(name) else ""), None

    # 2. « 45 » + voisin à droite
    for text, _, box in lines:
        if not _LEVEL_ONLY_RE.match(text):
            continue
        _, right, cy, height = _bounds(box)
        best, best_dx = None, None
        for other, _, obox in lines:
            if other is text or _LEVEL_ONLY_RE.match(other) or _DISTANCE_ONLY_RE.match(other):
                continue
            left, _, ocy, _ = _bounds(obox)
            dx = left - right
            if abs(ocy - cy) <= height and -height <= dx <= 4 * height:
                if best_dx is None or dx < best_dx:
                    best, best_dx = other, dx
        if best is not None:
            name, tag = _split_tag(best)
            return (name if is_plausible_player(name) else ""), tag

    # 3. « Nom [Srv] »
    for text, _, _ in lines:
        m = _TAGGED_RE.match(_TRAILING_DISTANCE_RE.sub("", text))
        if m:
            name = clean(m.group(1))
            if is_plausible_player(name):
                return name, m.group(2)

    # 4. même rangée que la distance
    for text, _, box in lines:
        if not _DISTANCE_ONLY_RE.match(text):
            continue
        left_d, _, cy, height = _bounds(box)
        best, best_dx = None, None
        for other, _, obox in lines:
            if other is text or _DISTANCE_ONLY_RE.match(other):
                continue
            _, right, ocy, _ = _bounds(obox)
            if abs(ocy - cy) <= height and right <= left_d + height:
                if best_dx is None or left_d - right < best_dx:
                    best, best_dx = other, left_d - right
        if best is not None:
            name, tag = _split_tag(best)
            return (name if is_plausible_player(name) else ""), tag

    # 5. ligne plausible la plus basse
    candidates = [(t, b) for t, _, b in lines if is_plausible_player(clean(t))]
    if not candidates:
        return "", None
    text, _ = max(candidates, key=lambda c: _bounds(c[1])[2])
    return clean(text), None


def _split_tag(text: str) -> tuple[str, str | None]:
    text = _TRAILING_DISTANCE_RE.sub("", text)
    m = _TAGGED_RE.match(text)
    if m:
        return clean(m.group(1)), m.group(2)
    return clean(text), None


def pick_name(lines: list[tuple[str, float, list]]) -> str:
    """Nom de la cible seulement (voir parse_target)."""
    return parse_target(lines)[0]


def server_from_tag(tag: str | None, servers: list[str]) -> str | None:
    """« Isr » → « eu-israphel » parmi les serveurs connus (I/l confondus par l'OCR)."""
    if not tag:
        return None
    norm = tag.lower().replace("l", "i")
    matches = [s for s in servers
               if s.split("-", 1)[-1].lower().replace("l", "i").startswith(norm)]
    return matches[0] if len(matches) == 1 else None


def is_plausible_player(name: str, blacklist: set[str] | None = None) -> bool:
    """Filtre peu coûteux, avant toute requête réseau.

    Rejette : vide, trop court/long, espaces (PNJ et boss ont des noms composés),
    caractères non alphanumériques, nom uniquement numérique, noms en liste noire.
    """
    if not name or not (MIN_LEN <= len(name) <= MAX_LEN):
        return False
    if not _PLAYER_RE.match(name):
        return False
    if name.isdigit() or _DISTANCE_ONLY_RE.match(name):
        return False
    if blacklist and name.lower() in blacklist:
        return False
    return True
