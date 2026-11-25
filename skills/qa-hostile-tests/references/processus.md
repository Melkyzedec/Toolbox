# Cartographie des Processus — `qa-hostile-tests`

Ce document synthétise le déroulement d'une campagne de tests hostiles boîte noire à `0 USD`, depuis le cadrage d'entrée jusqu'à la restitution en 4 points.

---

## 1. Workflow d'Exécution & Garde-Fous (`0 USD`)

```mermaid
flowchart TB
    subgraph Entry["Modes d'Entree"]
        M_TDD["Pre-Dev TDD (Jalon 1 RED)\nFormaliser le contrat en echec avant le code"]
        M_BG["Mission 0 (Brownfield Gate)\nFilet de caracterisation sur code repris/tiers"]
    end

    GUARD["Regles Non Negociables (actives en continu)\n• ZERO modification du code de production\n• Safety-Net 0-token (0 lecture .env/cles/PII)\n• Zero Untrusted External Code\n• 0 USD (execution locale isolee en temp dir, finally clean)\n• Canaris fictifs CANARY_TEST_ / @example.test uniquement"]

    S1["Etape 1 : Analyser sans presuppose\nPromesse metier, surfaces a risque, pile native"]
    S2["Etape 2 : Batir la matrice d'attaque (references/matrice-tests-hostiles.md)\n1. E2E boite noire · 2. Invariants & machine a etats\n3. Sabotage d'entrees & pannes · 4. Pieges Windows/OS\n5. Traversal/symlinks/DLP CANARY_TEST_ / SAST · 6. Mutation thinking"]
    S3["Etape 3 : Ecrire la suite isolee\nFichier dedie en dossier temp nettoye en finally"]
    S4["Etape 4 : Executer + Porte deterministe\nSuite + existants (0 USD) + check-release.ps1 -Fast si disponible\nDistinction stricte : vrai bug moteur vs defaut de fixture"]
    S5["Etape 5 : Restitution en 4 points (francais clair)\n1. Logique comprise · 2. Ce qui passe (X/X)\n3. Failles provoquees (fichier:ligne) · 4. Correctifs recommandes pour Jalon GREEN"]

    Entry --> GUARD --> S1 --> S2 --> S3 --> S4 --> S5
```
