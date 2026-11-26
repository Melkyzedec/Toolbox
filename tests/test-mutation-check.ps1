# Tests de mutation-check.ps1 (skill test-benchmark-audit), sans dépendance externe ni appel payant.
$ErrorActionPreference = 'Stop'
$tool = Join-Path $PSScriptRoot '..\skills\test-benchmark-audit\scripts\mutation-check.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) ('ct-mutation-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$utf8 = New-Object System.Text.UTF8Encoding($false)
$failures = 0; $count = 0
function W([string]$path, [string]$text) { New-Item -ItemType Directory -Force (Split-Path $path) | Out-Null; [IO.File]::WriteAllText($path, $text, $utf8) }
function Check([string]$label, [bool]$ok, [string]$detail) {
  $script:count++
  if ($ok) { Write-Output "PASS $label" } else { $script:failures++; Write-Output "FAIL $label : $detail" }
}
function Run([string]$project, [string]$mutants, [string]$cmd) {
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { $out = (& powershell -NoProfile -File $tool -Project $project -Mutants $mutants -TestCommand $cmd 2>&1 | Out-String).Trim(); $code = $LASTEXITCODE }
  finally { $ErrorActionPreference = $prev }
  $json = $out -split "`n" | Where-Object { $_ -match '^\{' } | Select-Object -Last 1
  if (!$json) { Write-Host "Sortie inattendue : $out" }
  return @{ code = $code; json = ($json | ConvertFrom-Json) }
}

try {
  # Projet fictif : une règle « refuser les montants négatifs » et une limite haute non testée.
  $p = Join-Path $root 'projet'
  W (Join-Path $p 'regle.ps1') "function Test-Montant([int]`$m) { if (`$m -lt 0) { return `$false }; if (`$m -gt 1000) { return `$false }; return `$true }`n"
  W (Join-Path $p 'test.ps1') ". `"`$PSScriptRoot\regle.ps1`"`nif (Test-Montant -5) { exit 1 }`nif (-not (Test-Montant 10)) { exit 1 }`nexit 0`n"
  $mutants = Join-Path $root 'mutants.json'
  W $mutants '[{"id":"negatif-accepte","file":"regle.ps1","from":"if ($m -lt 0) { return $false }; ","to":""},{"id":"plafond-retire","file":"regle.ps1","from":"if ($m -gt 1000) { return $false }; ","to":""}]'
  $cmd = 'powershell -NoProfile -File .\test.ps1; exit $LASTEXITCODE'

  $r = Run $p $mutants $cmd
  Check 'un survivant fait tomber le score sous le seuil' ($r.code -eq 1 -and $r.json.score -eq 50 -and $r.json.status -eq 'below-threshold') "code=$($r.code) score=$($r.json.score)"
  Check 'le survivant est nommé' (@($r.json.survivors) -contains 'plafond-retire') "survivants=$($r.json.survivors -join ',')"

  W (Join-Path $p 'test.ps1') ". `"`$PSScriptRoot\regle.ps1`"`nif (Test-Montant -5) { exit 1 }`nif (Test-Montant 5000) { exit 1 }`nif (-not (Test-Montant 10)) { exit 1 }`nexit 0`n"
  $r = Run $p $mutants $cmd
  Check 'des tests complets tuent tous les mutants' ($r.code -eq 0 -and $r.json.score -eq 100 -and $r.json.status -eq 'passed') "code=$($r.code) score=$($r.json.score)"

  W (Join-Path $root 'introuvable.json') '[{"id":"faux","file":"regle.ps1","from":"texte absent","to":"x"}]'
  $r = Run $p (Join-Path $root 'introuvable.json') $cmd
  Check 'un mutant qui ne s''applique pas est signalé, pas compté comme tué' ($r.code -eq 1 -and $r.json.applied -eq 0) "code=$($r.code) applied=$($r.json.applied)"

  W (Join-Path $p 'test.ps1') "exit 1`n"
  $r = Run $p $mutants $cmd
  Check 'des tests déjà en échec bloquent la mesure' ($r.code -eq 1 -and $r.json.status -eq 'baseline-failed') "code=$($r.code) status=$($r.json.status)"
  Check 'le projet d''origine n''est jamais modifié' ((Get-Content -Raw (Join-Path $p 'regle.ps1')).Contains('$m -lt 0')) 'regle.ps1 modifié'
} finally {
  Remove-Item -Recurse -Force -LiteralPath $root -ErrorAction SilentlyContinue
}
Write-Output "RÉSULTAT : $($count - $failures)/$count conformes"
if ($failures) { exit 1 }
