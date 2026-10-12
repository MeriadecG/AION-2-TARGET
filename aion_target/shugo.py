"""Recherche d'un personnage : API officielle NCSoft (serveur exact) + shugo.gg (GS / CP)."""
from __future__ import annotations

import html
import json
import re
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.parse import quote

import httpx

BASE_URL = "https://shugo.gg/character/global/{slug}/{name}"
SEARCH_URL = "https://api-search.plaync.com/aion2global/search/v2/character"
LEADERBOARD_URL = "https://shugo.gg/api/leaderboard/combat-power"
# En-têtes d'un appel à l'API depuis la page classement (identité de l'outil conservée).
API_HEADERS = {"Accept": "application/json", "Referer": "https://shugo.gg/leaderboard"}
USER_AGENT = "aion-target-overlay/0.2 (outil perso)"


@dataclass
class CharacterInfo:
    name: str
    server: str          # slug, ex. "eu-israphel"
    gear_score: int | None
    combat_power: int | None
    level: int | None = None
    char_class: str | None = None
    url: str = ""
    rank: int | None = None   # rang régional par CP (classement shugo.gg), si connu


def _to_int(s: str | None) -> int | None:
    if not s:
        return None
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else None


_TAG_RE = re.compile(r"<[^>]+>")
_NUM = r"([\d][\d,.\s  ]*\d|\d)"
_GS_RE = re.compile(r"gear\s*score\s*[:\-]?\s*" + _NUM, re.IGNORECASE)
_CP_RE = re.compile(r"combat\s*power\s*[:\-]?\s*" + _NUM, re.IGNORECASE)
_GS_TITLE_RE = re.compile(r"\(\s*" + _NUM + r"\s*GS\s*\)", re.IGNORECASE)
# La faction peut s'intercaler entre le niveau et la classe : « Level 45 Asmodians Chanter ».
_LEVEL_CLASS_RE = re.compile(r"Level\s+(\d{1,2})\s+(?:(?:Asmodians?|Elyos)\s+)?([A-Z][a-zA-Z]+)")


def parse_character_page(page: str, name: str, server: str, url: str = "") -> CharacterInfo | None:
    """Extrait GS/CP du HTML. Retourne None si la page ne ressemble pas à un profil."""
    text = html.unescape(_TAG_RE.sub(" ", page))
    text = re.sub(r"\s+", " ", text)

    gs_m = _GS_RE.search(text) or _GS_TITLE_RE.search(text)
    cp_m = _CP_RE.search(text)
    if not gs_m and not cp_m:
        return None

    lc = _LEVEL_CLASS_RE.search(text)
    return CharacterInfo(
        name=name,
        server=server,
        gear_score=_to_int(gs_m.group(1)) if gs_m else None,
        combat_power=_to_int(cp_m.group(1)) if cp_m else None,
        level=int(lc.group(1)) if lc else None,
        char_class=lc.group(2) if lc else None,
        url=url,
    )


# --- API officielle de recherche (NCSoft) -------------------------------------------

def server_slug(region: str, server_name: str) -> str:
    """« Zikel » + « eu » → « eu-zikel » (format des URL shugo.gg)."""
    return f"{region}-{server_name.strip().lower().replace(' ', '-')}"


def parse_official_servers(payload: str | dict, name: str, region: str) -> list[str]:
    """TOUS les serveurs où un personnage s'appelle exactement `name` (homonymes compris)."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    wanted = name.casefold()
    out: list[str] = []
    for entry in data.get("list") or []:
        entry_name = html.unescape(_TAG_RE.sub("", entry.get("name") or "")).strip()
        if entry_name.casefold() == wanted and entry.get("serverName"):
            slug = server_slug(region, entry["serverName"])
            if slug not in out:
                out.append(slug)
    return out


def parse_official_search(payload: str | dict, name: str, region: str) -> str | None:
    """Renvoie le slug du serveur du personnage EXACTEMENT nommé `name`, ou None.

    La recherche officielle est partielle (« Krix » renvoie aussi « Krixou ») et entoure
    le terme de <strong>…</strong> : on nettoie et on compare sans tenir compte de la casse.
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    wanted = name.casefold()
    for entry in data.get("list") or []:
        entry_name = html.unescape(_TAG_RE.sub("", entry.get("name") or "")).strip()
        if entry_name.casefold() == wanted and entry.get("serverName"):
            return server_slug(region, entry["serverName"])
    return None


