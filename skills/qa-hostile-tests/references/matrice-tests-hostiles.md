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

### Checklist Windows (6 points, issus de bugs réels)
1. **Fixtures binaires/LF :** écris-les avec `write_bytes` (jamais `write_text`, qui convertit `\n` en `\r\n` sous Windows).
2. **Chemins absolus portables :** construis-les via `os.path.abspath(...)` ; depuis Python 3.13 sous Windows, `os.path.isabs("\\x")` renvoie `False`.
3. **Un sous-dossier temporaire par nom de fichier testé :** NTFS ignore la casse (`NUL` et `nul` entrent en collision).
4. **Handles fermés sur erreur :** ouvre avec `with open(..., "rb")` avant de passer le flux à un parseur tiers (sinon `WinError 32` au nettoyage).
5. **BOM UTF-8 :** teste la lecture JSON/CSV avec BOM (`utf-8-sig`, produit par Windows PowerShell 5.1) et vérifie que l'écriture reste sans BOM.
6. **Vrai passage Windows avant fusion :** tout code produit sous Linux/Cloud passe une exécution réelle Windows + PowerShell (jonctions NTFS comprises).

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
- **Hygiène tests & scanners (`check-release`, Gitleaks, CodeQL) :**
  - Jamais de BOM ni d'Unicode invisible littéral dans un test : génère-les (`chr(0xFEFF)`, `"\u200b"`).
  - Jamais de variable ou fixture écrite sur disque nommée `secret`, `key` ou `passwd` : préfère des noms neutres (`canary_value`, `sample_token`).
  - Pour inspecter du HTML, utilise `html.parser.HTMLParser` plutôt qu'une regex sur `<script>` (signalée par CodeQL).
  - **Honnêteté OS & concurrence :** `SKIP` explicite hors Windows pour tout test Windows-only (zéro `PASS` muet sous Linux/Cloud) ; preuve horodatée d'un chevauchement réel des processus pour tout test de concurrence.
  - Données fictives uniquement : `CANARY_TEST_` / `@example.test`.
- **Règle d'échec honnête (`Red -> Green`) :** en reprise (`Brownfield Gate`) comme en TDD, quand un test casse le moteur, le laisser échouer proprement (ou le consigner explicitement comme `ÉCHEC CONFIRMÉ — BUG MOTEUR`) sans jamais bricoler la tolérance pour afficher vert.

## 7. Frontières Réelles : Données, SQLite Windows & Connecteurs
Active chaque bloc uniquement si la surface existe (base de données, argent, réseau, connecteur qui écrit). Ces angles couvrent ce que le test de mutation ne voit pas : il mute le code, pas le réseau, le disque ni l'horloge.

### 7.1 Data & Réseau
- **Concurrence :** deux requêtes ou deux processus identiques à la même milliseconde (double clic, cron + CLI manuel). La protection attendue est en base (`UNIQUE`, `UPDATE ... WHERE state = 'approved'` qui n'affecte qu'une ligne, verrou), jamais un `if` en mémoire.
- **Idempotence :** rejouer 2 ou 3 fois la même requête, le même webhook ou le même job doit laisser la base et les effets de bord (mails, crédits, publications) identiques à une seule exécution (clé d'idempotence `UNIQUE`).
- **Timeouts :** tout appel externe a un `timeout` explicite et n'est **jamais** fait à l'intérieur d'une transaction DB ouverte (file d'attente ou table `jobs` à la place).
- **Argent & temps :** montants en entiers dans la plus petite unité + devise, zéro `float` ; soldes et crédits en ledger *append-only* (`+100` / `-10`), jamais `balance = balance - 10`. Horodatage UTC côté serveur, jamais l'horloge client ; un rendez-vous futur garde l'heure locale + la timezone IANA (`Europe/Paris`).
- **Suppression & RGPD :** supprimer un utilisateur fictif `canary_test_user@example.test` qui a déjà payé. Attendu : pas de `ON DELETE CASCADE` aveugle sur les factures, anonymisation des champs personnels, et un *soft delete* qui n'empêche pas la réinscription (contrainte `UNIQUE` sur l'email).

