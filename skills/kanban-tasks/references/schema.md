# Référence du Schéma — `kanban-tasks`

Ce document décrit le format exact des fichiers manipulés par `scripts/kanban.py`, ainsi que la marche à suivre en mode dégradé sans terminal Python.

---

## 1. Arborescence d'un tableau (`<dossier-tableau>/`)

```text
<dossier-tableau>/
├── kanban.json          # Configuration du tableau, colonnes, horizons, compteur next_id
├── KANBAN.md            # Vue générée (compatible Obsidian Kanban), protégée par SHA-256
├── kanban.html          # Tableau interactif autonome (Kanban, Horizons, Frise, Stats)
├── .gitignore           # Ignore .kanban.lock (et optionnellement kanban.html)
├── .kanban.lock         # Verrou OS temporaire de concurrence (ne pas versionner)
├── tasks/
│   └── T-001-exemple.md # 1 fichier Markdown par carte active
└── archive/
    └── T-000-ancien.md  # Cartes terminées archivées
```

---

## 2. Configuration `kanban.json`

```json
{
  "version": 1,
  "title": "Tableau Kanban",
  "id_prefix": "T",
  "next_id": 1,
  "stale_days": 7,
  "obsidian_compat": true,
  "last_rendered_md_sha256": "",
  "labels": {"category": "Catégorie", "tag": "Tag"},
  "categories": [],
  "tags": [],
  "columns": [
    {"id": "idees", "label": "Idées", "wip_limit": null, "role": "backlog"},
    {"id": "a_faire", "label": "À faire", "wip_limit": 10, "role": "todo"},
    {"id": "en_cours", "label": "En cours", "wip_limit": 3, "role": "in_progress"},
    {"id": "en_revue", "label": "En revue", "wip_limit": 5, "role": "review"},
    {"id": "termine", "label": "Terminé", "wip_limit": null, "role": "done"}
  ],
  "horizons": [
    {"id": "cette_semaine", "label": "Cette semaine"},
    {"id": "ce_mois", "label": "Ce mois-ci"},
    {"id": "plus_tard", "label": "Plus tard"}
  ]
}
```

- `role` autorisés : `backlog`, `todo`, `in_progress`, `review`, `done`.
- `wip_limit` : entier positif ou `null` (alerte souple uniquement, jamais bloquant).

---

## 3. Format d'une carte (`tasks/T-XXX-slug.md`)

Le frontmatter YAML est **strictement plat** (scalaires + listes plates de chaînes) pour garantir 0 dépendance externe et 100 % de compatibilité avec les propriétés Obsidian.

### Champs requis
- `id` : identifiant unique (ex. `T-001`)
- `title` : titre court de la tâche
- `status` : identifiant d'une colonne définie dans `kanban.json`
- `priority` : `basse` | `normale` | `haute` | `urgente`
- `created_at` / `updated_at` : horodatage UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`)
- `history` : liste plate d'entrées `"YYYY-MM-DDTHH:MM:SSZ | <de> -> <vers> | <acteur>"`

### Champs optionnels (omis si vides pour garder les cartes légères)
- `tags` : liste de tags (`["CANARY_TEST_TAG", "doc"]`)
- `assignee` : personne ou agent (ex. `humain`, `agent:assistant`, `CANARY_TEST_user@example.test`)
- `claimed_at` : horodatage UTC de prise en charge via `claim`
- `category` : thème / regroupement pour les filtres et *swimlanes*
- `horizon` : `cette_semaine` | `ce_mois` | `plus_tard` (ou id configuré dans `kanban.json`)
- `start_date` / `due_date` / `defer_until` : dates `YYYY-MM-DD`
- `depends_on` : liste d'IDs prérequis (`["T-001"]`)
- `attachments` : liste de pièces jointes (`attachments/T-001/fichier.txt`) ou références relatives (`docs/spec.md`)
- `blocked` : `true` (omis si `false`)
- `blocked_reason` : motif du blocage

### Exemple complet canari

```markdown
---
id: T-001
title: "CANARY_TEST_Exemple de carte complète"
status: en_cours
priority: haute
tags:
  - CANARY_TEST_TAG
  - qa
