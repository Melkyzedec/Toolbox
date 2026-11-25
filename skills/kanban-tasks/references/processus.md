# Cartographie des Processus & Architecture — `kanban-tasks`

Ce document présente l'architecture locale, les garde-fous d'exécution et le cycle de vie des cartes pilotés par `kanban-tasks` (`python3 -B <dossier-skill>/scripts/kanban.py`).

---

## 1. Architecture Locale & Modes d'Exécution

```mermaid
flowchart TB
    subgraph Triggers["Déclencheurs & Modes"]
        CLI["CLI / Lot batch --stdin\n(Python 3 stdlib uniquement, 0 dépendance)"]
        RIT["3 Rituels Conversationnels\nfocus · triage · archive"]
        OBS["Édition manuelle / Obsidian\n(KANBAN.md)"]
        WEB["Serveur Web Local 127.0.0.1\nserve --port 8765 [--open]"]
    end

    subgraph Lock["Verrouillage & Validation Déterministe"]
        FLOCK["Verrou exclusif .kanban.lock\n(msvcrt / fcntl, écritures atomiques)"]
        GATE["Garde-fous Métier\n• Agent -> en_revue seul (--as-agent)\n• Humain -> valide termine\n• Port fallback si 8765 occupé\n• Jeton CSRF éphémère en mémoire\n• Anti-traversal pièces jointes <= 5 Mo"]
    end

    subgraph Board["Dossier du Tableau (<dossier-tableau>/)"]
        CFG[("kanban.json\nConfig, libellés, SHA-256 KANBAN.md,\nsuite d'IDs T-xxx monotone")]
        TASKS["tasks/T-xxx-*.md\nFrontmatter YAML plat + sous-tâches"]
        ARCH["archive/T-xxx-*.md\nHistorique conservé, IDs jamais réutilisés"]
        ATT["attachments/T-xxx/\nPièces jointes locales sûres"]
        MD["KANBAN.md\nProtégé par empreinte SHA-256\n(sync-md si modifié à la main)"]
        HTML["kanban.html\nExport autonome lecture seule\n(Kanban · Horizons · Frise · Réglages)"]
    end

    CLI --> FLOCK
    RIT --> CLI
    WEB -->|"API JSON + CSRF"| FLOCK
    OBS -->|"sync-md"| FLOCK
    FLOCK --> GATE
    GATE <--> CFG
    GATE <--> TASKS
    GATE <--> ARCH
    GATE <--> ATT
    GATE -->|"render (refuse si hash modifié)"| MD
    GATE -->|"render (interactive: false)"| HTML
    WEB -->|"Sert vue interactive: true"| HTML
```

---

## 2. Séquence Bout-en-Bout (Agent vs Humain & Interfaces)

```mermaid
sequenceDiagram
    autonumber
    actor Humain as Utilisateur
    participant Agent as Agent IA (Skill kanban-tasks)
    participant Engine as kanban.py (.kanban.lock)
    participant Disk as tasks/*.md + kanban.json + KANBAN.md
    participant Web as Serveur Local 127.0.0.1

    Humain->>Agent: Demande de tableau / création de tâches
    Agent->>Engine: init + batch --stdin (sous un verrou unique)
    Engine->>Disk: Écrit kanban.json + tasks/T-xxx-*.md + KANBAN.md + kanban.html

    opt Ouverture visuelle interactive
        Agent->>Web: Lance serve --port 8765 en arrière-plan (non bloquant)
        Web-->>Agent: URL http://127.0.0.1:<port>/kanban.html (repli port libre auto)
        Agent-->>Humain: Retourne l'URL locale interactive
    end

    Agent->>Engine: claim T-001 --by agent:assistant --start
    Engine->>Disk: Passe en_cours + claimed_at + history
    Agent->>Engine: check T-001 1 && move T-001 en_revue --as-agent
    Note over Engine,Disk: Refus mécanique si l'agent tente move -> termine
    Engine->>Disk: Dépose en en_revue pour validation humaine

    Humain->>Web: Glisser-déposer ou validation manuelle (+ Réglages / PJ)
    Web->>Engine: POST /api/op (avec jeton CSRF éphémère)
    Engine->>Disk: Valide en_revue -> termine + re-génère KANBAN.md

    opt Si KANBAN.md a été modifié à la main (Obsidian)
        Engine-->>Agent: render bloqué (empreinte SHA-256 divergente)
        Agent->>Engine: sync-md
        Engine->>Disk: Rapatrie les déplacements vers tasks/*.md puis re-rend
    end
```

---

## 3. Cycle de Vie d'une Carte & Planification Souple

```mermaid
stateDiagram-v2
    [*] --> idees : add --status idees
    [*] --> a_faire : add (défaut)

    idees --> a_faire : Triage / promotion
    a_faire --> en_cours : claim --start / move en_cours
    en_cours --> en_revue : Travail agent terminé (--as-agent)
    en_revue --> termine : Validation humaine uniquement
    en_cours --> a_faire : release / retour arrière
    en_revue --> en_cours : Reprise demandée (rework)

    state en_cours {
        [*] --> Active
        Active --> Bloquee : block --reason "..." ou dépendance non terminée
        Bloquee --> Active : unblock ou prérequis terminé / archivé
        Active --> EnVeille : defer_until > aujourd'hui (masqué de focus)
        EnVeille --> Active : Date atteinte (réveil dans triage/focus)
    }

    termine --> archive : archive (--done-older-than N / --all-done / --id)
    archive --> [*]
```
