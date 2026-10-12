# Aion Target — licence et crédits

Aion Target est distribué sous **GNU General Public License v3.0 ou ultérieure** (fichier `LICENSE`).
Code source complet : https://github.com/MeriadecG/AION-2-TARGET

## Composants tiers

| Composant | Utilisation | Licence | Provenance |
|---|---|---|---|
| **A2Tools DPS Meter** (taengu et contributeurs) | cœur d'analyse du moteur DPS (`engine/`, dépendance épinglée au commit `71868460c0ee`) ; capture et aiguillage adaptés de `pcap_capturer.rs`, `capture_dispatcher.rs`, `combat_port_detector.rs` ; données `engine/data/` (compétences et PNJ en français, serveurs, DoT) | GPL-3.0 | https://github.com/taengu/A2Tools-DPS-Meter |
| Cinzel (police) | interface | SIL OFL 1.1 (`aion_target/assets/Cinzel-OFL.txt`) | https://github.com/NDISCOVER/Cinzel |
| PyQt6 / Qt 6 | interface | GPL-3.0 / LGPL-3.0 | Riverbank Computing, The Qt Company |
| RapidOCR, ONNX Runtime | lecture du nom de la cible | Apache-2.0 / MIT | RapidAI, Microsoft |
| httpx, mss, numpy, Pillow, pynput, tzdata, cryptography | divers | BSD / MIT / Apache-2.0 / LGPL-3.0 (pynput) | PyPI |
| Python (python-build-standalone) | version Windows | PSF | Astral |

Sons intégrés : créés par synthèse (`tools/make_sounds.py`), aucune œuvre tierce.
Données de joueurs : shugo.gg et recherche officielle NCSoft, consultées en ligne, non redistribuées.

Npcap (Windows) n'est **pas** inclus : sa licence gratuite interdit de l'embarquer ; l'utilisateur
l'installe lui-même depuis https://npcap.com.
