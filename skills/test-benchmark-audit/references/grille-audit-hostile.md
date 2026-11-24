# Grille d'Audit Hostile — Tests, Oracles & Benchmarks LLM

Utilise cette grille de vérification complète pour chaque projet audité. Ne marque rien comme « sain » sans avoir cherché activement comment le contourner ou le casser.

---

## Axe 1 — *Mutation Testing* Mental, Trace TDD (`Red -> Green`) & Assertions Fantômes
Pour chaque bloc de tests critiques (métier, sécurité, concurrence, parsing, persistance) :
- **Test de cassure mentale :** *« Si je remplace la fonction testée par `return true` / `return 0`, si j'inverse une condition `!==`, ou si je supprime la vérification de signature/droits, quel `assert` précis casse ? »* Si aucun ne casse, le test est une coquille vide.
- **Vérification de la discipline TDD (`Red -> Green`) :**
  - Les tests vérifient-ils le comportement réel et les cas d'erreur documentés, ou ont-ils été écrits après coup pour calquer l'implémentation interne ?
  - Sur une reprise (`Brownfield Gate`) ou un correctif récent, existe-t-il un test de non-régression qui échoue franchement (`RED`) dès qu'on retire le garde-fou ?
- **Assertions molles / trompeuses :**
  - Vérifier seulement `stdout` ou `status === 'ok'` sans contrôler **l'effet de bord réel sur disque/DB après fermeture et réouverture**.
  - `assert.throws(fn)` ou `try/catch` sans vérifier `error.code` ni le motif exact (le test passe même si une `TypeError` ou un bug syntaxique a planté avant le garde-fou !).
  - Regex trop larges (`assert.match(out, /ok|pass/i)`), tolérances numériques floues sur des montants comptables/centimes, ou comparaison de longueur/clé sans vérifier les valeurs.
  - **Sauts silencieux (`silent skip`) :** branches `if (fs.existsSync(...)) { ... }` ou `if (items.length > 0)` qui ne font aucune assertion quand l'entrée manque.
- **Trous de numérotation / code mort :** repérer les tests sautés (`#13..#15`, `.skip`), helpers de test jamais appelés, et exports critiques du moteur absents de toute suite de test.

---

