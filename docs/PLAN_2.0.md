# Aion Target 2.0 — audit de départ et plan technique

*Rédigé le 12/10/2026 à partir du code réel du dépôt et des cinq projets cités (clones datés du jour).*

## 1. Ce qui existe réellement (dépôt `main`, version 2026.10.12.5 → .6)

| Module | État vérifié | Remarques |
|---|---|---|
| Capture | `capture.py` : mss (Windows/X11) ou portail ScreenCast + PipeWire (Wayland), jeton de restauration mémorisé | une seule capture partagée par Target et Farm (le portail renvoie la dernière image) |
| OCR / Target | `ocr.py` RapidOCR 2 threads, saut des images identiques ; `names.py` dictionnaire ~21 000 noms ; `tracker.py` confirmation | chemin critique mesuré ~0,16 s cache chaud, réseau 0,3–5 s |
| Recherche | `lookup.py` + `shugo.py` : base shugo.gg (rapide) puis fiche (lente), recherche officielle NCSoft en parallèle | — |
| Mémoire | `cache.py` SQLite `players.db`, limite de taille | **défaut trouvé : identité = nom seul** (voir §5) — corrigé |
| Événements | `events.py`, `notify.py`, `events_ui.py` (D-Bus / barre système), calendrier JSON | inchangé |
| Farm | `currency.py`, `currency_ui.py` | inchangé |
| Mises à jour | `updater.py` : version de `main`, archive vérifiée (compilation, liste blanche de types, pas de binaire natif), échange atomique | **ne peut pas livrer un exécutable natif** : nécessaire pour un moteur Rust (§7) |
| Distribution | `install-linux.sh`, NSIS + portable via GitHub Actions | — |

Le README était fidèle sur l'essentiel ; la seule divergence trouvée est fonctionnelle (homonymes).

## 2. Audit des cinq projets

| Projet | Langage / licence | Source | Collecte | Windows / Linux / Wayland | DPS · ping · historique | Identité utile en Abysse | Réutilisable ? |
|---|---|---|---|---|---|---|---|
| **A2Tools** (taengu, v2.0.62, commit `7186846`) | Rust + Tauri, **GPL-3.0** | complète | capture passive (Npcap / libpcap `cap_net_raw`), lecture du flux serveur → client ; trames **compressées LZ4, pas chiffrées** côté serveur ; requête ping cliente chiffrée (seule sa taille/horodatage sont utilisés) | Windows ✓ · Linux ✓ (paquets rpm, KDE Wayland, règle KWin « au-dessus du plein écran ») | DPS par joueur/compétence, soins, DoT, invocations, **ping**, historique, export | **noms + id d'entité des joueurs qui apparaissent** (enregistrements `45 36`), serveur via certains enregistrements, **GS/CP du groupe** (liste de groupe) ; **aucun paquet « changement de cible »** trouvé | **Oui** : le cœur d'analyse (`capture`, `combat`, `entity`, `rederive`) compile **sans** Tauri ni pcap (`cargo check --no-default-features --lib` : OK ici) |
| **AbyssLogs** (karim-mo) | — | **aucun code** (README + images) | inconnue | Windows | riche (README) | inconnue | Non (pas de licence ni de source) |
| **Eclipse** (Iota-Nine) | binaire | **binaires seulement**, « all rights reserved » | inconnue | Windows seulement | riche | inconnue | Non |
| **Aion2Flow** (cloris-chan) | C# .NET, GPL-3.0 | complète | **WinDivert** (pilote, admin) | Windows seulement | DPS, soins, boucliers, latence, historique de session | noms observés | Partiellement (C#, pas de Linux) |
| **AION2 DPS Meter** (cyberbadger6969) | C# .NET, GPL-3.0 | complète | Npcap (SharpPcap) | Windows seulement | DPS, historique, GS/CP du groupe (« la liste de groupe est le seul endroit où le jeu les envoie ») | groupe | Partiellement (C#, pas de Linux) |

