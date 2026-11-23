# Toolbox — Agent Skills & Developer Tools

Catalogue open source de **skills** et d'outils réutilisables pour assistants de développement IA (**Claude Code**, **OpenAI Codex**, **Google Antigravity** et **Claude Desktop**).

## Skills disponibles

| Skill | Dossier | Description |
| :--- | :--- | :--- |
| **`github-open-source-release`** | [`skills/github-open-source-release/`](./skills/github-open-source-release/) | Prépare, vérifie et publie un dépôt ou une archive open source sur GitHub (cadrage licence/visibilité, constitution d'un candidat isolé, porte de sécurité déterministe PowerShell + Gitleaks sur les fichiers, archives `.zip` imbriquées et historique Git complet, publication après accord explicite). |

---

## Installation rapide

Chaque skill située dans `skills/<nom-skill>/` suit le format standard (`SKILL.md` avec frontmatter YAML + dossier `scripts/` éventuel).

### 1. Claude Code / Google Antigravity / OpenAI Codex
Copiez le dossier de la skill souhaitée dans votre répertoire de skills (global ou local au projet) :

- **Claude Code :** `~/.claude/skills/<nom-skill>/`
- **Google Antigravity :** `~/.gemini/config/skills/<nom-skill>/` (ou `.agents/skills/<nom-skill>/` dans un projet)
- **OpenAI Codex :** `~/.codex/skills/<nom-skill>/`

```powershell
# Exemple sous Windows PowerShell pour github-open-source-release
Copy-Item -Path ".\skills\github-open-source-release" -Destination "$HOME\.claude\skills\github-open-source-release" -Recurse -Force
```

### 2. Claude Desktop (Import `.zip`)
Compressez le dossier `skills/<nom-skill>` en une archive `.zip` contenant le dossier racine `<nom-skill>/` avec des chemins POSIX (`/`), puis importez-la dans les paramètres de Claude Desktop.

---

## Prérequis pour `github-open-source-release`

Le script de vérification de sécurité [`skills/github-open-source-release/scripts/check-release.ps1`](./skills/github-open-source-release/scripts/check-release.ps1) utilise :
- **Windows PowerShell 5.1** ou **PowerShell 7+ (`pwsh`)**
- **[Git](https://git-scm.com/)** (pour l'inspection des fichiers suivis et de l'historique complet via `-GitRoot`)
- **[Gitleaks](https://github.com/gitleaks/gitleaks)** disponible dans le `PATH` (ou via la variable d'environnement `GITLEAKS_PATH`)
- **[GitHub CLI (`gh`)](https://cli.github.com/)** (pour la vérification du compte et la publication après validation)

Exemple d'exécution locale du contrôle de livraison avant publication :

```powershell
powershell -NoProfile -File ./skills/github-open-source-release/scripts/check-release.ps1 `
  -ArtifactPath "C:\chemin\vers\candidat-ou-archive.zip" `
  -GitRoot "C:\chemin\vers\depot-candidat"
```

Le script retourne un résumé JSON strict (`status: "passed"` avec l'empreinte `sha256` et `gitHistory: "checked"`, ou `status: "blocked"` avec l'étape et le motif de blocage, sans jamais exposer la valeur d'un secret détecté dans la console).

---

## Contribuer & Signaler un problème

Les retours, signalements de bugs et propositions d'améliorations se font via les **Issues** et **Pull Requests** du dépôt GitHub : [https://github.com/Melkyzedec/Toolbox](https://github.com/Melkyzedec/Toolbox).

## Licence

Distribué sous licence **[MIT](./LICENSE)** — Copyright (c) 2026 Melkyzedec.
