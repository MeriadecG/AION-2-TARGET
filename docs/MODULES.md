# Target, Farm, Familiers — version 2026.10.12.13

*12/10/2026. Ce qui marche réellement, sur quelles preuves, et ce qui reste à vérifier en jeu.*

## 1. Audit : données réellement disponibles

| Donnée | Disponible ? | Preuve |
|---|---|---|
| Joueurs vus (nom, serveur souvent) | oui (réseau) | A2Tools `note_player_spawn`, `append_nickname`, `note_player_server` ; publiés par `engine/src/main.rs` |
| GS/CP du groupe | oui (réseau) | `get_party_members` |
| Cible sélectionnée | **non** | aucun message dans le flux étudié : l'OCR reste nécessaire |
| Solde de Kinah (lié / non lié) | **non** dans le flux | aucun champ kinah / monnaie / inventaire dans `src-tauri/src` d'A2Tools (recherche du code) → lecture à l'écran (`currency.py`) |
| Mort d'un monstre **en combat** | oui (réseau) | `stream_processor.rs` l. 418-420 : drapeau 3 « killed in combat » → `mark_entity_dead` ; les drapeaux 1 (chargement) et 7 (sorti du monde) n'y entrent pas |
| Espèce du monstre | oui (réseau) | `mob_code(id)` donné à l'apparition (`append_mob`) ; noms via `npcs/fr.json` |
| Qui a frappé ce monstre | oui (réseau) | `get_target_snapshot_light(id).actors` ; toi = `local_player_id()` (+ tes invocations) |
| Obtention d'une âme de familier | **non** | aucun événement trouvé : âmes **manuelles** |
| Libellés « lié / non lié » de la barre | **non vérifié** | les sources consultées confirment l'existence du Kinah lié et non lié, pas l'ordre ni l'intitulé des valeurs de la barre → noms au choix, neutres par défaut |

## 2. Ce qui a été fait (fichiers)

| Fichier | Rôle |
|---|---|
| `engine_hub.py` (nouveau) | **un seul moteur** pour les modes `dps`, `observe`, `pets` ; démarre au 1er mode, s'arrête au dernier ; DPS envoyé seulement si `dps` ; états arrêté / démarrage / recherche du flux / actif / erreur / permission manquante / Npcap manquant |
| `observe_ui.py` (nouveau), `control.py` | section Target « Détection et préchargement des joueurs » : case (même réglage `observe_enabled`, rien perdu), précharge shugo.gg (`dps_prefetch`), état, compteurs rafraîchis toutes les 2 s |
| `dps_ui.py` | n'a plus que le DPS meter ; avertissement commun obligatoire (`ack_capture`), états et réparation partagés |
| `engine_client.py` | message `kill`, fonctions du moteur annoncées (`features`) : un ancien moteur est détecté |
| `farm.py` (nouveau), `currency.py`, `currency_ui.py`, `config.py` | **Kinah / minute** ; lecture horodatée (A, B, A − B d'une même capture) ; 2ᵉ emplacement (menu) ; profils de zones par résolution ; étiquettes ; champs de l'overlay ; « (ancien) » après 20 s sans mesure valide |
| `pets.py`, `pets_ui.py` (nouveaux), `assets/familiars.json`, `tools/make_familiars.py`, `tools/data/pets_global_en.txt` | catalogue, progression, paliers, overlay, comptage automatique, journal |
| `engine/src/kills.rs` (nouveau), `engine/src/main.rs` | événements `kill` avec verdict et raison, testés |
| `diag.py` (nouveau) | rapport exportable sans donnée sensible |

Le chemin rapide GS/CP (index RAM, priorité de la cible, OCR, peinture immédiate) n'est pas
touché : les nouveaux modules lisent à 2 s (monnaie, thread à part) ou à la réception d'un
événement, et leurs écrans se rafraîchissent toutes les 1 à 5 s.

## 3. Catalogue des familiers

- 206 familiers (client global) : Cogni 41, Fera 65, Natura 48, Varian 46, Spécial 6. La source
  en annonce 207 : le 7ᵉ spécial est tronqué dans la page (« Two… ») → **non inventé**, ajout
  manuel possible (bouton « Ajouter un familier »).
- Noms : anglais officiels (source) ; **108** ont un nom français, celui du PNJ portant
  exactement le même nom anglais dans les tables d'A2Tools. Les autres gardent le nom anglais
  (la recherche trouve aussi les noms français des monstres associés).
- Monstres associés : « exact » (même nom, preuve) et « même espèce » (le nom du monstre contient
  celui du familier ; indicatif). Les identifiants internes sont ceux des PNJ A2Tools.
- Paliers : 5 / 30 / 105 âmes cumulées (client global, source citée), Corée / Taïwan (Nv 5 à 500,
  palier 4 inconnu), ou personnalisés.
- Sources : https://www.aion2guidance.com/en/pets/ (vérifiée le 12/10/2026),
  https://sagye.kr/tips/pets (paliers KR), A2Tools `src/data/i18n/npcs/{en,fr}.json` (GPL-3.0).

## 4. Tests exécutés

- `pytest` : **333 tests** passent, sous Python 3.13 et 3.12 (version de la CI). Nouveaux : moteur unique DPS + observation + familiers,
  transitions d'état, interface Target/DPS sur le même moteur, session Kinah (2 lectures,
  lecture isolée erronée, grosse variation réelle, valeur négative, pauses, reprise explicite
  après redémarrage), monnaie jamais mélangée entre captures, repli sur le menu, catalogue,
  paliers, sauvegarde, journal, déduplication des morts, morts d'autres joueurs / incertaines /
  monstres sans familier, onglet Familiers de bout en bout, rapport sans donnée sensible, tous
  les fichiers de l'appli livrables par la mise à jour depuis `main`.
