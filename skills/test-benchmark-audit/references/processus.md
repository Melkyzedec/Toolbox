# Cartographie des Processus — `test-benchmark-audit`

Ce document décrit les 3 modes séquentiels d'audit hostile, de correction guidée par tranches TDD (`Red -> Green`) et de contre-vérification globale à `0 USD`.

---

## 1. Enchaînement des 3 Modes & Barrières de Validation

```mermaid
flowchart TB
    RULES["Garde-fous transverses (Modes 1, 2 & 3)\n• Safety-Net 0-token · Zero Untrusted External Code\n• 0 USD (aucun run LLM live par defaut) · Temp dir nettoye en finally\n• Fixtures CANARY_TEST_ / @example.test · Preuves fichier:ligne verifiees"]

    subgraph M1["Mode 1 — Audit Hostile & Brownfield Gate (defaut · Lecture seule)"]
        A1["Etape A : Cartographie & hygiene DLP/SAST\n(scinde en 2 passes si > 1800 lignes)"]
        A2["Etape B : Grille 7 axes (references/grille-audit-hostile.md)\ndont trace TDD Red->Green & Skill-Scanner"]
        A3["Etape C : Priorisation (Critique / Haute / Moyenne / Basse)"]
        A4["Etape D : Rapport .md obligatoire dans le dossier audite\n(sans ecraser l'historique)"]
        A1 --> A2 --> A3 --> A4
    end

    STOP1{"Demande explicite de\ncorrection suivant le .md ?"}

    subgraph M2["Mode 2 — Correction par Tranches TDD (Red -> Green)"]
        T1["Tranche 1 : Failles Critiques uniquement\nTest hostile en echec (RED) -> Fix -> Test vert (GREEN) -> STOP"]
        T2["Tranche 2 : Failles Hautes (apres feu vert)\nTDD Red -> Green -> Tests complets 0 USD -> STOP"]
        T3["Tranche 3 : Moyennes / Basses -> Maj ## 5. Suivi du .md"]
        T1 --> T2 --> T3
    end

    subgraph M3["Mode 3 — Reception & Contre-Verification Globale (0 USD)"]
        V1["1. Revue croisee code reel vs .md (0 skip, 0 mock menteur)"]
        V2["2. Contre-test mutation (mutation-check.ps1)\nScore minimal 80 % · survivant Critique/Haute = bloquant"]
        V3["3. Re-test global de 100 % des suites + check-release.ps1\n(-Fast ou complet -GitRoot -> status: passed)"]
        V4["4. Proces-verbal ## 6. Cloture dans le .md\nVerdict : Valide ou Bloquants restants"]
        V1 --> V2 --> V3 --> V4
    end

    RULES --> M1 --> STOP1
    STOP1 -->|"Oui"| M2 --> M3
    STOP1 -->|"Contre-verification directe"| M3
```
