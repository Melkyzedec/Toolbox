# Toolbox — Agent Skills & Developer Tools

Catalogue open source de **skills** et d'outils réutilisables pour assistants de développement IA (**Claude Code**, **OpenAI Codex**, **Google Antigravity** et **Claude Desktop**).

## Skills disponibles

| Skill | Dossier | Description |
| :--- | :--- | :--- |
| **`github-open-source-release`** | [`skills/github-open-source-release/`](./skills/github-open-source-release/) | Prépare, vérifie et publie un dépôt ou une archive open source sur GitHub (cadrage licence/visibilité, constitution d'un candidat isolé, porte déterministe 4 étages PowerShell + DLP/SAST/Gitleaks en mode rapide `-Fast` ou complet avec `-GitRoot`, publication après accord explicite). |
| **`qa-hostile-tests`** | [`skills/qa-hostile-tests/`](./skills/qa-hostile-tests/) | Conçoit et exécute une batterie de tests hostiles boîte noire à `0 USD` (Pré-Dev TDD `Red -> Green` ou Mission 0 de reprise `Brownfield Gate`, cohérence d'état, sabotage d'entrées, pièges Windows, canaris DLP `CANARY_TEST_` et scan SAST). |
| **`test-benchmark-audit`** | [`skills/test-benchmark-audit/`](./skills/test-benchmark-audit/) | Audite de manière hostile les suites de tests, oracles, garde-fous sécurité/RGPD/DLP/SAST et benchmarks A/B LLM (Mode 1 Audit & `Brownfield Gate`, Mode 2 Correction par tranches TDD `Red -> Green`, Mode 3 Réception globale et porte `check-release.ps1`). |
| **`mon-standard-skill`** | [`skills/mon-standard-skill/`](./skills/mon-standard-skill/) | Standard d'architecture, de sécurité (`Skill-Scanner`, DLP, `Safety-Net`), de cycle de vie en 3 zones et de synchronisation multi-outils Windows (`UTF-8` sans BOM, SHA-256 vérifiés et archives `.zip` POSIX pour Claude Desktop). |

---

## Installation rapide

Chaque skill située dans `skills/<nom-skill>/` suit le format standard (`SKILL.md` avec frontmatter YAML + dossiers `references/` ou `scripts/` éventuels).

### 1. Claude Code / Google Antigravity / OpenAI Codex
Copiez le dossier de la skill souhaitée dans votre répertoire de skills (global ou local au projet) :

- **Claude Code :** `~/.claude/skills/<nom-skill>/`
- **Google Antigravity :** `<dossier-config-antigravity>/skills/<nom-skill>/` (ou `.agents/skills/<nom-skill>/` dans un projet)
- **OpenAI Codex :** `<dossier-config-codex>/skills/<nom-skill>/`

```powershell
# Exemple sous Windows PowerShell pour github-open-source-release
Copy-Item -Path ".\skills\github-open-source-release" -Destination "$HOME\.claude\skills\github-open-source-release" -Recurse -Force
```

### 2. Claude Desktop (Import `.zip`)
Compressez le dossier `skills/<nom-skill>` en une archive `.zip` contenant le dossier racine `<nom-skill>/` avec des chemins POSIX (`/`), puis importez-la dans les paramètres de Claude Desktop.

---

## Porte de sécurité déterministe (`check-release.ps1`)

Le script de vérification [`skills/github-open-source-release/scripts/check-release.ps1`](./skills/github-open-source-release/scripts/check-release.ps1) inspecte quatre étages avant toute livraison ou publication :
1. **`artifact-layout` :** structure des fichiers et archives `.zip` (imbriquées jusqu'à profondeur 3), blocage des symlinks/jonctions sortants, traversées `../`, doublons et fichiers privés (`.env`, `.sqlite`, `.pem`, `.key`).
2. **`artifact-dlp` :** blocage déterministe des chemins machine privés Windows/Unix et des PII (IBAN, NIR, e-mails personnels), avec support de la convention `CANARY_TEST_` / `@example.test` pour les fixtures de test.
3. **`artifact-sast` (`Skill-Scanner`) :** détection des caractères Unicode invisibles ou Bidi (*Trojan Source*) et des patterns d'exécution distante ou obfusquée.
4. **`artifact-secrets` & `git-secrets` :** détection Gitleaks sur l'arbre, les archives et l'historique Git complet (`--all --full-history`).

### Prérequis
- **Windows PowerShell 5.1** ou **PowerShell 7+ (`pwsh`)**
- **[Git](https://git-scm.com/)** (pour l'inspection des fichiers suivis et de l'historique complet via `-GitRoot`)
- **[Gitleaks](https://github.com/gitleaks/gitleaks)** disponible dans le `PATH` (ou via la variable d'environnement `GITLEAKS_PATH`)
- **[GitHub CLI (`gh`)](https://cli.github.com/)** (pour la vérification du compte et la publication après validation)

### Exemples d'exécution locale

```powershell
# 1. Mode rapide (-Fast, < 1s) en boucle TDD, fin de mission ou hook Git pre-commit
powershell -NoProfile -File ./skills/github-open-source-release/scripts/check-release.ps1 `
  -ArtifactPath "C:\chemin\vers\dossier-ou-archive.zip" `
  -Fast

# 2. Mode complet (avec -GitRoot) avant publication publique
powershell -NoProfile -File ./skills/github-open-source-release/scripts/check-release.ps1 `
  -ArtifactPath "C:\chemin\vers\candidat-ou-archive.zip" `
  -GitRoot "C:\chemin\vers\depot-candidat"
```

Le script retourne un résumé JSON strict (`status: "passed"` avec `mode`, `sha256` et `gitHistory`, ou `status: "blocked"` avec `stage` et `reason`, sans jamais exposer la valeur d'un secret ou d'une PII détectée dans la console).

---

## Contribuer & Signaler un problème

Les retours, signalements de bugs et propositions d'améliorations se font via les **Issues** et **Pull Requests** du dépôt GitHub : [https://github.com/Melkyzedec/Toolbox](https://github.com/Melkyzedec/Toolbox).

## Licence

Distribué sous licence **[MIT](./LICENSE)** — Copyright (c) 2026 Melkyzedec.