- `cargo test --release` : 7 tests (dont 2 nouveaux : seule la mort en combat d'un monstre que
  TU as frappé est « mine », une seule fois ; joueur local inconnu → « uncertain »).
- Banc Target (`python -m aion_target.simulate 30`) : joueur en mémoire → cadres peints p50 44 ms,
  p95 63 ms (identique à la version .12 : aucun ralentissement du chemin GS/CP).
- Démarrage complet de l'application hors écran : un seul moteur lancé en mode `observe`,
  206 familiers chargés, rapport de diagnostic produit.

## 5. Non testable ici (à vérifier en jeu)

- Paquets de mort réels : le rejeu disponible (5 trames) ne contient aucune mort ; la logique est
  testée sur l'état du cœur A2Tools, pas sur une vraie capture.
- Wayland / Npcap / KDE : affichage des overlays par-dessus le jeu, capture PipeWire.
- Ordre et libellés réels des valeurs de la barre de monnaie ; lecture du menu de droite.
- Exactitude des associations monstres ↔ familiers « même espèce ».
- **Mise à jour du moteur** : les versions .10 à .12 n'ont produit aucune release GitHub (aucun
  tag) — sans release, le nouveau moteur (morts, observation sans DPS) n'arrive pas chez toi ;
  l'onglet Familiers l'indique (« moteur installé trop ancien »). Le code Python, lui, arrive par
  `main`.

## 6. Vérification chez toi (5 minutes, sans danger)

1. Relance l'appli (version 2026.10.12.13).
2. **Target** : section « Détection et préchargement des joueurs » → coche l'observation ; l'état
   passe à « Flux du jeu repéré » en jeu ; les compteurs montent quand tu croises du monde.
3. **DPS** : active le DPS meter aussi → l'état dit « Moteur partagé avec : observation » (un seul
   moteur) ; décoche l'un, l'autre continue.
4. **Farm** : renomme A / B / A − B d'après ton inventaire, démarre « Kinah / minute », achète un
   objet chez un PNJ : le gain net baisse (normal). Ferme et relance : « Interrompue » →
   « Reprendre ».
5. **Familiers** : cherche « Papis », mets tes âmes actuelles, coche « Suivre sur l'overlay »,
   active le comptage auto, tue quelques monstres : le journal dit pourquoi chaque mort est
   comptée ou non.
6. Après la session : Target → « Exporter le rapport de diagnostic » et envoie-le-moi.
