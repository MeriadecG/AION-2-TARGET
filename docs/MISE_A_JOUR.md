# Mise à jour transactionnelle (depuis 2026.10.12.19)

## Objectif

Après n'importe quel incident (coupure réseau, archive abîmée, fichier verrouillé par Windows,
plantage, coupure de courant), l'appli doit retrouver **une version complète et cohérente** :
jamais un mélange de fichiers de deux versions.

## Dossier de l'appli

```
app/                         (Windows : %LOCALAPPDATA%\Programs\Aion Target\app ; Linux : ~/.local/share/aion-target/app)
  aion_target/               version en service
  aion_target.prev/          version précédente qui fonctionnait (gardée jusqu'à la mise à jour suivante)
  aion_target.bad/           version retirée après un échec (supprimée à la mise à jour suivante)
  .update-staged/            version préparée, vérifiée, en attente de bascule
  update-state.json          état : ready → switching → trial → ok | rolled_back
  .update.lock               un seul processus de mise à jour à la fois (PID ; repris s'il est mort)
```

## États affichés

| État | Sens |
|---|---|
| Aucune mise à jour (`up_to_date`) | version publiée ≤ version installée |
| Téléchargement / Vérification | préparation en cours (thread à part, l'interface reste fluide) |
| Prête à installer (`staged`) | copie vérifiée prête ; bouton « Redémarrer » |
| Redémarrage nécessaire (`pending`) | bascule refusée par Windows (fichier utilisé) ou autre instance ouverte |
| Mise à jour installée (`updated` / `trial`) | nouvelle version à l'essai |
| Mise à jour réussie (`confirmed`) | 15 s de fonctionnement ou fermeture normale |
| Échec et restauration (`rolled_back`) | retour automatique à `aion_target.prev` |
| `error`, `offline`, `needs_setup`, `incompatible`, `busy` | rien n'a été modifié |

## Vérifications avant bascule

1. Version annoncée = version de l'archive.
2. Extraction contrôlée (`_extract_package`) : refus des chemins absolus, `..`, `\`, liens
   symboliques, noms réservés ou invalides sous Windows (`CON`, `aux.json`, `:`…), collisions de
   casse, archive de plus de 150 Mo décompressés ou de 5 000 fichiers, taux de compression
   suspect, CRC faux, fichier tronqué, plusieurs dossiers racine. Vérification par chemins
   résolus (`is_relative_to`), jamais par préfixe de texte. Seuls `aion_target/` et les
   fichiers annexes sont extraits.
3. Manifeste signé (si publié pour cette version) : signature Ed25519 vérifiée avec la clé
   publique du moteur (`engine_update.PUBLIC_KEY_B64`), archive du **commit exact**
   (`codeload…/zip/<sha>`, immuable), taille + SHA-256 de chaque fichier ; fichier manquant,
   en trop ou différent → refus. Dès qu'une installation a reçu une version signée, elle exige
   la signature pour toutes les suivantes.
4. Types de fichiers autorisés (`.py .json .png .ico .ttf .txt .wav .md`), compilation de tout le code.
5. Moteur DPS installé : le protocole (`engine_client.PROTOCOL`) doit être identique, sinon
   « incompatible » (nouveau Setup nécessaire).
6. Dépendances : installation pip des seules dépendances manquantes ; échec → « réinstallation
   nécessaire ».
7. Chargement de **tous** les modules dans un processus séparé, avec le Python de l'appli.
8. Copie dans `.update-staged/`, empreintes enregistrées ; revérifiées juste avant la bascule.

## Bascule et retour arrière

- Au démarrage, avant tout chargement (`__main__` → `updater.startup_update`), quand plus aucune
  instance ne tourne (la relance attend la fin de l'ancienne instance, `AION_TARGET_WAIT_PID`).
- Deux renommages de dossiers. Premier refusé → rien n'a changé. Second refusé → retour
  immédiat. Coupure entre les deux → réparé au lancement suivant (`recover`).
- Nouvelle version qui ne se charge pas → retour dans le même processus, l'appli démarre sur la
  version précédente. Deux lancements sans confirmation → retour au lancement suivant.
- Windows (nouveaux Setup) : le lanceur remet `aion_target.prev` si le paquet ne peut même pas
  être importé.

## Publication signée (CI)

Le job « Publication » du workflow `Version` exécute `tools/sign_app.py dist/app` puis joint
`app-manifest.json` et `app-manifest.sig` à la release `v<version>`. Il utilise le secret
existant **`ENGINE_SIGNING_KEY`** (aucun nouveau secret). Sans ce secret, rien n'est signé et
les installations continuent avec la vérification non signée (tant qu'elles n'ont jamais vu de
version signée).

Remarque : les releases échouent depuis 2026.10.12.10 ; tant qu'elles ne sont pas réparées,
aucune version signée n'est publiée et les mises à jour passent par la vérification non
signée (étapes 1, 2, 4 à 8).

## Limites connues

- Les installations antérieures à 2026.10.12.19 utilisent encore leur ancien mécanisme pour
  passer à la .19 (code déjà installé chez elles). Les protections s'appliquent aux mises à
  jour suivantes.
- Le filet du lanceur Windows n'existe que dans les Setup construits à partir de la .19.
- Linux : si le dossier `aion_target` disparaissait entre les deux renommages (coupure de
  courant à cet instant précis), `python -m aion_target` ne trouverait plus le paquet ;
  renommer `aion_target.prev` en `aion_target` répare.
