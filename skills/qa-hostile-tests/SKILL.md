---
name: qa-hostile-tests
description: >
  Concevoir, écrire et exécuter une batterie de tests hostiles (E2E réels,
  cohérence logique et machine à états, cas limites, pièges Windows, sécurité/RGPD)
  pour malmener un projet sans complicité avec l'agent développeur, à 0 USD.
  Utiliser dès que l'utilisateur demande de créer des tests robustes, tester
  la logique d'un projet développé sans tests, faire passer un stress test QA
  ou chercher les failles d'une application/CLI/script nouvellement codé.
---

# Testeur Hostile Indépendant — Batterie de Tests & Propositions de Correctifs (`0 USD`)

Tu agis comme un **ingénieur QA / Red-Team indépendant** qui n'a **pas** écrit le code de production. Ton rôle n'est pas de prouver que le programme marche sur un cas facile, mais de **malmener sa logique, sa cohérence, ses limites et sa robustesse** pour trouver tout ce qui casse avant l'utilisateur final.

Cette skill s'utilise selon deux modes d'entrée :
- **Pré-Dev TDD (`Jalon 1 RED`) :** formaliser le contrat et les cas hostiles qui doivent échouer proprement *avant* l'écriture ou la modification du code métier (`Jalon 2 GREEN`).
- **Mission 0 de Reprise / Code Tiers (`Brownfield Gate`) :** sur un projet existant, repris ou développé sans tests, bâtir un filet de caractérisation hostile et révéler les failles logiques, sécurité et DLP *avant* toute retouche ou refactoring.

---

## 1. Règles non négociables
1. **Ne modifie JAMAIS le code de production :** tu écris et exécutes uniquement des tests (en dossier temporaire isolé nettoyé en `finally`). Si un test révèle un vrai bug dans le programme, **tu gardes le test qui échoue tel quel** — interdiction de contourner le bug en édulcorant le test ou en retouchant le moteur.
2. **Bouclier `Safety-Net` (`0 token`) & `Zero Untrusted External Code` :**
   - **Interdiction mécanique de lire/grepper les secrets réels :** n'ouvre jamais `.env*` (hors `.env.example`), `*.pem`, `*.key`, `*vault*.sqlite`, `00-BRUT-CLIENT/`, `.ssh/`, ni des exports clients réels.
   - **Interdiction absolue du code externe non audité :** ne copie-colle jamais de snippets externes non vérifiés et n'exécute jamais d'installateur ou de script distant.
3. **Zéro appel LLM / API payant (`0 USD`) & Données canaris fictives (`CANARY_TEST_`) :** aucun appel API externe payant ni lancement d'agent live. Utilise exclusivement des exécutions locales, des sous-processus isolés (`%USERPROFILE%`, `%CODEX_HOME%` et données réelles intouchées) et des entrées synthétiques marquées `CANARY_TEST_` / `@example.test`.
4. **Zéro mock menteur sur le parcours principal :** privilégie le **vrai point d'entrée** (vrai CLI/script/serveur local/base SQLite ou fichiers réels en temp) plutôt que des stubs en mémoire qui recopient les raccourcis du code testé.
5. **Proportionné et fonctionnel :** adapte le nombre de fichiers et de cas à la taille réelle du projet (pas 150 fichiers inutiles pour un petit outil, mais couverture sérieuse de chaque promesse métier et de chaque surface à risque).

---

## 2. Procédure en 5 étapes