def _iso_ts(iso: str | None) -> float:
    from datetime import datetime
    if not iso:
        return 0.0
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def parse_leaderboard_entry(e: dict, region: str) -> tuple[CharacterInfo, float] | None:
    """Entrée de l'API classement shugo.gg → (profil, horodatage lastSeen)."""
    name, server_name = e.get("name"), e.get("serverName")
    if not name or not server_name:
        return None
    slug = server_slug(region, server_name)
    info = CharacterInfo(
        name=name, server=slug, gear_score=e.get("gearScore"), combat_power=e.get("combatPower"),
        level=e.get("level"), char_class=e.get("className"),
        url=BASE_URL.format(slug=slug, name=quote(name, safe="")), rank=e.get("rank"))
    return info, _iso_ts(e.get("lastSeen"))


class AmbiguousName(ValueError):
    """Le même nom existe sur plusieurs serveurs et rien ne dit lequel est la cible."""

    def __init__(self, name: str, servers: list[str]):
        super().__init__(f"{name} existe sur {len(servers)} serveurs : {', '.join(servers)}")
        self.name, self.servers = name, servers


class NetworkError(RuntimeError):
    """Aucune source n'a pu répondre : ne pas mettre en cache « introuvable »."""


class ShugoClient:
    """Trouve un personnage le plus vite possible, sans marteler les sites.

    Lancés EN MÊME TEMPS :
      - shugo.gg sur le serveur déjà connu du joueur (cache), s'il y en a un ;
      - shugo.gg sur les `fanout` serveurs où l'on trouve le plus souvent des joueurs ;
      - la recherche officielle NCSoft, qui donne le serveur exact (y compris un serveur
        jamais vu, qui est alors appris) → requête shugo.gg ciblée sur ce serveur.
    Le premier profil trouvé l'emporte. Si l'API officielle ne répond pas, on se rabat
    sur tous les serveurs connus. HTTP/2 : une seule connexion par site, gardée ouverte.
    """

    def __init__(self, servers: list[str], region: str = "eu", fanout: int = 3,
                 timeout: float = 6.0, http: httpx.Client | None = None, on_new_server=None):
        self.servers = list(servers)
        self.region = region
        self.fanout = max(0, fanout)
        self.on_new_server = on_new_server
        self.http = http or httpx.Client(
            http2=True, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120))
        self._pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="lookup")
        self._lock = threading.Lock()

    # -- requêtes élémentaires --
    def fetch(self, name: str, slug: str) -> CharacterInfo | None:
        url = BASE_URL.format(slug=slug, name=quote(name, safe=""))
        resp = self.http.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return parse_character_page(resp.text, name, slug, url)

    def search_official(self, name: str) -> str | None:
        servers = self.search_official_all(name)
        return servers[0] if servers else None

    def search_official_all(self, name: str) -> list[str]:
        resp = self.http.get(SEARCH_URL, params={
            "keyword": name, "serverId": "", "page": 1, "size": 30,
            "region": self.region, "localeInfo": "en-US"})
        resp.raise_for_status()
        return parse_official_servers(resp.json(), name, self.region)

    def quick_search(self, name: str) -> tuple[CharacterInfo, float] | None:
        """Recherche par nom dans la BASE de shugo.gg (API classement, paramètre q).

        Pas d'appel à NCSoft côté shugo.gg : réponse quasi instantanée, mais score de la
        dernière consultation (horodatage `lastSeen` renvoyé avec)."""
        resp = self.http.get(LEADERBOARD_URL, headers=API_HEADERS, params={
            "region": "GLOBAL", "subRegion": self.region, "q": name,
            "sort": "combatPower", "page": 1, "limit": 10})
        found = self.quick_search_all(name, resp)
        return found[0] if len(found) == 1 else None

    def quick_search_all(self, name: str, resp=None) -> list[tuple[CharacterInfo, float]]:
        """Toutes les entrées de la base portant EXACTEMENT ce nom (une par serveur)."""
        if resp is None:
            resp = self.http.get(LEADERBOARD_URL, headers=API_HEADERS, params={
                "region": "GLOBAL", "subRegion": self.region, "q": name,
                "sort": "combatPower", "page": 1, "limit": 10})
            resp.raise_for_status()
        wanted = name.casefold()
        out = []
        for e in resp.json().get("entries") or []:
            if (e.get("name") or "").casefold() == wanted:
                parsed = parse_leaderboard_entry(e, self.region)
                if parsed and all(parsed[0].server != o[0].server for o in out):
                    out.append(parsed)
        return out

    def warmup(self):
        """Ouvre les connexions (TLS + HTTP/2) à l'avance : la 1re recherche est plus rapide."""
        def ping(url):
            try:
                self.http.head(url)
            except httpx.HTTPError:
                pass
        for url in ("https://shugo.gg/", SEARCH_URL, LEADERBOARD_URL):
            self._pool.submit(ping, url)

    def _learn(self, slug: str):
        with self._lock:
            if slug not in self.servers:
                self.servers.append(slug)
                if self.on_new_server:
                    self.on_new_server(slug)

    def _promote(self, slug: str):
        with self._lock:
            if slug in self.servers and self.servers[0] != slug:
                self.servers.remove(slug)
                self.servers.insert(0, slug)

    # -- recherche complète --
    def lookup(self, name: str, hint: str | None = None) -> CharacterInfo | None:
        """Profil trouvé, None si introuvable, NetworkError si personne n'a pu répondre."""
        tried: set[str] = set()
        pending: dict = {}

        def fetch_on(slug):
            if slug and slug not in tried:
                tried.add(slug)
                pending[self._pool.submit(self.fetch, name, slug)] = ("shugo", slug)

        fetch_on(hint)
        if not hint:
            with self._lock:
                first = list(self.servers[: self.fanout])
            for slug in first:
                fetch_on(slug)
            pending[self._pool.submit(self.search_official_all, name)] = ("search", None)

        # Sans serveur connu, une fiche trouvée n'est acceptée que si la recherche officielle
        # confirme que ce nom n'existe que sur ce serveur (homonymes → AmbiguousName).
        found: dict[str, CharacterInfo] = {}
        official: list[str] | None = None if not hint else [hint]
        result, answered, errors = None, 0, 0

        def decide():
            if hint:
                return found.get(hint)
            if official is None:
                return None
            if len(official) == 1:
                return found.get(official[0])
            return None

        while pending and result is None:
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                kind, slug = pending.pop(fut)
                try:
                    value = fut.result()
                except (httpx.HTTPError, ValueError):
                    errors += 1
                    if kind == "search":
                        # API officielle indisponible : on interroge tous les serveurs connus et
                        # on n'accepte qu'un résultat UNIQUE (voir plus bas).
                        official = []
                        with self._lock:
                            rest = list(self.servers)
                        for s_ in rest:
                            fetch_on(s_)
                    continue
                answered += 1
                if kind == "shugo" and value:
                    found[slug] = value
                if kind == "search":
                    official = list(value or [])
                    if len(official) > 1:
                        for s_ in official:
                            self._learn(s_)
                        for fut2 in pending:
                            fut2.cancel()
                        raise AmbiguousName(name, official)
                    for s_ in official:
                        self._learn(s_)
                        fetch_on(s_)
                result = decide()
                if result:
                    break
        for fut in pending:
            fut.cancel()

        if result is None and official == [] and len(found) == 1:
            result = next(iter(found.values()))         # recherche officielle muette : unique trouvé
        elif result is None and official == [] and len(found) > 1:
            raise AmbiguousName(name, sorted(found))
        if result:
            self._promote(result.server)
            return result
        if answered == 0 and errors:
            raise NetworkError(f"aucune réponse pour {name}")
        return None