## Axe 2 — Isolation, Pollution d'État & Pièges Windows 10
- **Fuite vers l'environnement utilisateur réel :** les tests isolent-ils vraiment `HOME`, `USERPROFILE`, `CODEX_HOME`, `APPDATA`, variables `MEMENTO_*` / `OPENAI_*` / `ANTHROPIC_*` ? Restaurés dans `finally` ?
- **Piège Git ancêtre sous Windows :** création de dossiers supposés « hors Git » dans `%TEMP%` sans `GIT_CEILING_DIRECTORIES` (échoue silencieusement ou fausse le test si un parent dans `%USERPROFILE%` contient `.git`).
- **Ordre d'exécution & état partagé :** un test dépend-il d'une table SQLite, d'un fichier lock ou d'une variable globale remplie par le test précédent ? Que se passe-t-il si on exécute un cas seul ou dans l'ordre inverse ?
- **Spécificités Windows 10/PowerShell :**
  - Noms courts `8.3` (`UTILIS~1`), casse mixte (`C:\` vs `c:\`), préfixes `\\?\`, chemins avec espaces / `#` / accents.
  - Symlinks, jonctions NTFS, verrous transitoires `EPERM`/`EBUSY` (`STATUS_DELETE_PENDING`) sur SQLite `-wal`/`-shm` ou suppression de dossiers.
  - Quoting PowerShell (`'...'` vs `"..."`, backtick `` `" ``, `\"`, blocs `& { ... }`, BOM UTF-8, `\r\n` vs `\n`).

---

## Axe 3 — Mirage des Mocks & Dérive de Contrat (*Contract Drift*)
- **Faux tests d'intégration :** compter où la suite teste un objet JSON fabriqué à la main, un faux serveur HTTP ou une fonction interne en mémoire au lieu de lancer le **vrai processus enfant / vrai CLI / vrai hook (`stdin` -> `stdout` + disque)** avec séparation mémoire.
- **Dérive entre mocks et production :** comparer le schéma des fixtures/mocks de test (format JSONL Codex, payloads `PreToolUse`/`PostToolUse`/`Stop`, colonnes SQLite, en-têtes CSV/XLSX/PDF) avec ce que le code de production et les outils externes produisent **aujourd'hui**. Un test mocké sur un ancien format peut être 100 % vert alors que la prod est cassée.

---

## Axe 4 — Équité Réelle des Benchmarks A/B & Biais Expérimentaux
Si le projet contient un comparatif (`bare` vs `plugin`, modèle X vs Y, `AVEC` vs `SANS`) :
- **Asymétrie cachée de contexte :** comparer octet pour octet les prompts (`PROMPT*`), instructions système (`AGENTS.md`, `SKILL.md`), outils disponibles, fichiers pré-remplis (`DECISIONS-*`, `phase-0`), timeouts, niveaux d'effort, et configuration sandbox entre variantes A et B.
- **Contamination entre sessions / candidats :**
  - Un run « vierge » ou « sans outil » hérite-t-il par erreur d'un skill global, d'une base SQLite utilisateur ou d'un cache dans `%CODEX_HOME%` ?
  - Les noms de dossiers temporaires aléatoires changent-ils entre répétitions en cassant le préfixe stable (et donc le *prompt cache* serveur) dans un mode et pas un autre ?
- **Effet d'ordre & variance du cache serveur :** noter quand l'écart `$` / `inputUncached` tient au taux de `cached_input_tokens` (hasard serveur ou échauffement de préfixe par ordre d'exécution) plutôt qu'au code mesuré.
- **Sur-ajustement (*overfitting*) des scénarios :** les commandes ou textes de benchmark sont-ils calibrés exprès sur les mots-clés exacts / regex du filtre testé alors qu'une formulation voisine naturelle passerait à travers ?

---

## Axe 5 — Complaisance des Oracles & Calculs FinOps / Métier
- **Gaming / Contournement d'oracle :**
  - Le LLM peut-il obtenir `passed: true` / `6/6` en trichant (modifier un fichier de test/référence, recopier une somme depuis l'énoncé sans recalcul, écrire le JSON attendu avec des valeurs factices sans exécuter l'outil, ignorer un fichier `.md` ou `stdout` contradictoire) ?
  - L'oracle vérifie-t-il que le mécanisme évalué (rappel mémoire, compaction, garde-fou, outil SQL/Python) a **réellement** déclenché et contribué, ou valide-t-il aussi si la fonctionnalité est restée inerte (`rewritesRequested === 0`, `recallInjected === false`) ?
- **Exactitude mathématique & comptable :**
  - **Tokens & Coûts LLM :** vérifier si `cached_input_tokens` est bien soustrait de `input_tokens` avant calcul du prix plein, si `reasoning_output_tokens` est déjà inclus dans `output_tokens` (éviter double facturation), si les tarifs `/M` correspondent à chaque modèle du comparatif, et comment sont traitées les sessions avortées/incomplètes (`null` vs `0`).
  - **Projections (`$/mois`, ROI) :** vérifier l'extrapolation (ne pas projeter un coût *one-shot* d'initialisation Phase 0 comme coût récurrent mensuel sans séparation, ni extrapoler sans base Phase 1 mesurée).
  - **Métier (devis, factures, centimes, P2P, TVA) :** tolérances arrondis (`±0.01` vs exact), remises, avoirs, multi-taux TVA, cohérence `HT + TVA == TTC`.

---

## Axe 6 — Angles Morts Sécurité, RGPD/PII, Canaris DLP (`CANARY_TEST_`) & Scan SAST / `Skill-Scanner`
Vérifier si les tests et le dépôt couvrent explicitement les frontières de sécurité et d'hygiène :
- **Hygiène DLP des fixtures, snapshots et rapports (`CANARY_TEST_`) :**
  - Vérifier qu'aucune fixture de test, snapshot JSON ou rapport Markdown ne contient de vraie PII (IBAN, NIR, email personnel), de vrai secret ou de chemin machine personnel codé en dur. Toute donnée sensible simulée dans les tests doit utiliser la convention explicite `CANARY_TEST_` ou un domaine réservé (`@example.test`, `@example.com`).
- **Fuites de secrets, erreurs utilisateur & ZDR / Frontend :** détection des clés dans les sorties/journaux/bundles (`sk-`, `sk_live_`, `re_`, `AIza`, `ghp_`, `eyJ`, `PRIVATE KEY`), contrôle des variables exposées côté navigateur (`VITE_*`, `NEXT_PUBLIC_*`, `EXPO_PUBLIC_*`, `REACT_APP_*`), vérification `.env` / `.git` non servis, contrôle des fichiers SQLite `-wal`/`-shm`, et vérification que les messages d'erreur affichés à l'utilisateur final n'exposent aucun chemin système ni trace technique brute.
- **Scan SAST & `Skill-Scanner` (`Zero Untrusted External Code`) :**
  - Vérifier l'absence de caractères Unicode invisibles ou Bidi (*Trojan Source* : `U+200B..U+200F`, `U+202A..U+202E`, `U+2066..U+2069`, tags `U+E0000..U+E007F`) dans le code, les tests et les fichiers Markdown (`SKILL.md`, `references/`).
  - Vérifier l'absence d'exécution dynamique non bornée (code base64 évalué à la volée, téléchargement shell pipé directement vers un interpréteur, appels réseau cachés dans des suites supposées `0 USD`) et exiger le **verrouillage des dépendances par empreinte (`--require-hashes` / lockfile SHA-256)** ainsi qu'une CI déterministe (`0 €` sur dépôt public).
- **RGPD / PII Vault & Fichiers hostiles :** faux positifs/négatifs sur IBAN (espaces, Mod97), SIRET/SIREN, NIR, emails, téléphones multi-formats ; PDF scannés, XLSX avec formules non évaluées (`#REF!`, `#VALUE!`), archives `.xlsx`/`.docx`/`.zip` avec *Zip Slip* (`../`), symlinks sortants ou bombes ZIP, troncatures sans `\n`, verrous orphelins après crash.

---

## Axe 7 — Véracité Documentaire (`Docs` vs `Code` vs `JSON Bruts`)
- Recalculer manuellement un échantillon des totaux, pourcentages (`-X %`), coûts `USD`, durées et scores cités dans les rapports Markdown (`SYNTHESE*`, `RAPPORT*`, `AUDIT*`, `README*`) directement à partir des artefacts `.json` / logs source.
- Signaler tout mélange silencieux entre commits/versions différents, toute moyenne biaisée par suppression d'échecs, ou toute affirmation générale tirée d'un échantillon insuffisant (`N=1` ou `N=2` sur sortie stochastique).

---

## Gabarit de sortie pour le rapport `.md`

```markdown
# Audit des Tests & Benchmarks — <Nom du Projet>

> **Date :** AAAA-MM-JJ · **Périmètre :** <fichiers / dossiers lus> · **Mode :** Lecture seule sur l'existant (`0 USD`)

## 1. Périmètre inspecté & zones non vérifiées
- Liste exacte des fichiers lus (avec nombre de lignes si utile) et rapports JSON vérifiés.
- Éventuels sous-dossiers volontairement hors périmètre ou bloquants.

## 2. Tableau récapitulatif des failles identifiées
| ID | Sévérité | Axe | Emplacement (`fichier:ligne`) | Résumé de la faille |
| :--- | :--- | :--- | :--- | :--- |

## 3. Analyse détaillée des failles (Critique → Basse)
### [ID] Titre court (`Sévérité` · `Axe`)
- **Preuve (`fichier:ligne`) :** extrait de code/JSON ou calcul vérifié.
- **Scénario de contournement / fausse confiance :** ce qui passe à tort (ou fausse le score).
- **Correctif concret proposé (TDD `Red -> Green`) :** modification précise du test, de l'oracle, du harnais ou de la doc.

## 4. Verdict « Anti-Bullshit »
- **Ce qui est solidement prouvé aujourd'hui :** ...
- **Ce qui est illusoire, biaisé ou non couvert (priorité P0 avant usage/release) :** ...
```
