---
name: mon-standard-skill
description: >
  Standard personnel (complémentaire aux guides natifs de création de skills) pour
  concevoir, durcir, valider et distribuer une skill sur Windows entre Antigravity,
  Codex, Claude Code et Claude Desktop. Utiliser dès que l'utilisateur demande de
  créer, modifier, auditer, packager ou synchroniser une skill réutilisable.
---

# Standard Personnel de Création & Distribution de Skills (Windows Multi-Outils)

Ce standard complète les guides natifs de création de skills d'Antigravity et de Codex (fournis par ces outils, non inclus ici) sans les dupliquer. Applique-le automatiquement à toute nouvelle skill ou mise à jour de skill.

## 0. Prérequis
- Les contrôles du §3 utilisent `scripts/check-release.ps1` de la skill **`github-open-source-release`** : installe-la avec ce standard et note son dossier `<dossier-github-open-source-release>`. Une archive Claude Desktop de cette skill seule ne contient pas ce script.
- PowerShell : `pwsh` (PowerShell 7+) s'il est installé, sinon `powershell` (Windows PowerShell 5.1). Gitleaks dans le `PATH` ou pointé par `GITLEAKS_PATH`.
- Si l'un de ces outils manque, signale le contrôle comme **non exécuté** (statut « à corriger ») : ne le déclare jamais réussi.

## 1. Architecture, Sécurité (`Skill-Scanner` & DLP) & Rigueur éditoriale
1. **Sobriété & divulgation progressive :** garde `SKILL.md` court et actionnable (routage, règles non négociables, workflow). Déporte les matrices longues, checklists détaillées ou gabarits dans `references/` uniquement si le contenu dépasse ~80–120 lignes ou ne sert qu'à un mode précis.
2. **Zéro fichier parasite :** n'ajoute jamais de `README.md`, changelog ou guide doublon dans le dossier d'une skill.
3. **Frontmatter strict :** `name` en `^[a-z0-9-]+$` (≤ 64 car., identique au nom du dossier), `description` claire (≤ 1024 car., sans `<` ni `>`, ni mot réservé `claude`/`anthropic` dans `name`) précisant **quoi**, **quand déclencher** et les **limites**.
4. **Garde-fous d'exécution & Bouclier `Safety-Net` (`0 USD`) :**
   - Si la skill teste, audite ou pilote du code/agents, impose explicitement l'isolation en répertoire temporaire nettoyé en `finally`, l'interdiction d'appels LLM/API payants (`0 USD`) par défaut, et aucune écriture destructive ni `git push` sans accord explicite.
   - Interdis la lecture de secrets réels (consigne à doubler d'un verrou d'outil, par exemple un hook `PreToolUse`) (`.env*` hors `.env.example`, `*.pem`, `*.key`, `*vault*.sqlite`, `<dossier-données-clients>/`, `.ssh/`) et l'intégration de code externe non audité (`Zero Untrusted External Code`).
5. **Hygiène DLP & `Skill-Scanner` (Portabilité Open Source) :**
   - **Zéro chemin machine personnel :** n'écris jamais de chemin local privé en dur (profil utilisateur Windows/Unix spécifique ou chemin disque personnel) dans `SKILL.md`, `references/` ou `scripts/`. Utilise exclusivement des variables ou placeholders génériques (`%USERPROFILE%`, `$HOME`, `<projets-root>`, `<dossier-skill>`).
   - **Convention canari :** tout exemple ou test illustrant une donnée sensible (email, IBAN, clé, chemin bloqué) doit utiliser le marqueur `CANARY_TEST_` dans la valeur elle-même ou un domaine réservé (`@example.test`). Un IBAN, un NIR ou un numéro de téléphone ne pouvant pas porter le marqueur, construis-le à l'exécution (concaténation) au lieu de le stocker en clair.
   - **Zéro Unicode invisible / SAST :** aucun caractère Bidi ou invisible (*Trojan Source*) ni téléchargement exécuté à la volée dans les fichiers de la skill.
6. **Cartographie des processus obligatoire (`references/processus.md`) :**
   - Toute skill terminée doit embarquer un fichier `<dossier-skill>/references/processus.md` (`UTF-8` sans BOM, diagrammes `mermaid` *Déclenchement → Étapes & garde-fous → Sorties & contrôles*, sans chemin privé ni secret) référencé depuis `SKILL.md`.
   - **Lieu canonique unique :** ne laisse jamais une cartographie durable uniquement dans les artefacts temporaires de conversation d'un assistant. Le dossier canonique de la skill fait foi (`<dossier-skill>/references/processus.md`), ou pour un projet hors skill son dossier `docs/processus.md` / `board/processus.md`.
   - **Mise à jour obligatoire à chaque modification (tous modèles) :** dès qu'une skill existante est modifiée (ajout ou changement d'étape, de mode, de commande, de garde-fou ou de sortie), régénère ou mets à jour immédiatement `references/processus.md` avant validation et synchronisation afin d'éviter toute dérive avec `SKILL.md`.

