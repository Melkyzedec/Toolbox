param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$Mutants,
    [Parameter(Mandatory = $true)][string]$TestCommand,
    [int]$MinScore = 80
)
# Contre-test de mutation (0 USD) : chaque mutant remplace un extrait de code par une version sabotée,
# dans une COPIE temporaire du projet, puis relance les tests. Un mutant « tué » fait échouer les tests ;
# un mutant « survivant » révèle un garde-fou que les tests ne protègent pas.
# -Mutants : fichier JSON [{ "id": "...", "file": "src/x.mjs", "from": "extrait exact", "to": "remplacement" }]
# -TestCommand : commande lancée depuis la racine de la copie (ex. "npm test", "python -m unittest").
# Sortie : un résumé JSON unique ; code 1 si le score est inférieur à -MinScore ou si le témoin échoue.
$ErrorActionPreference = 'Stop'

function Invoke-Tests([string]$dir) {
    Push-Location -LiteralPath $dir
    try {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        & powershell -NoProfile -Command $TestCommand *> $null
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev; Pop-Location }
}

$projectRoot = (Get-Item -LiteralPath $Project).FullName
# Windows PowerShell 5.1 returns a JSON array as one object: unroll it explicitly.
$list = @((Get-Content -LiteralPath $Mutants -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { $_ })
if (!$list.Count) { throw 'Aucun mutant fourni.' }
$work = Join-Path ([IO.Path]::GetTempPath()) ('mutation-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$utf8 = New-Object System.Text.UTF8Encoding($false)
$results = @()
try {
    New-Item -ItemType Directory $work | Out-Null
    $tracked = @()
    if (Test-Path -LiteralPath (Join-Path $projectRoot '.git')) { $tracked = @(git -C $projectRoot ls-files) }
    if ($tracked.Count) {
        foreach ($f in $tracked) {
            $dst = Join-Path $work $f; New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
            Copy-Item -LiteralPath (Join-Path $projectRoot $f) $dst
        }
    } else {
        Get-ChildItem -LiteralPath $projectRoot -Force | Where-Object { $_.Name -notin @('.git', 'node_modules', '.venv', 'venv', '__pycache__') } |
            Copy-Item -Destination $work -Recurse -Force
    }

    if ((Invoke-Tests $work) -ne 0) {
        [pscustomobject]@{ status = 'baseline-failed'; reason = 'Les tests échouent déjà sans mutation : corrigez-les avant de mesurer.' } | ConvertTo-Json -Compress
        exit 1
    }
    foreach ($m in $list) {
        $path = Join-Path $work $m.file
        if (!(Test-Path -LiteralPath $path)) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = 'fichier absent' }; continue }
        $orig = [IO.File]::ReadAllText($path)
        if (!$orig.Contains([string]$m.from)) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = 'extrait introuvable' }; continue }
        try {
            [IO.File]::WriteAllText($path, $orig.Replace([string]$m.from, [string]$m.to), $utf8)
            $code = Invoke-Tests $work
            $results += [pscustomobject]@{ id = $m.id; result = $(if ($code -ne 0) { 'killed' } else { 'survived' }) }
        } finally { [IO.File]::WriteAllText($path, $orig, $utf8) }
    }
} finally {
    Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
}

$applied = @($results | Where-Object { $_.result -ne 'not-applied' })
$killed = @($applied | Where-Object { $_.result -eq 'killed' }).Count
$score = if ($applied.Count) { [int][Math]::Floor(100 * $killed / $applied.Count) } else { 0 }
$status = if ($applied.Count -eq $list.Count -and $score -ge $MinScore) { 'passed' } else { 'below-threshold' }
[pscustomobject]@{
    status = $status; score = $score; minScore = $MinScore; killed = $killed; applied = $applied.Count; total = $list.Count
    survivors = @($results | Where-Object { $_.result -eq 'survived' } | ForEach-Object { $_.id })
    notApplied = @($results | Where-Object { $_.result -eq 'not-applied' } | ForEach-Object { "$($_.id) ($($_.reason))" })
} | ConvertTo-Json -Compress
if ($status -ne 'passed') { exit 1 }
