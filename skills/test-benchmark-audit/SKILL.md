---
name: test-benchmark-audit
description: >
  Auditer de manière hostile les suites de tests, recettes, oracles, garde-fous
  sécurité/RGPD et benchmarks A/B LLM d'un projet (rapport .md obligatoire dans
  le dossier concerné), appliquer sur demande les correctifs par tranches
  validées (Mode 2), puis exécuter la contre-vérification globale et le re-test
  complet post-correctifs à 0 USD (Mode 3). Utiliser dès que l'utilisateur
  demande d'auditer/déconstruire des tests ou benchmarks, de suivre un rapport
  Markdown pour corriger, ou d'évaluer/retester l'ensemble après corrections.
---

# Audit Hostile, Correction Guidée & Réception Globale (`0 USD`)

Cette skill fonctionne en **trois phases distinctes** : **Mode 1 (Audit & Brownfield Gate)** par défaut, **Mode 2 (Correction par tranches TDD)** sur demande explicite, et **Mode 3 (Contre-vérification & Re-test global)** pour prouver que les correctifs tiennent réellement ensemble.

---

## Mode 1 — Audit Hostile & Reprise `Brownfield Gate` (`par défaut`, Lecture Seule)

Tu agis comme un **auditeur externe sceptique** (fiabilité logicielle, *mutation testing*, sécurité/RGPD/DLP/SAST, méthodologie LLM). Ce mode sert aussi de **Mission 0 (`Brownfield Gate`)** lors de la reprise d'un projet existant ou d'un code tiers avant toute modification.

### 1. Règles non négociables (Mode 1)
1. **Zéro modification du code, des tests ou des docs existants :** n'édite aucun fichier déjà présent dans le projet et ne passe jamais automatiquement en phase de correction sans instruction explicite.
2. **Bouclier `Safety-Net` (`0 token`) & `Zero Untrusted External Code` :**
   - **Interdiction mécanique de lire/grepper les secrets réels :** n'ouvre jamais `.env*` (hors `.env.example`), `*.pem`, `*.key`, `*vault*.sqlite`, `00-BRUT-CLIENT/`, `.ssh/`, ni des archives contenant des données clients réelles.
   - **Interdiction absolue du code externe non audité :** n'exécute jamais de script distant et n'introduis aucun snippet externe non vérifié.
3. **Zéro appel LLM / API payant (`0 USD`) & exécution isolée :** ne lance jamais de benchmark live (`--live`, `--run`, agent payant). Contrôles locaux gratuits uniquement, **impérativement** en répertoire temporaire jetable nettoyé en `finally` (zéro pollution des données réelles, `%USERPROFILE%` ou `%CODEX_HOME%`).
4. **Preuves vérifiées uniquement :** chaque constat cite `fichier:ligne`, l'extrait exact lu dans la session courante (ou le calcul depuis `.json` brut) et un scénario concret de défaillance. Interdiction d'extrapoler un fichier non lu ou tronqué.
5. **Sortie obligatoire en `.md` DANS le dossier concerné :**
   - Enregistre l'audit complet dans un **nouveau `.md` à l'intérieur du dossier audité** : `<dossier-concerné>/docs/AUDIT-TESTS-BENCHMARKS.md` si `docs/` existe, sinon `<dossier-concerné>/AUDIT-TESTS-BENCHMARKS.md` (ou chemin demandé dans ce dossier). Ne jamais écraser un rapport antérieur (ajoute `-AAAA-MM-JJ` puis `-HHMM` si besoin).
   - Dans le chat, renvoie uniquement le chemin exact du `.md` créé + un court résumé exécutif.

### 2. Procédure d'Audit (4 étapes)
- **Étape A — Cartographie & Hygiène DLP/SAST (`0 USD`) :** scanne le projet en excluant dès le départ `node_modules/`, `.git/`, `.tmp/`, `dist/`, `build/`, `.next/`, `out/`, caches binaires, archives volumineuses et fichiers protégés par `Safety-Net`. Repère tests (`tests/`, `*recette*`, `*.test.*`, `*.spec.*`), benchmarks/oracles (`evals/`, `*bench*`, `scenarios*`), rapports/JSON (`SYNTHESE*`, `RAPPORT*`, `README*`) et code testé. Vérifie immédiatement que les fixtures, snapshots JSON et rapports n'embarquent aucune vraie PII, secret ou chemin machine personnel (exiger la convention `CANARY_TEST_` / `@example.test`). Si le volume dépasse ~`1 800` lignes, scinde en **2 passes** (Passe 1 : tests/isolation/mocks/DLP/SAST ; Passe 2 : benchmarks/oracles/FinOps/docs vs JSON).
- **Étape B — Grille d'attaque :** charge et applique les **7 axes durcis** dans [grille-audit-hostile.md](./references/grille-audit-hostile.md) (incluant la vérification de la trace TDD `Red -> Green`, des canaris `CANARY_TEST_` et du scan SAST / `Skill-Scanner`).
- **Étape C — Priorisation honnête :** classe chaque faille (`Critique`, `Haute`, `Moyenne`, `Basse`) selon son impact réel.
- **Étape D — Écriture & contrôle :** écris le `.md` dans `<dossier-concerné>` selon le gabarit de [grille-audit-hostile.md](./references/grille-audit-hostile.md) et vérifie sur disque qu'il existe et est non vide.

