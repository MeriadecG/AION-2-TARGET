"""Import du classement shugo.gg de toute une région (≈ 21 000 joueurs EU, ≈ 3 Mo).

- Arrière-plan, une page de 50 joueurs toutes les `delay` secondes (≈ 4 min pour l'EU).
- Rafraîchissement complet au plus toutes les 24 h ; reprise à la page où l'on s'était arrêté.
- Poli : à la 1re erreur (429 « trop de requêtes », panne…), on s'arrête jusqu'au prochain lancement.
- Ne remplace jamais un score local plus récent (horodatage `lastSeen` de shugo.gg).
"""
from __future__ import annotations

import time
import httpx

from .shugo import API_HEADERS
from .shugo import LEADERBOARD_URL as API_URL
from .shugo import CharacterInfo, parse_leaderboard_entry
PAGE_SIZE = 50
REFRESH_EVERY = 24 * 3600


def parse_page(payload: dict, region: str) -> tuple[list[tuple[CharacterInfo, float]], bool, int]:
    """(profils horodatés, encore des pages ?, total) à partir d'une réponse de l'API."""
    items = [p for p in (parse_leaderboard_entry(e, region) for e in payload.get("entries") or []) if p]
    return items, bool(payload.get("hasMore")), int(payload.get("total") or 0)


def explain_error(err: str | None) -> str:
    """Erreur technique → explication compréhensible (affichée dans la fenêtre)."""
    if not err:
        return ""
    if err == "HTTP 403":
        return ("shugo.gg refuse l'accès automatique à son API (403). Les joueurs croisés restent "
                "mémorisés ; on pourra demander l'accord du site sur son Discord.")
    if err == "HTTP 429":
        return "shugo.gg signale trop de requêtes : nouvel essai automatique dans 10 min."
    if err.startswith("HTTP"):
        return f"shugo.gg est indisponible pour l'instant ({err}) : nouvel essai dans 10 min."
    if err.startswith("réseau"):
        return f"Pas de connexion à shugo.gg ({err.split(':', 1)[-1].strip()}) : nouvel essai dans 10 min."
    return f"Réponse inattendue de shugo.gg ({err})."


class LeaderboardImporter:
    def __init__(self, http: httpx.Client, cache, state: dict, region: str = "eu",
                 max_players: int | None = None, delay: float = 0.5,
                 on_new_server=None, on_progress=None, should_stop=lambda: False, clock=time.time):
        self.http = http
        self.cache = cache
        self.state = state                 # persistant (config) : next_page, last_full, total
        self.region = region
        self.max_players = max_players     # None = tout ; 0 = désactivé
        self.delay = delay
        self.on_new_server = on_new_server
        self.on_progress = on_progress     # (importés, total)
        self.should_stop = should_stop
        self.clock = clock

    def due(self) -> bool:
        if self.max_players == 0:
            return False
        if self.state.get("next_page", 1) > 1:     # import interrompu : à reprendre
            return True
        return self.clock() - self.state.get("last_full", 0) >= REFRESH_EVERY

    def _limit_pages(self) -> int | None:
        if self.max_players is None:
            return None
        return max(1, -(-self.max_players // PAGE_SIZE))

    def run(self) -> int:
        """Importe jusqu'à la fin (ou la limite). Renvoie le nombre de profils reçus."""
        if self.max_players == 0:
            return 0
        page = self.state.get("next_page", 1)
        limit = self._limit_pages()
        seen_servers: set[str] = set()
        received = 0
        while not self.should_stop():
            if limit is not None and page > limit:
                break
            try:
                resp = self.http.get(API_URL, headers=API_HEADERS, params={
                    "region": "GLOBAL", "subRegion": self.region, "sort": "combatPower",
                    "page": page, "limit": PAGE_SIZE})
            except httpx.HTTPError as e:
                self.state["last_error"] = f"réseau : {type(e).__name__}"
                return received
            if resp.status_code != 200:
                self.state["last_error"] = f"HTTP {resp.status_code}"   # 403 / 429 / panne
                return received
            try:
                items, has_more, total = parse_page(resp.json(), self.region)
            except ValueError:
                self.state["last_error"] = "réponse illisible (pas du JSON)"
                return received
            self.cache.put_many(items)
            received += len(items)
            for info, _ in items:
                if info.server not in seen_servers:
                    seen_servers.add(info.server)
                    if self.on_new_server:
                        self.on_new_server(info.server)
            self.state["total"] = total
            if self.on_progress:
                self.on_progress(min((page) * PAGE_SIZE, total), total)
            page += 1
            self.state["next_page"] = page
            if not has_more:
                break
            if self.delay:
                time.sleep(self.delay)
        else:
            return received                    # arrêt demandé : reprise plus tard
        # Fin normale (dernière page ou limite atteinte) : prochain passage dans 24 h.
        self.state["next_page"] = 1
        self.state["last_full"] = self.clock()
        self.state.pop("last_error", None)
        return received
