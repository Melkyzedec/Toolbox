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
function Expect([string]$label, [string]$dir, [string]$expected) {
  $script:count++
  $out = (& powershell -NoProfile -File $scanner -ArtifactPath $dir -Fast 2>&1 | Out-String).Trim()
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
  $d = New-Case 'gros-texte'; $sb = New-Object Text.StringBuilder; [void]$sb.Append(('x' * 100 + "`n") * 105000); [IO.File]::WriteAllText((Join-Path $d 'journal.log'), $sb.ToString()); Expect 'CA-05 fichier texte > 10 Mo refusé' $d 'artifact-dlp/unscanned-text-file'
} finally {
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}
Write-Output "RÉSULTAT : $($count - $failures)/$count conformes"
if ($failures) { exit 1 }
