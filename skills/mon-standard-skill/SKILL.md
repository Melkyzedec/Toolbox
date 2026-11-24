---
name: mon-standard-skill
description: >
  Standard personnel (complémentaire à agy-customizations et $skill-creator) pour
  concevoir, durcir, valider et distribuer une skill sur Windows entre Antigravity,
  Codex, Claude Code et Claude Desktop. Utiliser dès que l'utilisateur demande de
  créer, modifier, auditer, packager ou synchroniser une skill réutilisable.
---

# Standard Personnel de Création & Distribution de Skills (Windows Multi-Outils)

Ce standard complète les guides natifs d'Antigravity (`agy-customizations`) et de Codex (`$skill-creator`) sans les dupliquer. Applique-le automatiquement à toute nouvelle skill ou mise à jour de skill.

## 1. Architecture, Sécurité (`Skill-Scanner` & DLP) & Rigueur éditoriale
1. **Sobriété & divulgation progressive :** garde `SKILL.md` court et actionnable (routage, règles non négociables, workflow). Déporte les matrices longues, checklists détaillées ou gabarits dans `references/` uniquement si le contenu dépasse ~80–120 lignes ou ne sert qu'à un mode précis.
2. **Zéro fichier parasite :** n'ajoute jamais de `README.md`, changelog ou guide doublon dans le dossier d'une skill.
3. **Frontmatter strict :** `name` en `^[a-z0-9-]+$` (≤ 64 car., identique au nom du dossier), `description` claire (≤ 1024 car., sans `<` ni `>`, ni mot réservé `claude`/`anthropic` dans `name`) précisant **quoi**, **quand déclencher** et les **limites**.
4. **Garde-fous d'exécution & Bouclier `Safety-Net` (`0 USD`) :**
   - Si la skill teste, audite ou pilote du code/agents, impose explicitement l'isolation en répertoire temporaire nettoyé en `finally`, l'interdiction d'appels LLM/API payants (`0 USD`) par défaut, et aucune écriture destructive ni `git push` sans accord explicite.
   - Interdis mécaniquement la lecture de secrets réels (`.env*` hors `.env.example`, `*.pem`, `*.key`, `*vault*.sqlite`, `00-BRUT-CLIENT/`, `.ssh/`) et l'intégration de code externe non audité (`Zero Untrusted External Code`).
5. **Hygiène DLP & `Skill-Scanner` (Portabilité Open Source) :**
   - **Zéro chemin machine personnel :** n'écris jamais de chemin local privé en dur (profil utilisateur Windows/Unix spécifique ou chemin disque personnel) dans `SKILL.md`, `references/` ou `scripts/`. Utilise exclusivement des variables ou placeholders génériques (`%USERPROFILE%`, `$HOME`, `<projets-root>`, `<dossier-skill>`).
   - **Convention canari :** tout exemple ou test illustrant une donnée sensible (email, IBAN, clé, chemin bloqué) doit utiliser le marqueur `CANARY_TEST_` ou un domaine réservé (`@example.test`).
   - **Zéro Unicode invisible / SAST :** aucun caractère Bidi ou invisible (*Trojan Source*) ni téléchargement exécuté à la volée dans les fichiers de la skill.

## 2. Cycle de vie en 3 zones & Synchronisation Multi-Outils Windows
1. **Cycle de rangement en 3 zones (`skills en developpement` → `skills publiées` / `skills perso`) :**
   - **Chantier :** toute création ou refonte démarre dans `<projets-root>/skills en developpement/<nom>/`.
   - **Audit préalable des chemins (`0 USD`) :** avant tout déplacement, renommage ou promotion d'une skill entre zones, scanne les scripts, tests, jonctions et configurations pour mettre à jour les références sans rien casser.
   - **Promotion & Nettoyage :** une fois la skill validée, déploie-la dans `<projets-root>/skills perso/skills/<nom>/` (catalogue maître personnel) et, si elle est publique, dans `<projets-root>/skills publiées/<nom>/` (ainsi que le dépôt candidat `Toolbox/skills/<nom>/`), puis nettoie le sous-dossier terminé de `skills en developpement/`.
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
1. **Porte DLP / SAST / Secrets (`check-release.ps1 -Fast`) :** exécute `powershell -NoProfile -File "<dossier-github-open-source-release>/scripts/check-release.ps1" -ArtifactPath "<dossier-skill>" -Fast` sur chaque skill modifiée (et sur le `.zip` Claude Desktop généré) — doit retourner `"status": "passed"` (zéro chemin privé, zéro PII, zéro Unicode invisible, zéro secret).
2. **Validation de structure :** si Codex est dans le périmètre, exécute `quick_validate.py` (`Skill is valid!`). Vérifie systématiquement que les fichiers communs (`SKILL.md`, `references/`, `scripts/`) ont exactement le même **SHA-256** entre les zones (`skills perso`, `skills publiées` et les dossiers globaux des assistants), et vérifie la structure POSIX du `.zip` généré.
3. **Restitution claire en français :** termine en expliquant simplement à quoi sert la skill, comment la déclencher, les chemins synchronisés avec leurs empreintes SHA-256, et l'import `.zip` éventuel dans Claude Desktop.
