# Matrice d'Attaque — Tests Hostiles Multi-Projets (`qa-hostile-tests`)

Active uniquement les familles correspondant réellement aux surfaces présentes dans le projet, sans jamais sacrifier la rigueur logique.

---

## 1. Parcours E2E Boîte Noire (Vrai Démarrage à Froid)
- Lancer le **vrai point d'entrée** en sous-processus (`node cli.mjs`, `python`, script `.ps1`, serveur local éphémère sur `127.0.0.1:0`), jamais uniquement une fonction interne en mémoire.
- Tester le cycle complet : **création → persistance → fermeture complète → réouverture/relecture depuis le disque ou la base**.
- Vérifier le contrat CLI exact : arguments invalides, `--stdin`, codes de sortie (`!== 0` sur erreur), et format strict `stdout`/`stderr` (pas de warning parasite cassant un parseur JSON).

## 2. Cohérence Logique & Machine à États (Universel)
Même sans aucun calcul financier, tout logiciel a une logique à attaquer :
- **Transitions illégales :** tenter d'exécuter une action hors séquence (ex. valider avant d'avoir préparé/livré, ré-ouvrir/re-livrer un élément déjà clôturé ou annulé, modifier une entité archivée/verrouillée).
- **Allers-retours (`A → B → A`) & idempotence :** vérifier que répéter 2 fois une même opération ne crée ni doublon ni corruption, et qu'un aller-retour restaure exactement l'état ou l'historique attendu sans perte.
- **Cascades parent / enfant & plafonds :** que deviennent les enfants quand on archive, supprime ou annule le parent ? Que se passe-t-il exactement à `N`, `N+1` (plafond dépassé) et après libération à `N-1` ?
- **Coexistence & concurrence basique :** deux lancements parallèles ou rapides qui écrivent au même endroit laissent-ils une donnée cohérente sans fichier tronqué ni verrou bloqué ?
- **Temps & limites :** minuit local vs UTC (`2026-07-15T00:30:00+02:00`), passage d'année, valeurs nulles (`0`, `""`), chaînes très longues.

## 3. Sabotage d'Entrées & Pannes (« Chaos Contrôlé »)
- **Entrées malformées :** champs requis manquants, mauvais type (`123` au lieu d'objet/tableau), `JSON` tronqué, fichier vide `0` octet, ligne finale sans `\n`, texte avec guillemets / retours chariot / séparateurs de tableau Markdown (`|`).
- **Crash / écriture incomplète :** simuler un verrou orphelin, un fichier manifeste corrompu d'un octet, ou vérifier l'absence de fichiers temporaires `.tmp` laissés en cas d'échec (rollback disque + base atomique).
- **Erreur silencieuse interdite :** un fichier corrompu critique doit lever une erreur ou une alerte explicite, jamais renvoyer silencieusement `0` ou `ok: true`.

## 4. Pièges Windows 10, PowerShell & Chemins
- Noms avec **espaces**, **accents** (`données-é.json`), **`#`** ou parenthèses.
- Chemins mixtes `/` vs `\`, casse Windows (`C:\` vs `c:\`), préfixes `\\?\`, noms courts `8.3` si présents, et fins de ligne CRLF (`\r\n`) vs LF (`\n`).
- **Isolation Git / Temp :** si un test vérifie le comportement « hors dépôt Git » sous `%TEMP%`, utiliser `GIT_CEILING_DIRECTORIES` pour éviter de remonter vers un éventuel `.git` ancêtre dans `%USERPROFILE%`.
- Noms réservés Windows (`CON`, `NUL`, `PRN`, `AUX`, `COM1`, `LPT1`) si le programme crée des dossiers/fichiers depuis des saisies utilisateur.

## 5. Frontières Sécurité, Canaris DLP (`CANARY_TEST_`), SAST & Calculs
- **Chemins & symlinks :** path traversal (`../secret`, préfixe voisin `../projet-evil`), chemin absolu externe, jonction NTFS (`junction`) et **lien symbolique pendant (*dangling symlink*)** en écriture.
- **Canaris DLP / Secrets (`CANARY_TEST_`) & bundles web :**
  - Injecter dans le vrai point d'entrée des données synthétiques marquées `CANARY_TEST_` ou `@example.test` (faux IBAN et faux NIR construits à l'exécution, email `canary_test_user@example.test`, chemins machine locaux factices, jetons construits par concaténation en mémoire comme `'sk-' + 'CANARY_TEST_TOKEN'` pour ne jamais déclencher les scanners statiques sur le code de test au repos).
  - Vérifier de façon déterministe qu'aucune sortie non autorisée (`stdout`, `stderr`, rapports publics, logs, fichiers SQLite `-wal`/`-shm`, variables front-end `VITE_*`, `NEXT_PUBLIC_*`, `EXPO_PUBLIC_*`) ne fait fuiter ces canaris.
- **Scan SAST déterministe & Hygiène du code :**
  - Vérifier dans les sources et scripts du projet l'absence de caractères Unicode invisibles ou Bidi (*Trojan Source* : `U+200B..U+200F`, `U+202A..U+202E`, `U+2066..U+2069`, tags `U+E0000..U+E007F`), d'exécutions dynamiques risquées (évaluation de code décodé en base64, constructeur dynamique de fonction, téléchargement exécuté à la volée dans un shell) et d'appels réseau cachés non documentés.
- **Calculs & agrégats :** si le projet calcule montants, pourcentages, quotas ou scores, tester les cas limites (arrondis `0.005`, TVA mixte, remise, division par zéro, négatifs, sommes vides).

## 6. Discipline d'Écriture des Tests (Zéro Test Passoire & TDD)
- **Assertions précises :** pas de simple `assert(res)` ni `assert.throws(fn)` nu ; vérifier le code d'erreur exact (`err.code`), les clés/valeurs retournées **et** l'état relu sur disque après fermeture.
- **Autonomie complète :** chaque bloc/test prépare son propre répertoire temporaire isolé (pas de dépendance cachée à l'ordre entre scénarios ni au profil utilisateur réel).
- **Règle d'échec honnête (`Red -> Green`) :** en reprise (`Brownfield Gate`) comme en TDD, quand un test casse le moteur, le laisser échouer proprement (ou le consigner explicitement comme `ÉCHEC CONFIRMÉ — BUG MOTEUR`) sans jamais bricoler la tolérance pour afficher vert.
