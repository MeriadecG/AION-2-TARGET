# Target : affichage GS/CP rapide (version 2026.10.12.12)

*Rédigé le 12/10/2026. Tous les chiffres viennent de mesures réelles : soit les journaux envoyés
en jeu, soit les bancs d'essai du dépôt sur la machine de développement (2 cœurs). Le gain en jeu
reste **à mesurer chez toi** : le rapport de vitesse le montre maintenant étape par étape.*

## 1. Diagnostic : d'où venait le retard

Chemin critique, de la sélection à l'écran (fonctions réelles) :

```
image (capture.py : PortalGrabber.grab / MssGrabber.grab)
 → OCR une ligne (ocr.py : NameReader.read, ~20 ms)          → PNJ ? (npc.py) → nom réel ? (names.py)
 → validation (tracker.py : TargetTracker.feed)                 1 lecture si nom connu, sinon 2
 → score (lookup.py : PlayerLookup.cached → cache.py)           mémoire locale
 → signal Qt status (thread de lecture → thread de l'interface) → Overlay.show_status → peinture
 en parallèle : PlayerLookup._run → base shugo.gg puis fiche → _on_result → même signal
```

Constats (journal du 12/10 et lecture du code) :

| Cause | Preuve | Effet |
|---|---|---|
| **Réseau pour les joueurs absents de la mémoire** | `base shugo.gg Lis : 51 ms`, `fiche shugo.gg Lis : 3709 ms`, `fiche … Tiuna : 3722 ms` | 0,05–0,4 s (base) à 0,35–3,7 s (fiche). Le seul vrai remède : avoir le score **avant** de cibler. |
| **Cadence de lecture fixe à 100 ms** | `10.0 lectures/s` | un changement de cible attendait en moyenne ~50 ms (jusqu'à 100 ms) avant même l'OCR. |
| **Scores lus dans SQLite à chaque tour** | `cached()` + `ambiguous()` + `get()` = 4 à 5 requêtes SQLite par lecture, sous le même verrou que les écritures (import, préchargement, groupe) | coût faible en général, mais **attente possible derrière une écriture** ; aucune garantie « jamais d'attente ». |
| **Peinture différée** | `Overlay.show_status` appelait `update()` ; les écritures d'historique (SQLite) s'exécutaient dans le même thread avant la peinture | le score « prêt » n'était pas forcément peint tout de suite. |
| **Préchargement tardif et lent** | moteur : personnages publiés toutes les **2 s** ; Python : consultation locale seulement au moment d'une requête réseau, **1 requête / 3 s** tous profils confondus, refus 403/429 = pause fixe | un joueur observé n'était souvent pas encore en mémoire quand on le ciblait. |
| **Préchargement lié au DPS meter** | moteur démarré uniquement si « Activer le DPS meter » | sans DPS meter, aucun préchargement. |
| **Mesures trompeuses** | « âge image lue 0 ms » : horodatage pris à la lecture, pas à la capture ; « Affiché … après le 1er texte lu » ne se remettait pas à zéro sur un passage direct A → B | impossible de savoir où partait le temps. |
| PNJ cherchés sur shugo.gg | `fiche shugo.gg X54Vakron … introuvable` | requêtes inutiles (corrigé en .11). |

L'OCR n'est **pas** le goulot : 19–21 ms en jeu (journal), 20 ms sur les captures réelles du dépôt.

## 2. Changements (par fichier)

| Fichier | Changement | Pourquoi |
|---|---|---|
| `cache.py` | **index RAM** nom → serveur → (profil, horodatage), chargé à l'ouverture, tenu à jour à chaque écriture (mêmes règles que SQLite : import jamais plus récent qu'une donnée locale, rang conservé, écriture partielle du groupe sans perte de classe/niveau) ; lectures sans SQLite ni son verrou | « aucune attente » pour un joueur connu |
| `shugo.py` | `CharacterInfo.updated` (hors égalité) ; refus 403/429 : `Retry-After` respecté, sinon 5 min doublées à chaque refus rapproché (max 1 h) | fraîcheur affichable ; politesse envers le site |
| `style.py`, `config.py` | « il y a 25 min / 3 h / 4 j » dans la ligne de détails si le score a plus de 10 min (`show_freshness`) | un score ancien s'affiche tout de suite, honnêtement |
| `overlay.py` | score affiché depuis la RAM **avant** de lancer le rafraîchissement ; peinture **immédiate** (`repaint`) ; cadence adaptative (`poll_idle_ms` = 40 ms tant que l'image ne change pas, sans OCR) ; nom du dictionnaire ou observé validé à la 1re lecture ; chronologie par cible (1re image du nom → validation → score prêt → **cadres peints**) | latence et mesures fiables |
| `lookup.py` | `cached_age`, `needs_network` ; **priorité absolue de la cible** (le préchargement attend) ; une recherche de cible **réutilise** un préchargement en cours du même nom ; **index OCR des personnages observés** (exact d'abord ; une lettre mal lue acceptée seulement si le dictionnaire ne propose pas un autre nom) ; temps réseau mesurés | moins de requêtes, cible d'abord, OCR plus sûr en Abysse |
| `prefetch.py` | consultation **locale immédiate** à chaque observation (sans quota) ; file réseau à priorités (jamais vu > ancien, puis plus récent) ; 20/min uniquement pour le réseau ; identités partielles (sans serveur) gardées et comptées ; observations reçues avant le chargement de la mémoire conservées ; bilan dans le rapport | préchargement utile, sans brutaliser le site |
| `capture.py` | Wayland : GStreamer livre le format **natif** (plus de conversion de tout l'écran) ; seules les **lignes de la zone** sont copiées ; **jamais d'attente** si une image existe ; âge réel de l'image (horodatage de capture) ; écart entre images nouvelles | CPU et mesure honnête |
| `dps_ui.py`, `engine_client.py`, `config.py` | case **« Observation réseau légère (sans DPS) »** : moteur lancé sans calcul affiché (`publish_dps=false`, ignoré sans risque par un ancien moteur) | préchargement sans DPS meter |
| `engine/src/main.rs` | personnages observés publiés toutes les **250 ms** (au lieu de 2 s) avec horodatage ; commande `publish_dps` | préchargement plus tôt |
| `metrics.py` | nouvelles étapes (voir §4) + lignes de bilan (préchargement, mémoire) | rapport de vitesse complet |
| `simulate.py`, `tools/bench_*.py` | bancs d'essai reproductibles | chiffres avant/après |

Rien n'a été retiré (DPS, ping, historiques, événements, Farm, mises à jour, PNJ, homonymes).

## 3. Tests et bancs d'essai (exécutés)

- `pytest` : **316 tests passent** (dont `tests/test_fast_target.py` : index RAM sous verrou
  SQLite, règles d'écriture, fraîcheur, Retry-After, priorité cible, réutilisation d'une requête
  en cours, index des observés, file à priorités, sans serveur, observations précoces, report
  pendant la recherche de la cible, transitions **A→B→A**, homonymes, PNJ, panne réseau, joueur
  observé validé dès la 1re lecture ; `tests/test_capture_portal.py` : découpe Wayland).
- `cargo test --release` (moteur) : OK ; rejeu sur trames réelles : OK.

### Banc de bout en bout `python -m aion_target.simulate 30`

Vrais `Worker`, `TargetTracker`, `PlayerLookup`, mémoire, dictionnaire, filtre PNJ, `Overlay` et
peinture Qt (instant du `paintEvent` réel) ; capture, OCR (20 ms, relecture 45 ms) et réseau
(base 150 ms, fiche 900 ms) simulés. 90 changements de cible vers des joueurs connus (score
vieux de 3 jours), puis vide, homonymes, PNJ, joueur inconnu. Même banc lancé sur le commit
précédent (`7214728`) :

| changement de cible → cadres peints | avant (7214728) | après |
|---|---|---|
| joueur en mémoire, p50 | 76 ms | **44–46 ms** |
| joueur en mémoire, p95 | 119–124 ms | **59–63 ms** |
| pire cas mesuré | 305–319 ms | **69–98 ms** |
| joueur inconnu (réseau simulé 150 ms) | inclus dans le pire cas | 228–254 ms |
| affichage erroné / homonyme / PNJ | aucun | aucun |

Décomposition « après » (p50) : OCR 20 ms · score prêt 21 ms après la 1re image du nom ·
passage au thread d'interface + peinture 6 ms · le reste = attente de la lecture suivante.
**Cible de conception (< 150 ms après une image exploitable, joueur préchargé) : atteinte sur ce
banc**, à confirmer en jeu (capture réelle et charge du jeu en plus).

### OCR `python tools/bench_ocr.py 60` (2 vraies captures, 120 lectures par variante)

| variante | exact | p50 | p95 |
|---|---|---|---|
| actuelle (une ligne, 2 threads) | 120/120 | 20,6 ms | 26,3 ms |
| marges rognées | 120/120 | 20,1 ms | 27,8 ms |
| 1 thread | 120/120 | 28,3 ms | 43,6 ms |
| détection complète | 120/120 | 19,4 ms | 26,7 ms |

Aucune variante n'est nettement meilleure : **réglage actuel conservé** (pas de changement sans
gain mesuré).

### Découpe Wayland `python tools/bench_capture.py`

Copie + décodage par lecture : 1,56 ms → **0,07 ms** (2560×1440), 0,90 → 0,06 ms (1920×1080),
hors conversion plein écran de GStreamer (supprimée, non mesurable ici). Gain CPU modeste mais
réel, 10 à 25 fois par seconde.

### Non testé ici (à valider en jeu)

- Capture PipeWire réelle (GStreamer indisponible sur la machine de développement : la découpe
  est testée avec un faux GStreamer ; si l'écran partagé ne s'affiche plus, le signaler).
- Latence réelle de shugo.gg / NCSoft, charge du jeu, Windows.
- Taux de personnages observés avec serveur fiable, délai entre observation et ciblage.

## 4. Rapport de vitesse (bouton « Copier le rapport de vitesse »)

Par étape, p50 / p95 / p99 / max sur les vrais échantillons de la session :
`frame_age` (âge réel de l'image) · `new_frame_gap` (écart entre images nouvelles) · `capture` ·
`ocr` · `resolve` · `full_reread` (seconde lecture) · `confirm` (1re image → cible validée) ·
`ram` (mémoire) · `remote_quick` / `remote_full` (réseau) · `first_cache` / `first_net` (score
prêt) · `fresh` (actualisé) · `deliver` (score émis → cadres peints) · `paint_first_cache` /
`paint_first_net` / `paint_fresh` (1re image du nom → **cadres peints**).
Plus : personnages observés / avec serveur / sans serveur (homonymes) / trouvés en mémoire
(récents, anciens, absents) / requêtes envoyées / reportées.

## 5. Ce que le réseau passif voit vraiment (preuves dans le code d'A2Tools 7186846)

- **Apparitions** : enregistrements de joueurs `44/45 36` (`note_player_spawn`) + pseudos
  (`append_nickname`), serveur via `note_player_server` quand il est présent → ce sont nos
  « personnages observés ». Le moteur ne publie qu'**une fois** chaque (nom, serveur).
- **Disparitions** : le jeu envoie un drapeau « sorti du monde » (valeur 7, `stream_processor.rs`
  l. 424) mais A2Tools ne le garde **que pour les invocations** (`note_despawn`). On ne peut donc
  pas dire qui est **encore proche** : le rapport parle de « vus cette session » et « vus < 2 min »
  (dernière observation), jamais de « joueurs de l'instance ».
- **Groupe** : nom, serveur, GS/CP des membres (repris tels quels).
- **Cible sélectionnée** : aucun message trouvé (la sélection est locale au client). L'**OCR reste
  nécessaire** pour savoir qui est ciblé ; le réseau sert à avoir le score **déjà en mémoire**.

## 6. Tester en Abysse

1. Mettre à jour (relancer l'appli : version **2026.10.12.12**). Linux : l'onglet DPS redemande
   l'autorisation de capture après la mise à jour du moteur (bouton, mot de passe).
2. Onglet DPS : cocher **« Observation réseau légère (sans DPS) »** (ou garder le DPS meter).
   L'état doit passer à « Flux du jeu repéré (port …) ».
3. En Abysse, rester ~1 min dans une zone fréquentée **sans cibler**, puis cibler une dizaine de
   joueurs différents, en revenant sur certains (A → B → A).
4. Fenêtre principale → **« Copier le rapport de vitesse »** et me l'envoyer. À regarder :
   - `paint_first_cache` p50/p95 : objectif < 150 ms ;
   - ligne « Préchargement » : observés, avec serveur, trouvés en mémoire, requêtes ;
   - `frame_age` : si élevé (> 50 ms), c'est la capture KDE qui retarde ;
   - `first_net` : joueurs encore absents de la mémoire (réseau).
5. Vérifier à l'œil : jamais le score d'un autre joueur, « Homonymes » quand le serveur est
   inconnu, rien sur un PNJ, « il y a … » sur un score ancien qui disparaît une fois actualisé.

## 7. Risques

- Observation réseau : même profil que le DPS meter (lecture passive, risque faible mais pas nul,
  même avertissement) ; désactivée par défaut.
- Requêtes de préchargement : 20/min au plus, jamais pendant la recherche de la cible, pause sur
  refus (`Retry-After`). Si shugo.gg refuse souvent, réduire ou couper « précharger » (config
  `dps_prefetch`).
- Index RAM : ~quelques Mo pour 21 000 joueurs ; rechargé après une purge de taille.