assignee: "agent:assistant"
claimed_at: "2026-11-24T10:00:00Z"
category: "Qualité"
horizon: cette_semaine
start_date: "2026-11-24"
due_date: "2026-11-28"
depends_on:
  - T-000
created_at: "2026-11-24T09:00:00Z"
updated_at: "2026-11-24T10:00:00Z"
history:
  - "2026-11-24T09:00:00Z | creation -> a_faire | humain"
  - "2026-11-24T10:00:00Z | a_faire -> en_cours | agent:assistant"
---

Description libre de la tâche.

## Sous-tâches
- [x] Préparer les données fictives CANARY_TEST_
- [ ] Vérifier le rendu final
```

---

## 4. Mode `batch --stdin` (PowerShell UTF-8)

Permet d'enchaîner plusieurs mutations sous un seul verrou avec un seul rendu final :

```powershell
@'
[
  {"op": "add", "title": "CANARY_TEST_Tâche A", "status": "a_faire", "horizon": "cette_semaine", "subtasks": ["Étape 1", "Étape 2"]},
  {"op": "move", "id": "T-001", "status": "en_revue", "actor": "agent:assistant", "as_agent": true},
  {"op": "check", "id": "T-001", "target": "1"}
]
'@ | python -B "<dossier-skill>/scripts/kanban.py" --dir ./kanban batch --stdin --json
```

Opérations supportées dans `batch` : `add`, `edit`, `move`, `claim`, `release`, `block`, `unblock`, `check`, `uncheck`, `archive`, `delete`, `config`, `save_settings`, `rename-category`, `rename-tag`, `add_taxonomy`.

---

## 5. Règles Obsidian & `sync-md`

- `KANBAN.md` inclut `kanban-plugin: board` en tête et le bloc `%% kanban:settings ... %%` en pied quand `obsidian_compat` vaut `true`.
- À chaque `render`, l'empreinte SHA-256 de `KANBAN.md` est sauvegardée dans `kanban.json` (`last_rendered_md_sha256`).
- Si l'utilisateur déplace une carte dans Obsidian ou édite `KANBAN.md` à la main, `render` refuse d'écraser le fichier. Lancer `sync-md` lit les sections `## <Colonne>` de `KANBAN.md`, applique les changements de statut dans `tasks/*.md` (+ `history`), puis régénère `KANBAN.md` et `kanban.html`.

---

## 6. Mode interactif local (`serve [--port 8765] [--open] [--strict-port]`)

- Le fichier `kanban.html` posé sur disque reste toujours **autonome et en lecture seule** (`"interactive": false`, aucun jeton).
- Lorsqu'on lance `python -B "<dossier-skill>/scripts/kanban.py" --dir <dossier-tableau> serve --port 8765`, un serveur local (`127.0.0.1` uniquement, 0 dépendance) sert la même interface avec `"interactive": true` et un jeton CSRF éphémère en mémoire :
  - si le port demandé est déjà utilisé, le serveur bascule automatiquement sur le prochain port libre (désactivable via `--strict-port`) ;
  - `--open` ouvre automatiquement l'URL dans le navigateur par défaut du système ;
  - glisser-déposer des cartes entre colonnes ou horizons ;
  - création (`+ Nouvelle carte`), édition par onglets (`Modifier la carte`, sélecteur visuel de dépendances, aperçu Markdown), cochage des sous-tâches, pièces jointes (`POST /api/upload`, extensions sûres ≤ 5 Mo), suppression protégée (`delete`) et archivage en direct dans `tasks/*.md` + `KANBAN.md` sous verrou `.kanban.lock` ;
  - bouton **Réglages** pour personnaliser le titre, les libellés des champs (`category`, `tag`), des colonnes et horizons, ainsi que pour ajouter, renommer en masse ou retirer des catégories et des tags.
