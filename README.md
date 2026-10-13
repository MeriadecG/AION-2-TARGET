# Aion Target

Overlay pour Aion 2 : lit le nom de ta cible dans une petite zone de l'écran (OCR),
cherche le personnage sur shugo.gg et affiche son **gear score** et son **combat power**
juste sous la zone. Rien n'est injecté dans le jeu : l'outil ne fait que lire des pixels.

## Télécharger

- **Windows** : [AionTarget-Setup.exe](https://github.com/MeriadecG/AION-2-TARGET/releases/latest/download/AionTarget-Setup.exe)
  (dernière version ; double-clic → Suivant → Installer). Si Windows affiche « Windows a protégé
  votre ordinateur » : *Informations complémentaires* → *Exécuter quand même* (programme non signé).
  Version portable : [AionTarget-Windows.zip](https://github.com/MeriadecG/AION-2-TARGET/releases/latest/download/AionTarget-Windows.zip).
- **Linux (Fedora / Nobara)** : voir ci-dessous (`./install-linux.sh`).

Une fois installée, l'appli se met à jour toute seule à chaque lancement.
Les installateurs Windows sont construits automatiquement par GitHub (onglet *Actions*) à
chaque nouvelle version et publiés dans *Releases*.

## Installation

### Linux (Fedora / Nobara) : installateur

```bash
cd ~/Téléchargements && unzip -o aion-target.zip && cd aion-target
./install-linux.sh --run
```

Le script (sans sudo : il le demande lui-même pour les paquets système) installe PyGObject +
GStreamer PipeWire (capture Wayland) et pipewire-utils (sons), copie l'appli dans
`~/.local/share/aion-target`, crée son environnement Python, la commande `aion-target` et le
raccourci du menu (catégorie Jeux) + bureau, puis vérifie que tout s'importe.
**Mise à jour** : dézipper la nouvelle version et relancer `./install-linux.sh`.
**Désinstaller** : `~/.local/share/aion-target/app/uninstall-linux.sh` (`--purge` pour effacer
aussi réglages et mémoire des joueurs). Réglages : `~/.config/aion-target`, jamais touchés.

Sous Wayland, une appli ne peut pas lire l'écran directement (image noire). L'outil passe
par le **portail de partage d'écran de KDE** (comme OBS ou Discord). Au premier lancement,
KDE demande quel écran partager : choisis **l'écran où tourne le jeu**. Le choix est
mémorisé. Une icône « partage d'écran » reste dans la barre système tant que l'outil tourne.

### Développement (depuis le dossier source)

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m aion_target
```

## Windows : installateur et version portable

```bash
sudo dnf install mingw32-nsis      # une fois : fournit makensis (installateur Windows)
python tools/build_windows.py      # ~1 min, télécharge ~250 Mo la 1re fois
```

- `dist/AionTarget-Setup.exe` (~110 Mo) : **installateur** à envoyer. Double-clic, Suivant,
  Installer. Sans droits administrateur (dossier `%LOCALAPPDATA%\Programs\Aion Target`),
  raccourcis menu Démarrer + bureau, désinstallation par Paramètres > Applications.
  Relancer un Setup plus récent = mise à jour (réglages conservés).
- `dist/AionTarget-Windows.zip` (~150 Mo) : version **portable** (dézipper, double-cliquer).

Windows peut afficher « Windows a protégé votre ordinateur » (programme non signé) :
« Informations complémentaires » puis « Exécuter quand même ».

## Utilisation

Lance **Aion Target** depuis le menu (catégorie Jeux). La fenêtre de contrôle permet de :

- **Placer sur une capture (3 s)** : reviens en jeu et cible quelqu'un pendant le décompte.
  Sur la capture, place les **4 cadres** (glisser = déplacer, coin = taille) :
  - **bleu : lecture du nom**, autour de la ligne `45 Nom` du cadre de cible ;
  - **GS**, **CP**, **classe · niveau · serveur**, **TOP** : où afficher chaque info.
    Le cadre TOP affiche le rang européen par CP (`TOP - 23`), seulement dans le top 1 000
    (rang issu de l'import shugo.gg ; option « Afficher le top »).
  La taille du texte suit la hauteur de chaque cadre. Entrée = enregistrer.
- **Ajuster en direct** : les mêmes cadres, déplaçables directement sur l'écran.
- **Mon personnage** : ton nom → pastille **rouge** (cible plus forte que toi) ou **verte**
  (plus faible) à côté du CP.
- **Ouvrir la fiche de la cible** : la page shugo.gg complète (équipement).
- **Apparence** : couleurs GS / CP / détails indépendantes, police (Cinzel embarquée,
  style Aion, ou toute police installée), ligne de détails.

PNJ, monstres et noms introuvables : **rien n'est affiché**.

Cadres de cible reconnus : monde normal (`45 Nom`) et **Abysse** (`Nom [Isr]`, sans niveau ;
la balise donne directement le serveur). La légion et le titre / rang ne sont jamais pris
pour le nom.

## Vitesse

Détails, mesures et procédure de test en Abysse : `docs/PERF_TARGET.md`.

- **Joueur déjà en mémoire : aucune attente réseau.** Les scores sont servis par un **index en
  RAM** (nom + serveur), jamais par une requête ni par SQLite ; un score ancien s'affiche tout
  de suite avec sa fraîcheur discrète (« il y a 3 j ») puis il est rafraîchi en silence, sans
  effacer la valeur affichée. Cadres repeints immédiatement (pas de mise en file).
- Image du cadre inchangée : revérifiée toutes les 40 ms (sans OCR, quelques µs) ; dès qu'elle
  change, lecture aussitôt (cadence OCR normale : 100 ms).
- **Du plus rapide au plus lent** : (1) mémoire locale — score de moins de 10 min affiché sans
  aucune requête ; (2) **base shugo.gg** (recherche par nom dans l'API classement, ~0,1-0,3 s,
  sans relecture NCSoft) affichée dès réception ; (3) **fiche shugo.gg** (relue chez NCSoft,
  1-5 s) seulement si le score a plus de 10 min, sur le serveur exact, mise à jour discrète.
- **Dictionnaire des vrais noms** (toute la mémoire, ~21 000 joueurs) : chaque lecture est
  corrigée — niveau collé (`45Corgoth`, `4SCorgoth`, `454llin` → `4llin`), une lettre mal
  lue / en trop / en moins (`Corgotn` → `Corgoth`, seulement si un seul nom correspond).
  Un nom reconnu est validé et affiché immédiatement.
- Lecture rapide qui donne un nom inconnu → **relecture complète de la même image** (sépare
  le niveau du nom), au lieu d'attendre que l'image change (elle peut rester figée longtemps).
- OCR limité à 2 threads sans attente active (moins de concurrence avec le jeu).
- Joueur déjà connu : validé dès la 1re lecture OCR (pas de seconde lecture de contrôle).
- Mesuré (fiche simulée à 2,5 s) : **0,38 s** pour un nouveau joueur, **0,16 s** pour un
  joueur en mémoire. Le journal détaille le temps de chaque étape.
- OCR réglé pour le cadre de cible ; une image identique à la précédente n'est pas relue.
- Recherche lancée **dès la 1re lecture** du nom, avant même sa confirmation.
- En parallèle : shugo.gg sur les 3 serveurs les plus fréquents + **API officielle NCSoft**,
  qui donne le serveur exact (les serveurs inconnus sont appris automatiquement).
- HTTP/2 : une connexion par site, ouverte dès le lancement.
- **Import du classement shugo.gg** (toute la région : ≈ 21 000 joueurs EU, ≈ 3 Mo) en
  arrière-plan au lancement, puis au plus toutes les 24 h ; une page de 50 joueurs toutes
  les 0,5 s (≈ 4 min), reprise si interrompu, arrêt poli en cas de refus du site. Les scores
  importés ne remplacent jamais un score plus récent vu en jeu. Réglable : tout, top 5 000,
  top 1 000 ou désactivé. Les serveurs découverts (ex. Azphel) sont ajoutés automatiquement.
  Bouton **« Télécharger maintenant »** avec barre de progression ; en cas d'échec, la cause
  est affichée (refus 403, trop de requêtes, réseau…) et un nouvel essai a lieu toutes les 10 min.
- **Mémoire des joueurs** (`players.db`) : **aucune limite de durée**, seulement une taille
  maximale réglable dans la fenêtre (25 Mo à 5 Go, 100 Mo par défaut ; ~150 octets par
  joueur, toute l'Europe tient dans ~15 Mo). Pleine, elle oublie les joueurs mis à jour il y
  a le plus longtemps. Chaque joueur garde l'heure de sa dernière mise à jour : le score
  connu s'affiche immédiatement, puis il est **toujours rafraîchi** (score du jour).

## Homonymes (même nom sur plusieurs serveurs)

Un personnage est identifié par **nom + serveur**. Si le serveur de la cible est inconnu et
que le nom existe sur plusieurs serveurs, **aucun score n'est affiché** (« Homonymes (N
serveurs) : score non attribué ») plutôt qu'un score peut-être faux. En Abysse, la balise
`[Isr]` donne le serveur : le bon personnage est affiché. La mémoire existante est migrée
automatiquement (copie de sécurité `players.db.bak-v1`).

Plan de la version 2.0 (DPS meter, ping, historiques) : `docs/PLAN_2.0.md`.

## Plein écran fenêtré / sans bordure

- Les cadres sont des fenêtres de type « infobulle » sous Linux : KDE les affiche au-dessus
  même d'un jeu plein écran actif.
- En changeant de mode d'affichage, l'interface du jeu bouge : refais
  « Placer sur une capture » dans le mode où tu joues.
- Fenêtre de contrôle → **Diagnostic** : « Test affichage » (exemple 5 s dans tes cadres),
  « Test lecture » (3 s pour cibler un joueur, puis ce qui a été lu + image capturée),
  **« Mesurer le retard de capture »** (un carré clignote dans le coin du cadre bleu : temps
  avant qu'il apparaisse dans la capture, indépendamment du jeu, de l'OCR et du réseau) et
  **« Copier le rapport de vitesse »** (mesures détaillées, à coller tel quel).

## Fenêtre de contrôle : onglets

Interface « Tactical Analytics » : **mode clair ou sombre** (bouton « ◐ » en haut, ou Target →
Apparence → Thème : Clair / Sombre / Comme le système, mémorisé), thème froid (palette et feuille de style centralisées
dans `aion_target/theme.py`, composants dans `aion_target/ui_kit.py`), tuiles de statistiques en
chiffres tabulaires (valeurs réelles uniquement, « — » sinon), puces d'état des modules dans
l'en-tête (Lecture, Moteur, Farm, Familiers). Les overlays affichés par-dessus le jeu ne sont pas
concernés : apparence, position, transparence et comportement inchangés.

- **Target** : GS / CP de la cible (cadres, mémoire, **détection et préchargement des joueurs**,
  apparence, diagnostic + **rapport de diagnostic exportable**).
- **Notifications** : calendrier des événements (ci-dessous).
- **Familiers** : catalogue, progression âmes / morts, overlay, comptage auto (ci-dessous).
- **Farm** : monnaie calculée, **Kinah / minute** (ci-dessous).

Un **seul moteur réseau** sert le DPS meter, l'observation de Target et le comptage des
familiers : il tourne dès qu'un de ces modules est activé et s'arrête quand plus aucun ne
l'utilise (jamais deux captures). Avertissement obligatoire à la première activation.
Détails, preuves et limites : `docs/MODULES.md`.
- **DPS** : DPS meter, ping, historiques des combats et des rencontres (ci-dessous).

## DPS meter, ping et historiques (onglet DPS)

Module **optionnel, désactivé par défaut** (avertissement à l'activation). Un moteur séparé
(`engine/`, Rust, cœur d'analyse d'**A2Tools DPS Meter**, GPL-3.0) lit une **copie** du trafic
réseau du jeu : aucune injection, aucune écriture, aucune modification du client, aucun
déchiffrement. Éteint : aucun processus, aucune capture.

- DPS par joueur (classe, dégâts, part), cible suivie (boss, dernière cible, toutes…),
  remise à zéro, **overlay DPS** déplaçable (lignes, opacité) au-dessus du jeu.
- **Ping** : aller-retour mesuré PC ↔ serveur ; le temps de traitement du serveur n'est pas
  observable et n'est pas inclus.
- **Historique des combats** de boss (durée, total, ping moyen, détail par joueur et par
  compétence), recherche, suppression, limite réglable.
- **Historique des rencontres** : nom + serveur, nombre de rencontres, dernier GS/CP et âge de
  la donnée, source (OCR, groupe, réseau), fiabilité (exacte / nom unique / homonymes),
  favoris. Les homonymes ne reçoivent jamais de score.
- **GS/CP plus rapides** : le jeu envoie le GS/CP des membres du groupe (repris tel quel). Chaque
  personnage **observé** dans le flux est d'abord cherché **en mémoire locale** (immédiat, sans
  limite) et devient prioritaire pour la lecture OCR de son nom ; seuls les vrais manques avec
  un serveur connu partent sur shugo.gg (20 requêtes/min au plus, jamais pendant la recherche de
  la cible, pause selon `Retry-After` ou 5 min croissantes si le site refuse). Sans serveur :
  identité partielle gardée, aucune requête. Ce n'est **pas** la liste complète de l'instance,
  et la cible reste identifiée par l'OCR (le jeu n'envoie pas « qui est ciblé »).
- **Observation réseau légère** (onglet **Target**, section « Détection et préchargement des
  joueurs ») : même capture passive, **sans** DPS meter, seulement pour ce préchargement ; état
  du moteur et compteurs (observés, avec serveur, trouvés en mémoire, en attente). Indisponible
  (pas de Npcap / d'autorisation) : l'OCR fonctionne comme avant.
- Windows : installer **Npcap** (npcap.com, gratuit, non redistribuable). Linux : autorisation
  de capture `cap_net_raw` (demandée par l'installateur, ou bouton dans l'onglet DPS).
- Moteur mis à jour automatiquement **uniquement** via un manifeste signé Ed25519 (clé privée
  dans le secret GitHub `ENGINE_SIGNING_KEY`, clé publique dans `engine_update.py`).
- Rejeu reproductible : `engine/target/release/aion-engine --replay capture.txt --mode lastHitByMe`
  (`--record fichier.txt` enregistre le flux du jeu pour un test).

Compilation du moteur : `cd engine && cargo build --release` (la CI le fait pour Windows et Linux).

## Licence

GPL-3.0-or-later (`LICENSE`), crédits et composants tiers dans `NOTICE.md`.

## Monnaie calculée (onglet Farm)

La barre en haut à gauche du jeu affiche `1 300 | 807 572 | 707 572 | 22 212` ; la différence
entre la 2ᵉ et la 3ᵉ valeur (ici 100 000) est une monnaie visible seulement dans les menus.
Coche « Afficher en permanence », puis « Placer les cadres » : cadre **bleu** autour des
montants, second cadre où afficher le résultat (nom et couleur au choix, positions des valeurs
réglables : « valeur 2 − valeur 3 »). Lecture toutes les 2 s dans un thread à part (OCR
uniquement si l'image change, ~0,2 s), valeur affichée après deux lectures identiques ; si la
barre est cachée (menu), la dernière valeur reste affichée, même après redémarrage.

- **Noms** des valeurs A, B et A − B au choix (à vérifier dans l'inventaire : si A est le total
  et B le Kinah lié, A − B est le non lié). A, B et A − B viennent **toujours de la même
  capture**.
- **2ᵉ emplacement** (menu en haut à droite, cadre orange) : lu seulement quand la barre est
  illisible. Zones **mémorisées par résolution / échelle** d'affichage.
- **Affichage** : A, B, A − B et/ou Kinah/minute dans le cadre à l'écran ; « (ancien) » si la
  dernière mesure valide date de plus de 20 s.
- **Kinah / minute** : Démarrer, Pause / Reprendre, Réinitialiser. Gain **net observé** du solde
  suivi (achats, ventes, échanges compris : peut être négatif — ce n'est pas un calcul de
  butin), durée active (pauses exclues), Kinah/minute et /heure. Deux lectures identiques avant
  de retenir un solde. Session sauvegardée ; après un redémarrage elle revient « interrompue »
  et ne reprend qu'au clic sur « Reprendre ».
- Le flux réseau décodé ne contient **aucun solde** de monnaie : lecture à l'écran uniquement.

## Familiers (onglet Familiers)

- **Catalogue** de 206 familiers du client global (genre, nom anglais officiel, nom français
  quand un PNJ du même nom existe, monstres associés), recherche en français ou en anglais,
  sources et date affichées. Ajout manuel pour les nouveautés du jeu.
- Par familier : **âmes** (toujours manuelles) et **monstres tués** (manuel ou automatique),
  ±1, saisie directe d'une progression déjà entamée, remise à zéro avec confirmation ;
  sauvegarde immédiate (`pets.json`).
- **Paliers** : client global 5 / 30 / 105 âmes cumulées (Nv 1 / 2 / 3), Corée / Taïwan, ou
  personnalisés ; le compteur peut dépasser l'objectif.
- **Overlay** compact des familiers suivis (plusieurs à la fois), déplaçable, opacité réglable.
- **Comptage automatique** (facultatif, moteur partagé) : +1 seulement pour une mort **en
  combat** (drapeau de mort du jeu) d'un monstre de l'espèce que **tu** as frappé, une seule fois
  par monstre ; une disparition, des dégâts seuls ou un monstre tué par d'autres ne comptent
  pas. **Journal** des décisions (comptée / ignorée / incertaine + raison) consultable après la
  session. Jamais « 1 mort = 1 âme ».

## Mise à jour automatique

À chaque lancement, l'appli compare sa version à celle du dépôt GitHub
`MeriadecG/AION-2-TARGET` (branche `main`, 3 s max). Si elle est plus récente :
téléchargement (~1 Mo), vérification (version, compilation de tout le code), nouvelles
bibliothèques installées si besoin, puis remplacement du dossier `aion_target` et démarrage
direct sur la nouvelle version. Hors ligne : lancement normal. Jamais dans un dossier git
(développement). Désactivable dans l'onglet Target (« Mise à jour automatique »).
Publier une version = augmenter `__version__` dans `aion_target/__init__.py` et pousser sur `main`.

## Événements et notifications

Onglet **Notifications** de la fenêtre de contrôle (le nombre d'alertes actives est affiché).

- Liste par catégorie (horaires, quotidiens, hebdomadaires, mensuels), une case par entrée,
  **prochain déclenchement en heure de Paris** et compte à rebours, recherche et filtre.
- Statut de chaque horaire : **confirmé** (≥ 2 sources concordantes), **à vérifier** (1 seule
  source), **Horaire à confirmer** (non documenté : affiché sans case, aucune alerte).
  Survol d'une ligne : règle, fuseau d'origine, durée, sources et date de vérification.
- Notification système **1 minute avant** chaque événement coché (KDE : D-Bus freedesktop ;
  Windows : notification de l'icône de la barre système).
- **Son** facultatif (désactivé par défaut) : 6 sons intégrés (carillon, cloche, cristal,
  gong, appel, alerte), les sons KDE / système, ou ton propre fichier (« Choisir un
  fichier… » : WAV, OGG, FLAC ; WAV seulement sous Windows), volume réglable, bouton
  « ▶ Écouter ». Un seul son même si plusieurs alertes tombent à la même minute.
- Resets (quotidien / hebdomadaire, 07:00 UTC = 09:00 l'été, 08:00 l'hiver à Paris) : alerte
  1 min avant, plus des **rappels 1 h / 12 h / 24 h** regroupés en une seule notification qui
  liste les éléments cochés (« pense à utiliser si besoin… » : l'outil ne lit pas tes compteurs).
- Fonctionne jeu fermé, tant qu'Aion Target tourne. Un seul minuteur, réveillé sur la
  prochaine alerte (au plus toutes les 60 s) : pas de boucle, pas de réseau.
- Pas de doublon : chaque alerte est mémorisée (`events_state.json`) ; une seule instance
  envoie les notifications (`events.lock`) même si l'appli est lancée deux fois.
- Après une veille ou un PC éteint, les alertes passées sont **ignorées** (journal), jamais
  envoyées en rafale. Changements d'heure : calculs en instants UTC via `zoneinfo`.
- Préférences : `events.json` (enregistrées à chaque clic).
- **Corriger / ajouter un horaire** sans toucher au code : créer `events_catalog.json` dans le
  dossier de configuration (bouton « Calendrier… ») avec les mêmes champs que
  `aion_target/assets/events_catalog.json` — les entrées sont fusionnées par `id` — puis
  « Recharger ». Exemple :

  ```json
  {"events": [{"id": "arena_tactics", "status": "confirmed",
               "rule": {"type": "daily", "times": ["20:00"]}, "sources": ["mon_test"]}],
   "blackouts": [{"start": "2026-10-21T09:00+02:00", "end": "2026-10-21T13:00+02:00",
                  "label": "maintenance"}]}
  ```

  Règles : `hourly` (`minute`), `every_hours` (`interval`, `start`), `daily` (`times`),
  `weekly` (`days`, `time`), `monthly` (`day`, `time`), `unknown`. Fuseau `tz` : `server`
  (heure du serveur de la région) ou un nom IANA (`UTC`, `Europe/Paris`…). `blackouts` :
  maintenances annoncées, aucune alerte pendant ces créneaux.

## Diagnostic

```bash
python -m aion_target --test-lookup Corgoth   # shugo.gg seul
python -m aion_target --test-ocr              # capture la zone → capture.png + texte lu
python -m aion_target --select-zone           # sélecteur de zone en ligne de commande
python -m aion_target --test-import           # 1 page du classement shugo.gg : réponse exacte
```

Lancé depuis le menu, le journal est dans `~/.config/aion-target/aion-target.log`
(Windows : `%APPDATA%\aion-target\`). Config : `config.json` dans le même dossier.
Méthode de capture forçable : `"capture_backend": "mss"` ou `"portal"`.

## Crédits

Police Cinzel © The Cinzel Project Authors, SIL Open Font License 1.1
(`aion_target/assets/Cinzel-OFL.txt`). Données : shugo.gg.
