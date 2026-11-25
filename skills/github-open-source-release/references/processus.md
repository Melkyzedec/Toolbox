# Cartographie des Processus — `github-open-source-release`

Ce document décrit le processus complet de préparation, contrôle de sécurité à 4 étages (`check-release.ps1`) et publication open source sur GitHub sans contournement de protection de branche.

---

## 1. Workflow de Préparation, Contrôle & Publication (4 Phases)

```mermaid
flowchart TB
    subgraph P1["1. Cadrage & Verification d'Acces"]
        CAD["Fixer livrable (sources / paquet / Release),\ncompte unique, depot, visibilite, licence MIT/autre"]
        AUTH["Verif gh api user + gh repo view\n(aucun jeton ou code affiche)"]
    end

    subgraph P2["2. Constitution du Candidat Public Isole"]
        ISO["Clone ou copie isolee hors chantier principal\n• Identite auteur/committer noreply uniquement\n• .gitignore (.env, *.pem, *.key, *.sqlite)\n• Fixtures CANARY_TEST_ / @example.test\n• Zero chemin prive · Zero code externe non audite"]
        PREP["README, LICENSE, CI 0 EUR (actions epinglees SHA-40),\ndependances --require-hashes SHA-256"]
    end

    subgraph P3["3. Porte Deterministe (check-release.ps1)"]
        FAST["Mode -Fast (< 1s) en boucle dev / pre-commit\n(gitHistory: skipped-fast)"]
        FULL["Mode complet avec -GitRoot (obligatoire avant publication)\nInspecte arbre, .zip (prof. 3) + historique Git complet\n-> status: passed & SHA-256 exact"]
    end

    subgraph P4["4. Accord Explicite & Publication Sans Bypass"]
        STOP["Recapitulatif complet + STOP\n(depot, licence, auteur, commit, fichiers, SHA-256)"]
        PUB{"Branche main protegee ?\n(enforce_admins: true)"}
        PR["Branche dediee + Pull Request\nAttente CI verte + CodeQL (0 --admin / 0 bypass)"]
        PUSH["Envoi direct si main non protegee"]
        POST["Controle post-diffusion :\nvisibilite, licence, raw HTTP 200 sans auth"]
    end

    CAD --> AUTH --> ISO --> PREP --> FAST --> FULL --> STOP --> PUB
    PUB -->|"Oui (PR obligatoire)"| PR --> POST
    PUB -->|"Non"| PUSH --> POST
```

---

## 2. Les 4 Étages Déterministes de `check-release.ps1`

```mermaid
flowchart LR
    E1["1. artifact-layout\n• 0 symlink/jonction sortant\n• 0 traversal ../ ni nom reserve Windows\n• 0 fichier prive (.git, .env, .sqlite, .key)\n• 0 bombe ou doublon ZIP (prof. <= 3)"]
    E2["2. artifact-dlp\n• 0 chemin prive Windows/Unix\n• 0 dossier cache bare hors home\n• 0 PII (IBAN, NIR, e-mail perso)\n• Exemption fixtures CANARY_TEST_"]
    E3["3. artifact-sast (Skill-Scanner)\n• 0 caractere Unicode invisible / Bidi\n• 0 execution distante ou obfusquee\n  (curl|bash, iex, eval/atob)"]
    E4["4. Secrets & git-identity\n• Gitleaks --redact=100 (arbre + ZIP)\n• Hors -Fast : historique non shallow,\n  100 % commits en @users.noreply.github.com,\n  Gitleaks --all --full-history"]

    E1 --> E2 --> E3 --> E4
```