**Moteur retenu : A2Tools, uniquement son cœur d'analyse**, intégré comme bibliothèque Rust
(dépendance git épinglée sur un commit), sans son interface Tauri ni ses services en ligne.
Raisons : seul projet à source complète **et** multiplateforme (Linux/Wayland compris), le plus
actif (commit du jour, suit les changements d'opcodes du jeu comme celui de juin 2026), cœur
déjà isolé et testable hors application, ping et historique inclus.

## 3. Règles de NCSoft et incertitudes

- Aucune déclaration officielle trouvée autorisant ou interdisant les compteurs de dégâts.
  Les politiques d'exploitation sanctionnent les « programmes tiers donnant un avantage
  déloyal » (formulation large) ; les poursuites publiques visent les **macros**
  (déc. 2025, janv. 2026). La manipulation de paquets est explicitement proscrite.
- Ce que fait le moteur retenu : **lecture passive** d'une copie du trafic (comme Wireshark),
  aucune injection, aucune écriture, aucune modification du client, **pas de déchiffrement**
  (le flux lu n'est que compressé). C'est le profil le moins risqué, **pas un risque nul**.
- Décision de conception : module **désactivé par défaut**, activation explicite avec
  avertissement ; Target reste en OCR + API et fonctionne sans lui.

## 4. Permissions et dépendances sur le PC final

- **Windows** : la capture exige **Npcap** (installé par l'utilisateur depuis npcap.com ;
  sa licence gratuite **interdit de l'embarquer** dans notre installateur sans licence OEM).
  A2Tools demande aussi l'administrateur. → l'onglet DPS propose un lien d'installation de Npcap.
- **Linux** : capacité `cap_net_raw` sur le binaire du moteur (une commande `pkexec setcap`
  à l'installation **et à chaque mise à jour du moteur**, car remplacer le fichier efface la
  capacité). Le jeu sous Proton passe par la pile réseau de l'hôte : capture possible.
- **Licence** : intégrer du code GPL-3.0 impose de distribuer Aion Target sous **GPL-3.0**
  (code source public — c'est déjà le cas — et crédits). Le dépôt n'a aujourd'hui aucune licence.

## 5. Défaut corrigé dès maintenant : homonymes entre serveurs

Avant : la mémoire SQLite était indexée par le **nom seul** et la recherche sans serveur
acceptait la **première** fiche trouvée. Deux « Krix » de serveurs différents s'écrasaient, et
la cible pouvait recevoir le GS/CP d'un homonyme.

Après (2026.10.12.6) :
- clé `nom@serveur` (schéma v2, migration automatique, **copie `players.db.bak-v1`** avant) ;
- sans serveur connu, un nom présent sur plusieurs serveurs est **ambigu** : aucun score,
  affichage « Homonymes (N serveurs) : score non attribué » ;
- sans balise de serveur, une fiche n'est acceptée que si la recherche officielle NCSoft
  confirme que le nom n'existe que sur ce serveur ;
- en Abysse, la balise `[Isr]` donne le serveur : résultat exact ;
- un résultat réseau n'est affiché que pour la cible **et** le serveur courants
  (génération de cible : jamais de réponse tardive d'une cible précédente) ;
- mesures p50/p95/p99 par étape (capture, OCR, résolution, 1er score mémoire / réseau,
  score actualisé) dans « Copier le rapport de vitesse ».

## 6. Ce que le flux passif peut — et ne peut pas — apporter en Abysse

- **Peut** : noms (et souvent serveurs) des personnages **qui apparaissent dans votre champ de
  vision** au fil du temps → préchargement de leurs GS/CP avant même qu'on les cible.
- **Ne peut pas (en l'état du code étudié)** : donner la **liste complète** des joueurs d'une
  instance, ni signaler la **cible sélectionnée** (choix purement local au client). L'OCR
  reste donc indispensable pour savoir *qui* est ciblé ; le gain vient de ce que la fiche est
  **déjà en mémoire** quand on cible.
- À vérifier sur captures réelles avant d'en faire une promesse : taux de personnages
  observés avec serveur fiable, délai entre apparition et ciblage.

## 7. Architecture choisie : migration progressive (chemin A)

```
 Python / PyQt6 (UI actuelle, Target, Notifications, Farm, Familiers)
        │  IPC : JSON par lignes sur stdin/stdout, protocole versionné {"v":1,"type":…}
        ▼
 aion-engine (Rust, binaire séparé, GPL-3.0)
   capture  : pcap (Npcap / libpcap), détection du port du jeu
   combat   : cœur A2Tools (StreamAssembler → StreamProcessor → DataStorage/DpsCalculator)
   identity : personnages observés (id, nom, serveur, horodatage, source)
   latency  : ping du jeu (horodatages des trames ping)
```

- **Pourquoi pas Tauri complet (B)** : réécrire capture Wayland/portail, overlays « infobulle »
  au-dessus du plein écran, notifications D-Bus et OCR serait long et fragiliserait ce qui
  marche ; A2Tools lui-même documente des contournements Wayland lourds.
- Le moteur ne démarre **que** si l'onglet DPS est activé (aucune ressource sinon) ; s'il
  plante ou manque de permissions, Target continue en OCR.
- Pas de seconde capture d'écran : le moteur ne capture que le réseau.

### Distribution du moteur et mises à jour (phase 6)
- GitHub Actions : matrice Windows + Linux, tests, puis publication d'un **manifeste signé**
  (`manifest.json` : version, fichiers, SHA-256, taille) signé **Ed25519** ; clé privée
  dans un **secret GitHub** uniquement, clé publique embarquée dans l'appli.
- `updater.py` : n'accepte un binaire natif que s'il est listé dans un manifeste dont la
  signature est valide ; téléchargement → vérification → remplacement au prochain lancement
  (jamais de remplacement d'un exécutable en cours) ; ancienne version gardée pour retour
  arrière ; Linux : `pkexec setcap` après mise à jour du moteur.

## 8. Étapes et état

| # | Étape | État |
|---|---|---|
| 1 | Audit + plan (ce document) | fait |
| 2 | Identité nom@serveur, ambiguïtés, génération de cible, mesures p50/p95/p99 | **fait, testé (269 tests)** |
| 3 | Historique des rencontres (SQLite, confiance, source, favoris, recherche) | **fait** (`history.py`) |
| 4 | Licence GPL-3.0 + crédits | **fait** (`LICENSE`, `NOTICE.md`) |
| 5 | Moteur Rust `aion-engine` (capture + cœur A2Tools + IPC) et onglet DPS | **fait** ; validé en rejeu sur trames réelles publiées ; **à valider en jeu** |
| 6 | Préchargement des personnages observés (quotas, backoff 403/429, TTL) | **fait** (`prefetch.py`) |
| 7 | Manifeste signé, CI Windows/Linux pour le binaire | **fait** ; secret `ENGINE_SIGNING_KEY` en place (vérifié : mise à jour signée acceptée en .9) |
| 8 | Historique des combats, overlay DPS configurable | **fait** ; profils d'overlay et raccourcis : à faire |
| 10 | Moteur unique partagé, observation dans Target, Farm Kinah/minute, Familiers, rapport de diagnostic | **fait** (2026.10.12.13) — voir `docs/MODULES.md` |
| 9 | Target ultra-rapide : index RAM, préchargement local immédiat, observation légère, mesures jusqu'à la peinture | **fait** (2026.10.12.12) — voir `docs/PERF_TARGET.md` ; à mesurer en Abysse |

## 9. Risques

- Sanction de compte théoriquement possible (§3) ; mitigé par défaut désactivé + avertissement.
- Le jeu change ses opcodes (déjà arrivé) : dépendance épinglée, mise à jour suivie d'A2Tools.
- Permissions (Npcap / setcap) : friction à l'installation, documentée dans l'appli.
- GPL-3.0 : obligation de publier le code des versions distribuées (déjà public).