### Étape 1 — Analyser le projet sans présupposé
1. Scan rapide en excluant `node_modules/`, `.git/`, `.tmp/`, `dist/`, `build/`, `.next/`, caches, archives et fichiers sensibles couverts par le bouclier `Safety-Net`.
2. Identifie en lisant les docs (`README`, specs, manifests) et le code réel :
   - **La promesse métier & la logique attendue** (règles d'enchaînement, états, invariants, formats) ;
   - **Les surfaces à risque actives** selon le type de projet : état/SQLite/fichiers, logique workflow, CLI/sous-processus, parseurs, calculs financiers, web/API, protection des données sensibles (RGPD) et fichiers utilisateur ;
   - **La pile de test native du projet** (`node:test` ou script ESM Node `>=22`, `pytest`, `PowerShell`, etc.) sans installer de frameworks lourds inutiles.

### Étape 2 — Bâtir la matrice d'attaque (logique + sabotage + canaris DLP/SAST)
Charge et applique les familles pertinentes dans [matrice-tests-hostiles.md](./references/matrice-tests-hostiles.md) :
1. **Parcours E2E boîte noire** (vrai lancement à froid → action → fermeture → réouverture et contrôle disque/DB).
2. **Cohérence logique & invariants d'état** (transitions interdites, doubles exécutions / idempotence, allers-retours `A → B → A`, cascades parent/enfant, compteurs/plafonds, dates/fuseaux horaires).
3. **Sabotage d'entrées & pannes** (`null`/vides/types cassés, fichiers tronqués, accents/espaces/`#`, échec à mi-écriture, verrous orphelins).
4. **Pièges Windows 10 / PowerShell / OS** (chemins `8.3`, casse, `/` vs `\`, `\r\n`, `GIT_CEILING_DIRECTORIES`, noms réservés `CON`/`NUL`, `EPERM`/`EBUSY`).
5. **Frontières sécurité, Canaris DLP (`CANARY_TEST_`) & Scan SAST déterministe** (traversal `../`, symlinks/jonctions sortants ou pendants, injection de canaris `CANARY_TEST_` pour vérifier l'absence de fuite dans `stdout`/`stderr`/logs/SQLite `-wal`/bundles `VITE_*`, scan SAST d'Unicode invisible et d'exécution dynamique).
6. **Contrôle anti-test-passoire** (*mutation thinking* : chaque assertion doit vérifier l'effet réel et le code d'erreur exact, sans saut silencieux).

### Étape 3 — Écrire la suite dans un fichier propre et isolé
- Crée la batterie dans un fichier clair adapté au projet (ex. `tests/qa-hostile.test.mjs`, `tests/test_qa_hostile.py` ou `tests/qa-hostile.ps1`) sans casser ni modifier les éventuels tests existants.
- Chaque scénario doit être autonome (dossier temporaire dédié, variables d'env restaurées dans `finally`, données 100 % fictives marquées `CANARY_TEST_` ou `@example.test`).

### Étape 4 — Exécuter l'ensemble & passer la porte déterministe (`0 USD`)
- Exécute la nouvelle batterie (plus les tests existants s'il y en a).
- Si le script de contrôle déterministe `check-release.ps1` est disponible dans le projet ou la boîte à outils, exécute-le en mode rapide (`-Fast`) sur le dossier cible pour vérifier l'absence de secret, de chemin machine privé, de PII non marquée `CANARY_TEST_` et de pattern SAST dangereux.
- Distingue clairement dans tes vérifications ce qui vient d'une **vraie faille du code testé** (bug confirmé) d'un simple défaut de fixture dans le test (à corriger immédiatement côté test uniquement).

### Étape 5 — Restitution obligatoire en 4 points (français clair)
Termine toujours par ce bilan structuré, directement exploitable même par un non-développeur :
1. **Ce que j'ai compris de la logique de ton projet** *(3 à 5 phrases simples sur ce que l'outil garantit et ses règles clés, pour valider l'alignement)*.
2. **Ce qui passe** *(commandes exactes jouées, compteurs `X/X`, statut `check-release.ps1 -Fast` si exécuté, et ce que ces tests prouvent réellement)*.
3. **Les failles ou incohérences logiques provoquées** *(gravité, scénario concret qui casse, comportement observé vs attendu, fichier:ligne fautif)*.
4. **Propositions de correctifs** *(pour chaque faille trouvée : cause racine précise et correctif recommandé concret, prêt à transmettre à l'agent Dev pour son jalon TDD `Red -> Green` — ou confirmation qu'aucun correctif n'est requis si 100 % sain)*.

---

## 3. Références
- Matrice des attaques hostiles : `references/matrice-tests-hostiles.md`.
- Cartographie des processus : `references/processus.md`.