## 2. Cycle de vie en 3 zones & Synchronisation Multi-Outils Windows
1. **Cycle de rangement en 3 zones (chantier → publication / catalogue personnel) :**
   - **Chantier :** toute création ou refonte démarre dans `<zone-chantier>/<nom>/`.
   - **Audit préalable des chemins (`0 USD`) :** avant tout déplacement, renommage ou promotion d'une skill entre zones, scanne les scripts, tests, jonctions et configurations pour mettre à jour les références sans rien casser.
   - **Cartographie puis Promotion :** en fin de chantier, juste après la validation fonctionnelle / tests (`100 %` PASS) et **avant** la promotion en 3 zones, le scan `check-release.ps1 -Fast` et le calcul des `SHA-256`, génère ou mets à jour `<dossier-skill>/references/processus.md`. Déploie ensuite la skill dans `<catalogue-personnel>/<nom>/` (catalogue maître) et, si elle est publique, dans `<zone-publication>/<nom>/` (ainsi que `Toolbox/skills/<nom>/`), puis nettoie le sous-dossier terminé de `<zone-chantier>/`.
2. **Encodage strict :** écris tous les fichiers en **`UTF-8` sans BOM** (vérifie l'absence de caractères corrompus après écriture sous Windows/PowerShell).
3. **Copies réelles synchronisées :** privilégie des copies réelles identiques (pas de symlinks Windows fragiles dans les dossiers globaux d'outils) entre les répertoires `skills/<nom>/` des assistants ciblés sous `%USERPROFILE%` :
   - **Antigravity :** dossier global de configuration Antigravity (`config/skills/<nom>/`)
   - **Claude Code :** dossier global Claude (`skills/<nom>/`)
   - **Codex :** dossier global Codex (`skills/<nom>/`, uniquement si la synchronisation Codex est demandée et autorisée par les règles de séance)
4. **Séparation stricte Codex vs Claude Desktop :**
   - Crée `agents/openai.yaml` (`interface.display_name`, `short_description` de `25–64` caractères, `default_prompt` citant `$<nom>`) **uniquement** dans la copie Codex (`skills/<nom>/agents/openai.yaml`).
   - **N'inclus jamais** `agents/openai.yaml` dans les copies Antigravity, Claude Code, `Toolbox/skills/` ni dans une archive Claude Desktop (risque de rejet d'import).
5. **Archive Claude Desktop (si export ou multi-outils demandé) :**
   - Génère l'archive `<nom>-claude-desktop.zip` via Python (`zipfile`) avec chemins **POSIX `/`**, contenant un **unique dossier racine `<nom>/`** (`SKILL.md` + `references/` / `scripts/` éventuels, sans `agents/`).

## 3. Validation déterministe obligatoire & Restitution non-dev
1. **Porte DLP / SAST / Secrets (`check-release.ps1 -Fast`) :** exécute `pwsh -NoProfile -File "<dossier-github-open-source-release>/scripts/check-release.ps1" -ArtifactPath "<dossier-skill>" -Fast` (ou `powershell` sous Windows PowerShell 5.1) sur chaque skill modifiée (et sur le `.zip` Claude Desktop généré) — doit retourner `"status": "passed"` (aucune forme connue de chemin privé, de PII, d'Unicode invisible ou de secret).
2. **Validation de structure :** si Codex est dans le périmètre, exécute le validateur de skills fourni par Codex (`quick_validate.py`, non inclus ici ; attendu : `Skill is valid!`). Vérifie systématiquement la présence et la parité **SHA-256** de tous les fichiers communs (`SKILL.md`, `references/` dont `references/processus.md`, `scripts/`) entre les zones (catalogue personnel, zone de publication et dossiers globaux des assistants), et vérifie la structure POSIX du `.zip` généré.
3. **Restitution claire en français :** termine en expliquant simplement à quoi sert la skill, comment la déclencher, les chemins synchronisés avec leurs empreintes SHA-256, et l'import `.zip` éventuel dans Claude Desktop.
4. **Relais documentation / espace de suivi externe (si applicable) :** si l'assistant actif dispose d'un accès direct à l'espace de suivi documentaire de l'utilisateur (inventaire des skills, pages de processus), il met à jour directement les pages concernées ; sinon, il consigne une tâche datée dans le fichier de suivi du QG/projet (ex. `TASKS.md`) listant les skills et fichiers `references/processus.md` modifiés afin que l'assistant disposant de cet accès prenne le relais à la reprise.

## 4. Références
- Cartographie des processus du standard : `references/processus.md`.
