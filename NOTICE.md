# Aion Target — licence et crédits

Aion Target est distribué sous **GNU General Public License v3.0 ou ultérieure** (fichier `LICENSE`).
Code source complet : https://github.com/MeriadecG/AION-2-TARGET

## Composants tiers

| Composant | Utilisation | Licence | Provenance |
|---|---|---|---|
| **A2Tools DPS Meter** (taengu et contributeurs) | cœur d'analyse du moteur DPS (`engine/`, dépendance épinglée au commit `71868460c0ee`) ; capture et aiguillage adaptés de `pcap_capturer.rs`, `capture_dispatcher.rs`, `combat_port_detector.rs` ; données `engine/data/` (compétences et PNJ en français, serveurs, DoT) et la liste `aion_target/assets/npc_names.txt` qui en est tirée | GPL-3.0 | https://github.com/taengu/A2Tools-DPS-Meter |
| Cinzel (police) | interface | SIL OFL 1.1 (`aion_target/assets/Cinzel-OFL.txt`) | https://github.com/NDISCOVER/Cinzel |
| Inter (police) | interface | SIL OFL 1.1 (`aion_target/assets/Inter-OFL.txt`) | https://github.com/rsms/inter |
| JetBrains Mono (police) | chiffres de l'interface | SIL OFL 1.1 (`aion_target/assets/JetBrainsMono-OFL.txt`) | https://github.com/JetBrains/JetBrainsMono |
| PyQt6 / Qt 6 | interface | GPL-3.0 / LGPL-3.0 | Riverbank Computing, The Qt Company |
| RapidOCR, ONNX Runtime | lecture du nom de la cible | Apache-2.0 / MIT | RapidAI, Microsoft |
| httpx, mss, numpy, Pillow, pynput, tzdata, cryptography | divers | BSD / MIT / Apache-2.0 / LGPL-3.0 (pynput) | PyPI |
| Python (python-build-standalone) | version Windows | PSF | Astral |

Catalogue des familiers (`aion_target/assets/familiars.json`, généré par `tools/make_familiars.py`) :
liste des familiers et paliers du client global relevés sur https://www.aion2guidance.com/en/pets/
(12/10/2026, faits de jeu) ; noms français et identifiants de PNJ tirés des tables `i18n/npcs/en.json`
et `fr.json` d'A2Tools DPS Meter (GPL-3.0, commit `71868460c0ee`).

Sons intégrés : créés par synthèse (`tools/make_sounds.py`), aucune œuvre tierce.
Données de joueurs : shugo.gg et recherche officielle NCSoft, consultées en ligne, non redistribuées.

Npcap (Windows) n'est **pas** inclus : sa licence gratuite interdit de l'embarquer ; l'utilisateur
l'installe lui-même depuis https://npcap.com.
