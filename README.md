# Toolbox — Agent Skills & Developer Tools

Catalogue open source de **skills** et d'outils réutilisables pour assistants de développement IA (**Claude Code**, **OpenAI Codex**, **Google Antigravity** et **Claude Desktop**).

## Skills disponibles

| Skill | Dossier | Description |
| :--- | :--- | :--- |
| **`github-open-source-release`** | [`skills/github-open-source-release/`](./skills/github-open-source-release/) | Prépare, vérifie et publie un dépôt ou une archive open source sur GitHub (cadrage licence/visibilité, constitution d'un candidat isolé, porte déterministe 4 étages PowerShell + DLP/SAST/Gitleaks en mode rapide `-Fast` ou complet avec `-GitRoot`, publication après accord explicite). |
| **`qa-hostile-tests`** | [`skills/qa-hostile-tests/`](./skills/qa-hostile-tests/) | Conçoit et exécute une batterie de tests hostiles boîte noire à `0 USD` (Pré-Dev TDD `Red -> Green` ou Mission 0 de reprise `Brownfield Gate`, cohérence d'état, sabotage d'entrées, pièges Windows, canaris DLP `CANARY_TEST_` et scan SAST). |
| **`test-benchmark-audit`** | [`skills/test-benchmark-audit/`](./skills/test-benchmark-audit/) | Audite de manière hostile les suites de tests, oracles, garde-fous sécurité/RGPD/DLP/SAST et benchmarks A/B LLM (Mode 1 Audit & `Brownfield Gate`, Mode 2 Correction par tranches TDD `Red -> Green`, Mode 3 Réception globale et porte `check-release.ps1`). |
| **`mon-standard-skill`** | [`skills/mon-standard-skill/`](./skills/mon-standard-skill/) | Standard d'architecture, de sécurité (`Skill-Scanner`, DLP, `Safety-Net`), de cycle de vie en 3 zones et de synchronisation multi-outils Windows (`UTF-8` sans BOM, SHA-256 vérifiés et archives `.zip` POSIX pour Claude Desktop). |
| **`kanban-tasks`** | [`skills/kanban-tasks/`](./skills/kanban-tasks/) | Gère un tableau Kanban Markdown local (1 fichier `.md` par carte, compatible Obsidian `KANBAN.md`), vues interactives Kanban/Horizons/Frise, dépendances, pièces jointes, réglages de libellés et serveur web local `127.0.0.1` (`serve --open`) sans dépendance externe (Python 3 stdlib, 17 tests automatisés). |

---

## Installation rapide

Chaque skill située dans `skills/<nom-skill>/` suit le format standard (`SKILL.md` avec frontmatter YAML + dossiers `references/` ou `scripts/` éventuels).

### Tout installer en une commande

- **Google Antigravity (plugin natif via `.antigravity-plugin/plugin.json`) :**

```bash
agy plugin install https://github.com/Melkyzedec/Toolbox
```

- **Claude Code (plugin via `.claude-plugin/marketplace.json` et `.claude-plugin/plugin.json`) :**

```bash
# En une commande depuis le terminal (Claude Code v2.1.292+)
claude plugin install toolbox --marketplace Melkyzedec/Toolbox
```

```text
# Ou en session interactive Claude Code
/plugin marketplace add Melkyzedec/Toolbox
/plugin install toolbox@melkyzedec-toolbox
```

- **Copie directe des 5 skills dans votre projet (`.agents/skills/` pour Antigravity & OpenAI Codex) ou en global Claude Code (`~/.claude/skills/`) :**

```powershell
# Windows PowerShell — installer les 5 skills dans le projet courant (.agents/skills/)
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("toolbox-" + [Guid]::NewGuid().ToString("N")); git clone --depth 1 https://github.com/Melkyzedec/Toolbox.git $tmp; New-Item -ItemType Directory -Path ".agents/skills" -Force | Out-Null; Copy-Item -Path "$tmp/skills/*" -Destination ".agents/skills" -Recurse -Force; Remove-Item -Path $tmp -Recurse -Force
```

```bash
# macOS / Linux (Bash) — installer les 5 skills dans le projet courant (.agents/skills/)
tmp="$(mktemp -d)" && git clone --depth 1 https://github.com/Melkyzedec/Toolbox.git "$tmp" && mkdir -p .agents/skills && cp -R "$tmp/skills/." .agents/skills/ && rm -rf "$tmp"
```

> Pour installer globalement dans **Claude Code** au lieu du projet courant, remplacez `.agents/skills` par `$HOME\.claude\skills` (PowerShell) ou `~/.claude/skills` (Bash). Pour une installation globale dans Antigravity ou OpenAI Codex, remplacez `.agents/skills` par `<dossier-config-antigravity>/skills` ou `<dossier-config-codex>/skills`.

### Prompt prêt à copier pour demander l'installation à un assistant

Copiez-collez ce prompt dans **Antigravity**, **Claude Code** ou **OpenAI Codex** :

```text
Installe les 5 skills open source du dépôt https://github.com/Melkyzedec/Toolbox (`github-open-source-release`, `kanban-tasks`, `mon-standard-skill`, `qa-hostile-tests`, `test-benchmark-audit`) en choisissant la méthode native adaptée à ton environnement :
1. Si tu es Google Antigravity : vérifie/installe le plugin via `agy plugin install https://github.com/Melkyzedec/Toolbox` (ou copie `skills/*` dans `.agents/skills/` si le projet est local).
2. Si tu es Claude Code : installe le plugin via `claude plugin install toolbox --marketplace Melkyzedec/Toolbox` (ou copie `skills/*` dans `~/.claude/skills/`).
3. Si tu es OpenAI Codex (ou pour un dépôt partagé) : clone en `--depth 1` dans un dossier temporaire nettoyé à la fin et copie `skills/*` dans `.agents/skills/` (ou le dossier skills configuré de l'outil).
Règles strictes : n'exécute aucun script téléchargé à la volée, n'écris pas de chemin machine personnel en dur, puis vérifie que les 5 `SKILL.md` sont présents et lance `python -B ./scripts/test_packaging.py -v` (si le dépôt local est conservé) pour confirmer que tout passe à 0 USD.
```

### Claude Desktop (Import `.zip`)

1. Téléchargez les archives individuelles `<nom-skill>-claude-desktop.zip` et `SHA256SUMS.txt` depuis la section **[Releases](https://github.com/Melkyzedec/Toolbox/releases)** du dépôt (ou générez-les localement avec `python -B ./scripts/package-skills.py --out ./dist/v1.0.0`).
2. Vérifiez l'empreinte SHA-256 avant import (`Get-FileHash <archive>.zip -Algorithm SHA256` sous PowerShell ou `sha256sum -c SHA256SUMS.txt`).
3. Dans **Claude Desktop**, activez *Code execution and file creation* (**Settings > Capabilities**), ouvrez **Customize > Skills > Add skill > Upload a skill** et importez chaque archive `.zip`.

### Premier essai rapide (`kanban-tasks`)
Aucune tâche préenregistrée ni donnée de démo n'est incluse dans le dépôt : vous démarrez avec un tableau vierge.

```powershell
python -B ./skills/kanban-tasks/scripts/kanban.py --dir ./mon-kanban init --title "Mon tableau"
python -B ./skills/kanban-tasks/scripts/kanban.py --dir ./mon-kanban serve --open
```

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
