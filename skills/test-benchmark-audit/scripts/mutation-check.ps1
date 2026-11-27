param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$Mutants,
    [Parameter(Mandatory = $true)][string]$TestCommand,
    [int]$MinScore = 80,
    [int]$TimeoutSec = 300
)
# Contre-test de mutation (0 USD) : chaque mutant remplace un extrait de code par une version sabotée,
# dans une COPIE temporaire du projet, puis relance les tests. Un mutant « tué » fait échouer les tests ;
# un mutant « survivant » révèle un garde-fou que les tests ne protègent pas.
# -Mutants : fichier JSON [{ "id": "...", "file": "src/x.mjs", "from": "extrait exact et unique", "to": "remplacement" }]
#            "file" est relatif à la racine du projet et doit rester dans la copie (ni chemin absolu, ni ..\ sortant).
# -TestCommand : commande lancée depuis la racine de la copie (ex. "npm test", "python -m unittest").
# -TimeoutSec : durée maximale d'un lancement des tests ; au-delà, le processus et ses enfants sont arrêtés.
#               Un mutant qui bloque les tests est compté comme détecté (« timeout ») ; un témoin trop long bloque la mesure.
# Sortie : un résumé JSON unique ; code 1 si le score est inférieur à -MinScore, si un mutant est refusé ou
# ne s'applique pas, ou si le témoin échoue.
$ErrorActionPreference = 'Stop'
if ($TimeoutSec -lt 1) { throw '-TimeoutSec doit être >= 1.' }

# Same engine as the caller (pwsh or Windows PowerShell): the test command keeps its semantics.
$engine = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName

function Stop-ProcessTree([Diagnostics.Process]$Process) {
    if ($Process.HasExited) { return }
    if ($env:OS -eq 'Windows_NT') {
        & taskkill.exe /PID $Process.Id /T /F *> $null
    } else {
        try { $Process.Kill($true) } catch { try { $Process.Kill() } catch {} }
    }
    try { $Process.WaitForExit(5000) | Out-Null } catch {}
}

