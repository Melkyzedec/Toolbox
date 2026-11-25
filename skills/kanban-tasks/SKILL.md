---
name: kanban-tasks
description: >
  Gère un tableau Kanban Markdown local (1 fichier .md par carte) avec vue KANBAN.md
  compatible Obsidian, export HTML autonome interactif (Kanban, Horizons, Frise),
  dépendances, métriques de flux et rituels courts. Utiliser dès que l'utilisateur
  veut créer, consulter, trier, planifier ou piloter des tâches dans un tableau Kanban
  local.
---

# Kanban Tasks — Tableau Markdown & Planification Souple

Pilote un tableau Kanban local autonome dans `./kanban/` (ou `--dir <dossier-tableau>`).
Exécute le moteur Python sans bytecode : `python3 -B "<dossier-skill>/scripts/kanban.py" ...` (macOS/Linux) ou `python -B "<dossier-skill>/scripts/kanban.py" ...` (Windows).

## 1. Garde-fous d'exécution & Windows PowerShell
- **Safety-Net :** ne lis jamais de secrets (`.env*` hors `.env.example`, `*.pem`, `*.key`, `*vault*.sqlite`, `.ssh/`) et n'écris rien hors du `<dossier-tableau>` ciblé.
- **Zéro destruction tacite :** demande confirmation avant tout `init --force` ou suppression manuelle de cartes.
- **PowerShell & UTF-8 :** pour plusieurs opérations d'un coup ou des textes accentués, utilise **`batch --stdin`** (`@' ... '@ | python -B "<dossier-skill>/scripts/kanban.py" batch --stdin`).
- **Règle d'or Agent vs Humain :** un agent IA qui réalise une tâche la prend avec `claim --start`, coche ses sous-tâches, puis la dépose en **`en_revue`** (`--as-agent`) avec son résumé. **Seul l'humain valide le passage en `termine`.**
- **Protection `KANBAN.md` (Obsidian / édition manuelle) :** si `render` signale une modification manuelle de `KANBAN.md`, lance `sync-md` pour rapatrier les déplacements vers `tasks/*.md` avant de re-rendre.
- **Sans terminal Python :** lis `references/schema.md` et manipule les fichiers `.md` + `kanban.json` en respectant strictement le schéma.

## 2. Modèle par défaut & Dimension Temporelle (« calendrier souple »)
- **5 colonnes :** `idees` (*Idées*) → `a_faire` (*À faire*, WIP 10) → `en_cours` (*En cours*, WIP 3) → `en_revue` (*En revue*, WIP 5) → `termine` (*Terminé*). Limites WIP souples (alerte sans bloquer) + flag `bloqué` + `archive/`.
- **Trio temporel optionnel (dans le même tableau) :**
  - `horizon` : `cette_semaine` | `ce_mois` | `plus_tard` (intention de période sans heure fixe).
  - `start_date` / `due_date` (`YYYY-MM-DD`) : plage pour la vue **Frise** et alerte de retard.
  - `defer_until` (`YYYY-MM-DD`) : met la carte en veille hors du *Focus du jour* jusqu'à la date voulue.

## 3. Commandes CLI essentielles
Toutes les commandes acceptent `--dir <dossier-tableau>` et `--json`.

| Action | Commande |
| :--- | :--- |
| Initialiser | `init [--title "..."] [--force]` |
| Ajouter | `add --title "..." [--status idees] [--priority normale] [--horizon ...] [--start-date ...] [--due-date ...] [--defer-until ...] [--tags a,b] [--assignee ...] [--category ...] [--depends-on T-001] [--subtask "..."] [--body "..."]` |
| Modifier | `edit <ID> [--title ...] [--priority ...] [--horizon ...] [--due-date ...] [--append-note "..."]` |
| Déplacer | `move <ID> <colonne> [--actor ...] [--as-agent] [--note "..."]` |
| Prendre / Libérer | `claim <ID> --by <acteur> [--start]` / `release <ID>` |
| Bloquer / Débloquer | `block <ID> --reason "..."` / `unblock <ID>` |
| Sous-tâches | `check <ID> <index_ou_texte>` / `uncheck <ID> <index_ou_texte>` |
| Lister / Voir | `list [--status ...] [--horizon ...] [--blocked] [--stale] [--overdue]` / `show <ID>` |
| Rituels | `focus` · `triage` · `archive [--id <ID> \| --done-older-than 14 \| --all-done]` · `delete <ID> [--force]` |
| Pièces jointes | `attach <ID> (--file <chemin> \| --ref <ref>)` · `detach <ID> <cible>` |
| Vues & Sync | `render [--no-html] [--force-md]` · `sync-md [--dry-run]` · `stats` · `validate` |
| Config & Libellés | `config [--title ...] [--category-label ...] [--tag-label ...] [--rename-column col=Libellé] [--rename-horizon h=Libellé]` · `rename-category <ancien> [nouveau]` · `rename-tag <ancien> [nouveau]` |
| Serveur interactif | `serve [--port 8765] [--open] [--strict-port]` (mode web local 127.0.0.1 : glisser-déposer, édition par onglets, dépendances cliquables, pièces jointes, suppression protégée et **Réglages**) |
| Lot JSON | `batch --stdin` (tableau JSON de `{"op": "...", ...}`) |

## 4. Les 3 Rituels Conversationnels (concis)
1. **Focus du jour (`focus`) :** à la demande (*« Qu'est-ce que je fais aujourd'hui ? »*), affiche les cartes `en_revue` à valider, les retards/échéances proches, les cartes `en_cours`, l'horizon `cette_semaine` et le haut de `a_faire` (hors cartes en veille `defer_until` ou bloquées par dépendance), avec alertes WIP.
2. **Triage & Blocages (`triage`) :** passe en revue les `idees` à promouvoir, les blocages explicites/implicites, les cartes stagnantes (âge dans la colonne ≥ `stale_days`, non réinitialisé par une simple retouche) et les réveils proches.
3. **Archivage (`archive`) :** propose de déplacer les cartes `termine` vers `archive/` pour garder `KANBAN.md` et `kanban.html` rapides et lisibles, tout en préservant l'historique et la suite des IDs.

## 5. Procédure de démarrage du serveur interactif (`serve`)
- **Quand démarrer :** dès que l'utilisateur demande à voir, ouvrir ou manipuler son tableau dans l'interface web (ou juste après `init`).
- **Lancement non bloquant :** démarre `serve` **toujours en arrière-plan / tâche de fond** (jamais bloquant au premier plan) :
  `python3 -B "<dossier-skill>/scripts/kanban.py" --dir <dossier-tableau> serve --port 8765` (ou `python -B` sous Windows).
  Si le port `8765` est déjà occupé, le script bascule automatiquement sur le prochain port libre (sauf avec `--strict-port`).
- **URL & ouverture :** lis la ligne `Serveur Kanban interactif prêt sur http://127.0.0.1:<port>/kanban.html`, transmets le lien à l'utilisateur ou ouvre-le dans le navigateur intégré s'il existe. Pour un lancement manuel par un humain, `--open` ouvre le navigateur par défaut.
- **Repli sans shell local persistant (ex. Claude Desktop) :** exécute `render` pour générer `kanban.html` (autonome en lecture seule) et `KANBAN.md`, puis indique la commande locale `serve --open`.

## 6. Références
- Schéma complet (`kanban.json`, frontmatter YAML plat, `history`, `batch --stdin`, compatibilité Obsidian) : `references/schema.md`.