### 7.2 Property-Based Testing (`0 €`)
- Cibler les **2 à 3 fonctions métier pures les plus critiques** (calcul, permission, parsing, transition d'état), écrire leurs invariants absolus (ex. « le prix remisé n'est jamais négatif ni supérieur au prix initial ») et les attaquer avec `fast-check` (JS) ou `Hypothesis` (Python) sur **≥ 500 cas** générés (négatifs, `0`, chaînes vides, Unicode RTL, années bissextiles, très grands tableaux).
- Fixer la graine (*seed*) dans le rapport pour rejouer un contre-exemple, et réduire chaque contre-exemple trouvé en test de régression classique.

### 7.3 Restauration & Migrations (*Game Day*)
- **Restauration réelle :** produire une sauvegarde par `VACUUM INTO` (ou l'API `backup`), la rouvrir dans un dossier temporaire neuf, exécuter `PRAGMA quick_check` (attendu `ok`) et comparer le nombre de lignes des tables critiques. Une sauvegarde jamais restaurée ne compte pas.
- **Migration sans perte :** appliquer la migration sur une fixture **déjà remplie** (données `CANARY_TEST_`), jamais sur une base vide. Échec attendu sur une colonne `NOT NULL` sans valeur par défaut ; vérifier qu'une migration interrompue laisse la base à l'état précédent (transaction).

### 7.4 SQLite & Windows réel
- **PRAGMA à chaque ouverture :** un test relit `PRAGMA foreign_keys` (`1`), `journal_mode` (`wal`), `busy_timeout` (`5000`) et `synchronous` (`2` = `FULL` pour une base finance) sur une **nouvelle** connexion. Les clés étrangères sont désactivées par défaut dans SQLite.
- **Verrous fantômes Windows Defender / indexeur :** simuler `EPERM` / `EBUSY` sur l'écriture atomique (`.tmp` puis remplacement). Attendu : 3 à 5 essais courts (20–150 ms) puis une erreur explicite, jamais un fichier tronqué ni un `.tmp` orphelin.
- **Instance unique :** lancer deux fois la même tâche planifiée ; la seconde doit refuser via un verrou OS (mutex nommé ou fichier verrouillé `msvcrt`/`flock`), et un crash ne doit pas laisser de verrou bloquant.
- **Dossier synchronisé :** refuser (ou signaler) une base WAL active placée sous OneDrive, Dropbox ou Google Drive.
- **Arrêt gracieux :** envoyer `Ctrl+C` (`SIGINT` / `CTRL_BREAK_EVENT`) pendant une écriture ; attendu : transaction terminée ou annulée, verrous relâchés, base `quick_check` à `ok`.

### 7.5 Injection de pannes aux frontières & actions irréversibles
- **Réponses menteuses :** HTTP `200 OK` contenant une page HTML (captcha, maintenance Cloudflare) au lieu du JSON ; JSON tronqué au milieu ; prix en chaîne `"19.99"` ou `null` sur un champ habituel. Attendu : rejet par schéma strict, aucune écriture.
- **Limitation :** `429` avec `Retry-After` en secondes **et** en date HTTP ; arrêt complet au 2ᵉ `429` ou `403`/challenge sur un connecteur social. Le compteur de débit est persisté en SQLite (il ne repart pas à zéro à chaque lancement).
- **Timeout POST + redémarrage (*Check-Before-Act*) :** couper le processus après l'envoi d'un POST. Au redémarrage, une opération restée `in_flight` ou `uncertain` est réconciliée en lecture côté API ou escaladée à l'humain ; **tout nouvel essai aveugle est un échec du test**.
- **Disque & base :** `SQLITE_BUSY` pendant une écriture concurrente et `EPERM` sur le fichier d'état : erreur explicite, état cohérent.
- **Garde-fous d'écriture :** `--dry-run` par défaut et `Plan -> Apply` (le plan exécuté a le même hash que le plan approuvé) ; dépasser un plafond (*Blast-Radius Caps* : nombre d'annonces par run, écart de prix > seuil, montant cumulé 24 h) doit être refusé sans flag explicite ; un canari `'Bearer ' + 'CANARY_TEST_TOKEN'` injecté dans une erreur HTTP ne doit apparaître dans aucun log (DLP runtime) ; deux rafraîchissements OAuth2 simultanés ne doivent pas invalider le jeton (verrou exclusif + écriture atomique, stockage DPAPI / Credential Manager).
- **Volet conditionnel App Web / Multi-users** (uniquement si une application web est exposée à plusieurs comptes) : `IDOR` (changer un identifiant dans l'URL ou le corps), *Mass Assignment* (`"role": "admin"`, `"isPaid": true` ajoutés au JSON), rate limiting des routes coûteuses, CORS restreint au domaine exact.