# Returns @{ timedOut = $true } when the run exceeds -TimeoutSec, otherwise @{ timedOut = $false; code = <exit code> }.
function Invoke-Tests([string]$dir) {
    # The command is passed encoded so that quotes and spaces reach the child process unchanged.
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($TestCommand))
    $outLog = Join-Path $logs ([guid]::NewGuid().ToString('N') + '.out')
    $errLog = Join-Path $logs ([guid]::NewGuid().ToString('N') + '.err')
    $argLine = '-NoProfile -NonInteractive -EncodedCommand ' + $encoded
    $proc = Start-Process -FilePath $engine -ArgumentList $argLine -WorkingDirectory $dir -PassThru `
        -NoNewWindow -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $null = $proc.Handle  # keeps the exit code readable once the process has ended (Windows PowerShell 5.1)
    if (!$proc.WaitForExit($TimeoutSec * 1000)) {
        Stop-ProcessTree $proc
        return @{ timedOut = $true; code = $null }
    }
    $proc.WaitForExit()
    return @{ timedOut = $false; code = [int]$proc.ExitCode }
}

# Resolves a mutant's file inside the work copy, or returns $null when the path would leave it.
function Resolve-MutantPath([string]$rel) {
    if (!$rel -or $rel.Contains([string][char]0)) { return $null }
    try {
        if ([IO.Path]::IsPathRooted($rel) -or $rel -match '^[A-Za-z]:') { return $null }
        $full = [IO.Path]::GetFullPath([IO.Path]::Combine($workFull, $rel))
    } catch { return $null }
    if (!$full.StartsWith($workFull + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { return $null }
    return $full
}

function Get-OccurrenceCount([string]$text, [string]$needle) {
    $n = 0; $i = 0
    while (($i = $text.IndexOf($needle, $i, [StringComparison]::Ordinal)) -ge 0) { $n++; $i += $needle.Length }
    return $n
}

$projectRoot = (Get-Item -LiteralPath $Project).FullName
# Windows PowerShell 5.1 returns a JSON array as one object: unroll it explicitly.
$list = @((Get-Content -LiteralPath $Mutants -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { $_ })
if (!$list.Count) { throw 'Aucun mutant fourni.' }
$tmpBase = [IO.Path]::GetTempPath()
$work = Join-Path $tmpBase ('mutation-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$logs = Join-Path $tmpBase ('mutation-logs-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$results = @()
try {
    New-Item -ItemType Directory $work | Out-Null
    New-Item -ItemType Directory $logs | Out-Null
    $workFull = [IO.Path]::GetFullPath($work).TrimEnd([char[]]'\/')
    $tracked = @()
    if (Test-Path -LiteralPath (Join-Path $projectRoot '.git')) {
        # -z keeps non-ASCII names intact; files deleted from the working tree are skipped.
        $tracked = @(((git -C $projectRoot ls-files -z) -join '') -split [char]0 | Where-Object { $_ })
    }
    if ($tracked.Count) {
        foreach ($f in $tracked) {
            $src = Join-Path $projectRoot $f
            if (!(Test-Path -LiteralPath $src -PathType Leaf)) { continue }
            $dst = Join-Path $work $f; New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
            Copy-Item -LiteralPath $src $dst
        }
    } else {
        Get-ChildItem -LiteralPath $projectRoot -Force | Where-Object { $_.Name -notin @('.git', 'node_modules', '.venv', 'venv', '__pycache__') } |
            Copy-Item -Destination $work -Recurse -Force
    }

    $baseline = Invoke-Tests $work
    if ($baseline.timedOut) {
        [pscustomobject]@{ status = 'baseline-timeout'; reason = "Les tests dépassent $TimeoutSec s sans mutation : augmentez -TimeoutSec ou corrigez-les." } | ConvertTo-Json -Compress
        exit 1
    }
    if ($baseline.code -ne 0) {
        [pscustomobject]@{ status = 'baseline-failed'; reason = 'Les tests échouent déjà sans mutation : corrigez-les avant de mesurer.' } | ConvertTo-Json -Compress
        exit 1
    }
    foreach ($m in $list) {
        $path = Resolve-MutantPath ([string]$m.file)
        if (!$path) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = 'chemin refusé (hors de la copie)' }; continue }
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = 'fichier absent' }; continue }
        $from = [string]$m.from
        if (!$from) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = 'extrait vide' }; continue }
        $origBytes = [IO.File]::ReadAllBytes($path)
        $orig = [IO.File]::ReadAllText($path)
        $occ = Get-OccurrenceCount $orig $from
        if ($occ -eq 0) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = 'extrait introuvable' }; continue }
        if ($occ -gt 1) { $results += [pscustomobject]@{ id = $m.id; result = 'not-applied'; reason = "extrait ambigu ($occ occurrences)" }; continue }
        # Keep the original byte-order mark so that Windows PowerShell 5.1 still decodes the mutated file.
        $hasBom = $origBytes.Length -ge 3 -and $origBytes[0] -eq 0xEF -and $origBytes[1] -eq 0xBB -and $origBytes[2] -eq 0xBF
        try {
            [IO.File]::WriteAllText($path, $orig.Replace($from, [string]$m.to), (New-Object System.Text.UTF8Encoding($hasBom)))
            $run = Invoke-Tests $work
            $outcome = if ($run.timedOut) { 'timeout' } elseif ($run.code -ne 0) { 'killed' } else { 'survived' }
            $results += [pscustomobject]@{ id = $m.id; result = $outcome }
        } finally { [IO.File]::WriteAllBytes($path, $origBytes) }
    }
} finally {
    Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -LiteralPath $logs -ErrorAction SilentlyContinue
}

$applied = @($results | Where-Object { $_.result -ne 'not-applied' })
$killed = @($applied | Where-Object { $_.result -eq 'killed' -or $_.result -eq 'timeout' }).Count
$score = if ($applied.Count) { [int][Math]::Floor(100 * $killed / $applied.Count) } else { 0 }
$status = if ($applied.Count -eq $list.Count -and $score -ge $MinScore) { 'passed' } else { 'below-threshold' }
[pscustomobject]@{
    status = $status; score = $score; minScore = $MinScore; killed = $killed; applied = $applied.Count; total = $list.Count
    survivors = @($results | Where-Object { $_.result -eq 'survived' } | ForEach-Object { $_.id })
    timedOut = @($results | Where-Object { $_.result -eq 'timeout' } | ForEach-Object { $_.id })
    notApplied = @($results | Where-Object { $_.result -eq 'not-applied' } | ForEach-Object { "$($_.id) ($($_.reason))" })
} | ConvertTo-Json -Compress
if ($status -ne 'passed') { exit 1 }