---

## Mode 2 — Phase de Correction & Mise à Jour (`sur demande explicite uniquement`)

À déclencher **seulement** quand l'utilisateur demande d'utiliser/suivre le fichier `.md` d'audit pour corriger et mettre à jour le dépôt.

### Règles strictes d'exécution par tranches
1. **Découpage obligatoire avec pause (`STOP & validation`) :** dès qu'il y a **plus de 3 failles** ou la moindre faille `Critique` / `Haute`, travaille **strictement une seule tranche par tour** et **attends la validation ou la relance de l'utilisateur** avant la suivante :
   - **Tranche 1 :** uniquement les failles **`Critique`** → cycle TDD `Red -> Green` → tests → bilan → **STOP**.
   - **Tranche 2 :** uniquement les failles **`Haute`** (après feu vert) → cycle TDD `Red -> Green` → tests → bilan → **STOP**.
   - **Tranche 3 :** **`Moyenne` / `Basse`** + contrôle global final (après feu vert).
   *(S'il n'y a que ≤ 3 petites failles `Moyenne/Basse` localisées, traite-les en 1 seule tranche propre.)*
2. **Vérifier avant d'éditer :** relis le code réel ciblé avant toute modification. Si une faille du `.md` est un faux positif ou déjà couverte, n'y touche pas et signale-le. Si une règle locale de séance (type `AGENTS.md`) bloque la lecture d'un fichier moteur à réparer, demande d'abord la levée explicite sur ce fichier (sans jamais contourner de règle sécurité/RGPD ni le bouclier `Safety-Net`).
3. **Cycle TDD obligatoire (`Red -> Green`) & canaris `CANARY_TEST_` :** interdiction d'assouplir une assertion, sauter un cas ou sur-mocker pour « faire passer au vert ». Pour toute faille touchant le moteur (sécurité, DLP, SAST, calcul, rollback, parsing), commence par ajouter/durcir le test hostile (avec données fictives `CANARY_TEST_` / `@example.test`), vérifie qu'il échoue bien sur le bug actuel (`Jalon 1 RED`), puis corrige le code et vérifie qu'il passe (`Jalon 2 GREEN`).
4. **Tests complets après la dernière modif (`0 USD`) & isolation :** à la fin de la tranche, réexécute **toutes** les suites locales concernées (`0 USD`, y compris vrai CLI en sous-processus et scripts PowerShell sous environnement temporaire nettoyé en `finally`). Ne lance aucun benchmark LLM payant ni `git commit` / `git push` sans accord explicite.
5. **Mise à jour du suivi & compte rendu d'arrêt :** mets à jour en fin de fichier `.md` une section `## 5. Suivi des corrections` (statut réel par ID : `Corrigé`, `Faux positif`, `À faire`), indique dans le chat les fichiers modifiés, les sorties exactes observées **après la dernière modification**, puis **arrête-toi et demande validation avant de passer à la tranche suivante** (en fin de dernière tranche, enchaîne ou propose immédiatement le **Mode 3**).

---

## Mode 3 — Réception & Contre-Vérification Globale Post-Correctifs (`0 USD`)

À exécuter **en clôture de la dernière tranche du Mode 2**, ou **indépendamment dans une nouvelle session** (y compris avec un autre modèle pour contrôle croisé) quand l'utilisateur demande d'évaluer et retester l'ensemble après les corrections :

1. **Revue croisée des correctifs (`code réel` vs `.md`) :** inspecte chaque faille `Critique` / `Haute` du rapport et sa mise en œuvre réelle (y compris `git diff` si Git est présent). Signale immédiatement toute faille oubliée, partiellement traitée, ou contournée par mock factice / assertion affaiblie / `skip`.
2. **Contre-test *mutation* (`Red -> Green`) :** pour chaque correctif `Critique` / `Haute`, vérifie (par lecture ciblée ou test d'échec en environnement temporaire) que le nouveau test échoue réellement si la faille ou la régression réapparaît.
3. **Re-test global & Porte déterministe DLP/SAST/Secrets (`0 USD`, isolé) :** après la toute dernière modification (code, tests, scripts, manifests/sync), lance **100 % des suites locales du dépôt** (JS, PowerShell, Python, CLI réel en sous-processus, `--dry-run`, vérification de sync/manifeste), jamais une sélection filtrée sur la seule dernière tranche. Exécute en complément le scanner déterministe `check-release.ps1` (`-Fast` en recette courante ou complet avec `-GitRoot` avant publication) pour garantir `"status": "passed"` (zéro secret, zéro PII/chemin privé hors `CANARY_TEST_`, zéro Unicode invisible ou pattern SAST).
4. **Procès-verbal de réception (`## 6. Clôture & Contre-vérification globale` dans le `.md`) :** consigne la date, les commandes exactes exécutées après la dernière modif, les compteurs bruts (`X/X PASS`), le résultat JSON de `check-release.ps1`, l'état Git/sync, la liste des réserves éventuelles (ex. tests live LLM payants non rejoués à `0 USD`), et un verdict explicite **`Validé`** ou **`Bloquants restants`**.
