# Cartographie des Processus — `mon-standard-skill`

Ce document synthétise le cycle complet de création, mise à jour, sécurisation, cartographie et distribution multi-outils d'une skill Windows.

---

## 1. Cycle de Vie en 3 Zones, Mise à Jour & Emplacement Canonique

```mermaid
flowchart TB
    DEV["1A. Nouvelle skill (Chantier)\n<projets-root>/skills en developpement/<nom>/\n(SKILL.md + references/ + scripts/)"]
    MOD["1B. Modification d'une skill existante\n(Source maitre / chantier · Tous modeles IA)"]
    TESTS["2. Validation Fonctionnelle & Tests\n0 USD · Isolation temp dir · 100 % PASS"]
    MAP["3. Cartographie Obligatoire (Creation ou MAJ)\n<dossier-skill>/references/processus.md\n(Mermaid UTF-8 sans BOM · 0 chemin prive)"]
    AUDIT["4. Audit Prealable des Chemins (0 USD)\nVerification references & liens avant promotion"]

    subgraph Promo["5. Promotion en Copies Reelles Identiques (SHA-256)"]
        PERSO["<projets-root>/skills perso/skills/<nom>/\n(Source maitre)"]
        PUB["<projets-root>/skills publiees/<nom>/\n+ Toolbox/skills/<nom>/"]
        AGY["Antigravity\nDossier global Antigravity\n(config/skills/<nom>/)"]
        CLAUDE["Claude Code\nDossier global Claude\n(skills/<nom>/)"]
        CODEX["Codex (si autorise)\nDossier global Codex\n(skills/<nom>/ + agents/openai.yaml)"]
        ZIP["Archive Claude Desktop\n<nom>-claude-desktop.zip\n(Chemins POSIX /, sans agents/)"]
    end

    CLEAN["6. Nettoyage Chantier\nSuppression de skills en developpement/<nom>/"]

    DEV --> TESTS
    MOD --> TESTS
    TESTS --> MAP --> AUDIT
    AUDIT --> PERSO
    PERSO --> PUB
    PERSO --> AGY
    PERSO --> CLAUDE
    PERSO --> CODEX
    PERSO --> ZIP
    Promo --> CLEAN
```

---

## 2. Porte de Validation Déterministe & Relais Documentation

```mermaid
flowchart LR
    GATE["check-release.ps1 -Fast\nLayout + DLP + SAST + Gitleaks\n-> status: passed"]
    QV["quick_validate.py\n(si Codex dans le perimetre)\n-> Skill is valid!"]
    HASH["Controle Parite & Proprete\n• Memes SHA-256 sur 6 cibles\n• UTF-8 sans BOM · LF\n• 0 __pycache__ / .pyc / README.md"]
    ISO["Isolation Stricte\n• agents/openai.yaml dans Codex seul\n• .zip POSIX sans agents/"]
    DONE["Restitution & Relais Doc\n• Espace doc MAJ directe si acces\n• Sinon tache datee dans TASKS.md\n  avec fichiers exacts pour relais"]

    GATE --> QV --> HASH --> ISO --> DONE
```
