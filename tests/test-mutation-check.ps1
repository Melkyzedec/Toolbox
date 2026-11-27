# Tests de mutation-check.ps1 (skill test-benchmark-audit), sans dépendance externe ni appel payant.
# Usage : pwsh -NoProfile -File tests/test-mutation-check.ps1 (ou powershell -NoProfile -File ... sous Windows PowerShell 5.1)
$ErrorActionPreference = 'Stop'
$tool = Join-Path $PSScriptRoot '..\skills\test-benchmark-audit\scripts\mutation-check.ps1'
$engine = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
$root = Join-Path ([IO.Path]::GetTempPath()) ('ct-mutation-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$utf8 = New-Object System.Text.UTF8Encoding($false)
$failures = 0; $count = 0
function W([string]$path, [string]$text) { New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null; [IO.File]::WriteAllText($path, $text, $utf8) }
function Check([string]$label, [bool]$ok, [string]$detail) {
  $script:count++
  if ($ok) { Write-Output "PASS $label" } else { $script:failures++; Write-Output "FAIL $label : $detail" }
}
function Run([string]$project, [string]$mutants, [string]$cmd, [string[]]$extra = @()) {
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try { $out = (& $engine -NoProfile -File $tool -Project $project -Mutants $mutants -TestCommand $cmd @extra 2>&1 | Out-String).Trim(); $code = $LASTEXITCODE }
  finally { $ErrorActionPreference = $prev; $sw.Stop() }
  $json = $out -split "`n" | Where-Object { $_ -match '^\{' } | Select-Object -Last 1
  if (!$json) { Write-Host "Sortie inattendue : $out" }
  $obj = $null; try { $obj = $json | ConvertFrom-Json } catch {}
  return @{ code = $code; json = $obj; seconds = $sw.Elapsed.TotalSeconds; out = $out }
}
function Get-TreeHash([string]$dir) {
  $lines = Get-ChildItem -LiteralPath $dir -Recurse -File -Force | Sort-Object FullName |
    ForEach-Object { $_.FullName.Substring($dir.Length) + '=' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
  return ($lines -join "`n")
}

try {
  # Projet fictif : une règle « refuser les montants négatifs » et une limite haute non testée.
  $p = Join-Path $root 'projet'
  W (Join-Path $p 'regle.ps1') "function Test-Montant([int]`$m) { if (`$m -lt 0) { return `$false }; if (`$m -gt 1000) { return `$false }; return `$true }`n"
  W (Join-Path $p 'test.ps1') ". `"`$PSScriptRoot\regle.ps1`"`nif (Test-Montant -5) { exit 1 }`nif (-not (Test-Montant 10)) { exit 1 }`nexit 0`n"
  $mutants = Join-Path $root 'mutants.json'
  W $mutants '[{"id":"negatif-accepte","file":"regle.ps1","from":"if ($m -lt 0) { return $false }; ","to":""},{"id":"plafond-retire","file":"regle.ps1","from":"if ($m -gt 1000) { return $false }; ","to":""}]'
  $cmd = 'powershell -NoProfile -File .\test.ps1; exit $LASTEXITCODE'
  if ($engine -notmatch '(?i)powershell(\.exe)?$') { $cmd = '& "' + $engine + '" -NoProfile -File ./test.ps1; exit $LASTEXITCODE' }
  $hashAvant = Get-TreeHash $p

  $r = Run $p $mutants $cmd
  Check 'un survivant fait tomber le score sous le seuil' ($r.code -eq 1 -and $r.json.score -eq 50 -and $r.json.status -eq 'below-threshold' -and $r.json.applied -eq 2) "code=$($r.code) score=$($r.json.score) applied=$($r.json.applied)"
  Check 'le survivant est nommé' (@($r.json.survivors) -contains 'plafond-retire') "survivants=$($r.json.survivors -join ',')"
  # M-07 : contrôle placé après un scénario où des mutants ont réellement été appliqués
  Check 'le projet d''origine n''est jamais modifié (mutants appliqués)' ((Get-TreeHash $p) -eq $hashAvant) 'empreintes du projet modifiées'

  W (Join-Path $p 'test.ps1') ". `"`$PSScriptRoot\regle.ps1`"`nif (Test-Montant -5) { exit 1 }`nif (Test-Montant 5000) { exit 1 }`nif (-not (Test-Montant 10)) { exit 1 }`nexit 0`n"
  $hashAvant = Get-TreeHash $p
  $r = Run $p $mutants $cmd
  Check 'des tests complets tuent tous les mutants' ($r.code -eq 0 -and $r.json.score -eq 100 -and $r.json.status -eq 'passed') "code=$($r.code) score=$($r.json.score)"
  Check 'le projet d''origine reste identique après 2 mutants tués' ((Get-TreeHash $p) -eq $hashAvant) 'empreintes du projet modifiées'

  W (Join-Path $root 'introuvable.json') '[{"id":"faux","file":"regle.ps1","from":"texte absent","to":"x"}]'
  $r = Run $p (Join-Path $root 'introuvable.json') $cmd
  Check 'un mutant qui ne s''applique pas est signalé, pas compté comme tué' ($r.code -eq 1 -and $r.json.applied -eq 0) "code=$($r.code) applied=$($r.json.applied)"

  # M-07 : un mutant ne peut jamais écrire hors de la copie temporaire
  $victim = Join-Path $root 'victime.ps1'
  W $victim "CANARY_TEST_original`n"
  $obs = Join-Path $root 'observations.txt'
  $obsCmd = "Add-Content -LiteralPath '$obs' -Value (Get-Content -Raw -LiteralPath '$victim'); exit 0"
  $rootLeaf = Split-Path $root -Leaf
  $escapes = @(
    @{ id = 'relatif-sortant'; file = "..\$rootLeaf\victime.ps1" },
    @{ id = 'relatif-sortant-slash'; file = "../$rootLeaf/victime.ps1" },
    @{ id = 'absolu'; file = $victim },
    @{ id = 'profond'; file = "sous\..\..\..\$rootLeaf\victime.ps1" }
  )
  $mEsc = Join-Path $root 'evasion.json'
  W $mEsc (ConvertTo-Json -Compress -InputObject @($escapes | ForEach-Object { @{ id = $_.id; file = $_.file; from = 'CANARY_TEST_original'; to = 'CANARY_TEST_sabote' } }))
  $r = Run $p $mEsc $obsCmd
  $seen = if (Test-Path -LiteralPath $obs) { Get-Content -Raw -LiteralPath $obs } else { '' }
  Check 'M-07 chemins sortants refusés et signalés' ($r.code -eq 1 -and $r.json.applied -eq 0 -and @($r.json.notApplied).Count -eq 4 -and (@($r.json.notApplied) -join ' ') -match 'chemin refusé') "out=$($r.out)"
  Check 'M-07 aucune écriture hors de la copie, même temporaire' (-not $seen.Contains('CANARY_TEST_sabote') -and (Get-Content -Raw -LiteralPath $victim).Contains('CANARY_TEST_original')) "observé=$seen"

  # M-07 : extrait vide et extrait ambigu refusés sans faire planter l'outil
  W (Join-Path $root 'vide.json') '[{"id":"vide","file":"regle.ps1","from":"","to":"x"},{"id":"ambigu","file":"regle.ps1","from":"return","to":"x"}]'
  $r = Run $p (Join-Path $root 'vide.json') $cmd
  $na = @($r.json.notApplied) -join ' '
  Check 'M-07 extrait vide signalé (JSON produit)' ($r.code -eq 1 -and $na -match 'vide \(extrait vide\)') "out=$($r.out)"
  Check 'M-07 extrait ambigu signalé' ($na -match 'ambigu \(extrait ambigu') "out=$($r.out)"

  # M-07 : un mutant qui bloque les tests est arrêté après -TimeoutSec et compté comme détecté
  $q = Join-Path $root 'projet-boucle'
  W (Join-Path $q 'regle.ps1') "function Get-Pas { return 1 }`n"
  W (Join-Path $q 'test.ps1') ". `"`$PSScriptRoot\regle.ps1`"`n`$i = 0; while (`$i -lt 3) { `$i += (Get-Pas) }`nexit 0`n"
  $mLoop = Join-Path $root 'boucle.json'
  W $mLoop '[{"id":"pas-nul","file":"regle.ps1","from":"return 1","to":"return 0"}]'
  $r = Run $q $mLoop $cmd @('-TimeoutSec', '15')
  Check 'M-07 -TimeoutSec arrête un mutant bloquant' ($r.code -eq 0 -and @($r.json.timedOut) -contains 'pas-nul' -and $r.json.score -eq 100 -and $r.seconds -lt 60) "code=$($r.code) durée=$([int]$r.seconds)s out=$($r.out)"

  W (Join-Path $q 'test.ps1') "while (`$true) { Start-Sleep -Milliseconds 200 }`n"
  $r = Run $q $mLoop $cmd @('-TimeoutSec', '3')
  Check 'M-07 témoin trop long : baseline-timeout' ($r.code -eq 1 -and $r.json.status -eq 'baseline-timeout' -and $r.seconds -lt 60) "code=$($r.code) out=$($r.out)"

  W (Join-Path $p 'test.ps1') "exit 1`n"
  $r = Run $p $mutants $cmd
  Check 'des tests déjà en échec bloquent la mesure' ($r.code -eq 1 -and $r.json.status -eq 'baseline-failed') "code=$($r.code) status=$($r.json.status)"
} finally {
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}
Write-Output "RÉSULTAT : $($count - $failures)/$count conformes"
if ($failures) { exit 1 }
