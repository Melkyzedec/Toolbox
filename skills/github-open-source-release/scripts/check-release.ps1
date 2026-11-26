param(
    [Parameter(Mandatory = $true)][string]$ArtifactPath,
    [string]$GitRoot,
    [switch]$Fast
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$MaxArchiveDepth = 3
$MaxArchiveEntries = 10000
$MaxArchiveUncompressedBytes = 524288000 # 500 MiB
$MaxScannableTextBytes = 10485760 # 10 MiB

# Dynamic pattern construction so self-scans of check-release.ps1 never trigger false positives.
$PrivateWinCursorRegex = '(?i)\b[A-Z]:' + '[\\/]+' + 'CUR' + 'SOR[\\/]+'
$PrivateWinUsersRegex = '(?i)\b[A-Z]:' + '[\\/]+' + 'Users' + '[\\/]+(?!(?:Public|Default|All\s*Users)(?:[\\/]|$))[^\s\\/:*?"<>|`''"]+'
$PrivateUnixUsersRegex = '(?:^|[\s"''`(=:])/(?:Users|home)/(?!(?:runner|ubuntu|root|devcontainer|git|node|docker|vscode)(?:/|$))[a-zA-Z0-9._-]+/'
$PrivateInternalDirRegex = '(?i)(?:^|[\s"''`(=/\\])(?!(?:~|\$HOME|%USERPROFILE%)[\\/]+)\.(?:memento' + '|codex' + '|gemini)(?:[\\/]|$)'

# Well-known credential shapes, also checked by the DLP stage so that a key hidden where gitleaks does not
# look (UTF-16 text, Office documents, renamed archives) still blocks the release.
$SecretShapeRegex = '\b(?:AK' + 'IA[0-9A-Z]{16}|gh' + '[pousr]_[A-Za-z0-9]{36,}|github_' + 'pat_[A-Za-z0-9_]{22,}|sk_' + 'live_[A-Za-z0-9]{16,}|xox' + '[abposr]-[A-Za-z0-9-]{10,}|AIza' + '[0-9A-Za-z_-]{35})|-----BEGIN [A-Z ]*PRIVATE ' + 'KEY-----'
$IbanFrRegex ='\bFR\d{2}(?:[\s-]?\d{4}){5}[\s-]?\d{3}\b'
$NirFrRegex = '\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2]|2[0-9])\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}(?:\s?\d{2})?\b'
$EmailExtractRegex = '\b([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
$AllowedEmailDomainRegex = '(?i)^(?:example\.(?:com|org|net|test|edu)|localhost|(?:[a-z0-9-]+\.)*users\.noreply\.github\.com|.*\.(?:test|example|invalid|localhost))$'

$InvisibleUnicodeRegex = '[\u200B-\u200F\u202A-\u202E\u2066-\u2069]|\uDB40[\uDC00-\uDC7F]'
$SastIexRegex = '(?i)\b(?:Invoke-' + 'Expression|i' + 'ex)\s*\(?\s*(?:Invoke-' + 'RestMethod|Invoke-' + 'WebRequest|i' + 'rm|i' + 'wr|curl|wget)\b'
$SastCurlPipeRegex = '(?i)\b(?:curl|wget)\b[^\r\n|]{1,240}\|\s*(?:ba)?sh\b'
$SastEvalAtobRegex = '(?i)\bev' + 'al\s*\(\s*(?:at' + 'ob|Buffer\.from)\s*\('
$SastFuncCtorRegex = '(?i)\bnew\s+Func' + 'tion\s*\(\s*["'']return\b|\bFunc' + 'tion\s*\(\s*["'']return\b'
$SastChildExecB64Regex = '(?i)child_' + 'process\s*\.\s*(?:exec|execSync)\s*\([^\r\n)]*(?:base64|-enc|-EncodedCommand)'

function Invoke-NativeQuiet {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $FilePath @ArgumentList 1>$null 2>$null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-NativeCaptureStdout {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $FilePath @ArgumentList 2>$null
        return @{ exitCode = $LASTEXITCODE; output = $output }
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Assert-PublicPath {
    param([string]$RelativePath)

    $entry = $RelativePath.Replace('\', '/').TrimEnd('/')
    if (!$entry -or $entry.Contains(':') -or $entry -match '(^|/)([. ]+)?(/|$)|[. ](/|$)') {
        throw 'unsafe-entry'
    }
    if ($entry -match '(?i)(^|/)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.[^/]*)?(/|$)') {
        throw 'unsafe-entry'
    }
    if ($entry -match '(?i)(^|/)(\.git|\.memento|\.local|\.gitleaksignore|\.ssh|\.aws|\.gnupg)(/|$)' -or
        $entry -match '(?i)(^|/)\.env($|\.(?!example$))|\.(sqlite3?|db|pem|pfx|key|p12|keystore|jks)(-(wal|shm|journal))?$' -or
        $entry -match '(?i)(^|/)(id_(rsa|ed25519|ecdsa|dsa)|credentials\.json|service-account[^/]*\.json|\.git-credentials|\.netrc|\.pgpass|secrets\.(ya?ml|json|toml))$') {
        throw 'private-entry'
    }
}

function Get-PublicFiles {
    param([string]$CurrentDirectory, [string]$RootDirectory)

    foreach ($item in Get-ChildItem -LiteralPath $CurrentDirectory -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked-entry' }
        $relative = $item.FullName.Substring($RootDirectory.Length).TrimStart([char[]]'/\')
        Assert-PublicPath $relative
        if ($item.PSIsContainer) {
            Get-PublicFiles $item.FullName $RootDirectory
        } else {
            $item
        }
    }
}

$TextExtensionRegex = '(?i)\.(md|markdown|rst|txt|text|log|json|jsonc|ipynb|js|mjs|cjs|ts|tsx|jsx|py|ps1|psm1|psd1|sh|bash|zsh|bat|cmd|vbs|yaml|yml|toml|ini|cfg|conf|env|csv|tsv|xml|html|htm|css|sql)$'

function Test-ZipMagic {
    param([byte[]]$Bytes)
    return ($Bytes.Length -ge 4 -and $Bytes[0] -eq 0x50 -and $Bytes[1] -eq 0x4B -and $Bytes[2] -eq 3 -and $Bytes[3] -eq 4)
}

# Returns the text to inspect, or $null for a genuine binary file. A text file is never skipped
# silently: UTF-16 is decoded, and a NUL byte in a text extension does not hide the rest of it.
function Get-ScanText {
    param([byte[]]$Bytes, [string]$Name)

    if ($null -eq $Bytes -or $Bytes.Length -eq 0) { return $null }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) { return [Text.Encoding]::Unicode.GetString($Bytes, 2, $Bytes.Length - 2) }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 0xFE -and $Bytes[1] -eq 0xFF) { return [Text.Encoding]::BigEndianUnicode.GetString($Bytes, 2, $Bytes.Length - 2) }
    $checkLen = [Math]::Min($Bytes.Length, 4096)
    $nulOdd = 0; $nulEven = 0
    for ($i = 0; $i -lt $checkLen; $i++) {
        if ($Bytes[$i] -eq 0) { if ($i % 2) { $nulOdd++ } else { $nulEven++ } }
    }
    if (($nulOdd + $nulEven) -eq 0) { return [Text.Encoding]::UTF8.GetString($Bytes) }
    if ($nulOdd -ge ($checkLen / 2) * 0.4 -and $nulEven -le $nulOdd / 10) { return [Text.Encoding]::Unicode.GetString($Bytes) }
    if ($nulEven -ge ($checkLen / 2) * 0.4 -and $nulOdd -le $nulEven / 10) { return [Text.Encoding]::BigEndianUnicode.GetString($Bytes) }
    if ($Name -match $TextExtensionRegex) { return [Text.Encoding]::UTF8.GetString($Bytes).Replace([string][char]0, '') }
    return $null
}

function Assert-SafeEmailsInLine {
    param(
        [string]$Line,
        [string]$ErrorReason = 'dlp-pii'
    )

    foreach ($m in [regex]::Matches($Line, $EmailExtractRegex)) {
        $localPart = $m.Groups[1].Value
        $domainPart = $m.Groups[2].Value
        if ($localPart -imatch '^canary_test_') { continue }
        if ($localPart -ieq 'noreply' -and $domainPart -ieq 'github.com') { continue }
        if ($domainPart -notmatch $AllowedEmailDomainRegex) {
            throw $ErrorReason
        }
    }
}

function Invoke-DlpCheckOnText {
    param([string]$Text)

    foreach ($line in ($Text -split "\r?\n")) {
        if (!$line) { continue }
        # A CANARY_TEST_ line only exempts the values that carry the marker themselves (a path such as
        # C:\Users\CANARY_TEST_user), plus fake IBAN/NIR numbers that cannot carry it. E-mail addresses
        # are always checked: only canary_test_ local parts and reserved test domains are allowed.
        $isCanaryLine = $line -imatch 'CANARY_TEST_'
        if ($line -cmatch $SecretShapeRegex) { throw 'secret-pattern' }
        foreach ($pathRegex in @($PrivateWinCursorRegex, $PrivateWinUsersRegex, $PrivateUnixUsersRegex, $PrivateInternalDirRegex)) {
            foreach ($m in [regex]::Matches($line, $pathRegex)) {
                if (!$isCanaryLine -or $m.Value -inotmatch 'CANARY_TEST_') { throw 'dlp-private-path' }
            }
        }
        if (!$isCanaryLine -and ($line -match $IbanFrRegex -or $line -match $NirFrRegex)) {
            throw 'dlp-pii'
        }
        Assert-SafeEmailsInLine -Line $line -ErrorReason 'dlp-pii'
    }
}

function Invoke-SastCheckOnText {
    param([string]$Text, [string]$RelativePath)

    if ($Text -match $InvisibleUnicodeRegex) {
        throw 'sast-invisible-unicode'
    }
    if ($RelativePath -imatch '\.(?:md|ps1|psm1|mjs|cjs|js|ts|tsx|jsx|py|sh|bash|zsh|yaml|yml|toml|json)$') {
        foreach ($line in ($Text -split "\r?\n")) {
            if (!$line -or $line -imatch 'CANARY_TEST_') { continue }
            if ($line -match $SastIexRegex -or
                $line -match $SastCurlPipeRegex -or
                $line -match $SastEvalAtobRegex -or
                $line -match $SastFuncCtorRegex -or
                $line -match $SastChildExecB64Regex) {
                throw 'sast-dangerous-pattern'
            }
        }
    }
}

function Inspect-ZipArchiveContent {
    param(
        [Parameter(Mandatory = $true)][IO.Compression.ZipArchive]$Archive,
        [int]$Depth,
        [string]$Mode
    )

    if ($Depth -gt $MaxArchiveDepth) { throw 'archive-too-deep' }
    foreach ($entry in $Archive.Entries) {
        if (!$entry.Name) { continue }
        # Entries of an archive found inside a folder have not been through the layout stage yet.
        Assert-PublicPath $entry.FullName
        if ($entry.Length -le 0) { continue }
        if ($entry.Length -gt $MaxArchiveUncompressedBytes) { throw 'archive-too-large' }
        $ms = [IO.MemoryStream]::new()
        $es = $entry.Open()
        try { $es.CopyTo($ms) } finally { $es.Dispose() }
        $bytes = $ms.ToArray()
        # Any ZIP container (.zip, Office and OpenDocument files, renamed archives) is opened by its signature.
        if (Test-ZipMagic $bytes) {
            $ms.Position = 0
            $nested = $null
            try {
                try {
                    $nested = [IO.Compression.ZipArchive]::new($ms, [IO.Compression.ZipArchiveMode]::Read, $false)
                } catch { throw 'unscannable-archive' }
                Inspect-ZipArchiveContent -Archive $nested -Depth ($Depth + 1) -Mode $Mode
            } finally {
                if ($nested) { $nested.Dispose() } else { $ms.Dispose() }
            }
            continue
        }
        $ms.Dispose()
        if ($bytes.Length -gt $MaxScannableTextBytes) {
            if ($entry.Name -match $TextExtensionRegex) { throw 'unscanned-text-file' }
            continue
        }
        $text = Get-ScanText -Bytes $bytes -Name $entry.Name
        if ($null -eq $text) { continue }
        if ($Mode -eq 'dlp') {
            Invoke-DlpCheckOnText -Text $text
        } elseif ($Mode -eq 'sast') {
            Invoke-SastCheckOnText -Text $text -RelativePath $entry.FullName
        }
    }
}

function Read-FileHead {
    param([string]$Path)
    $fs = [IO.File]::OpenRead($Path)
    try {
        $head = New-Object byte[] 4
        $n = $fs.Read($head, 0, 4)
        return $head[0..([Math]::Max(0, $n - 1))]
    } finally { $fs.Dispose() }
}

function Invoke-ArtifactContentScan {
    param($Item, [string]$Mode)

    if ($Item.PSIsContainer) {
        $files = @(Get-PublicFiles $Item.FullName $Item.FullName | Sort-Object FullName)
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        foreach ($file in $files) {
            if ($file.Length -le 0) { continue }
            $relative = $file.FullName.Substring($Item.FullName.Length).TrimStart([char[]]'/\').Replace('\', '/')
            # ZIP containers inside the folder (.zip, .docx, .xlsx, renamed archives) are opened and inspected too.
            if (Test-ZipMagic ([byte[]](Read-FileHead $file.FullName))) {
                $archive = $null
                try {
                    try { $archive = [IO.Compression.ZipFile]::OpenRead($file.FullName) } catch { throw 'unscannable-archive' }
                    Inspect-ZipArchiveContent -Archive $archive -Depth 1 -Mode $Mode
                } finally {
                    if ($archive) { $archive.Dispose() }
                }
                continue
            }
            if ($file.Length -gt $MaxScannableTextBytes) {
                # A text file too large to inspect blocks the check instead of passing in silence.
                if ($file.Name -match $TextExtensionRegex) { throw 'unscanned-text-file' }
                continue
            }
            $text = Get-ScanText -Bytes ([IO.File]::ReadAllBytes($file.FullName)) -Name $file.Name
            if ($null -eq $text) { continue }
            if ($Mode -eq 'dlp') {
                Invoke-DlpCheckOnText -Text $text
            } elseif ($Mode -eq 'sast') {
                Invoke-SastCheckOnText -Text $text -RelativePath $relative
            }
        }
        return
    }

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Item.FullName)
    try {
        Inspect-ZipArchiveContent -Archive $archive -Depth 1 -Mode $Mode
    } finally {
        $archive.Dispose()
    }
}

function Inspect-ZipArchive {
    param(
        [Parameter(Mandatory = $true)][IO.Compression.ZipArchive]$Archive,
        [int]$Depth,
        [hashtable]$Stats
    )

    if ($Depth -gt $MaxArchiveDepth) { throw 'archive-too-deep' }
    $localFiles = 0
    $entries = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $Archive.Entries) {
        $Stats.totalEntries++
        if ($Stats.totalEntries -gt $MaxArchiveEntries) { throw 'archive-too-large' }
        Assert-PublicPath $entry.FullName
        $norm = $entry.FullName.Replace('\', '/').TrimEnd('/')
        if (!$entries.Add($norm)) { throw 'duplicate-entry' }
        if ((($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'linked-entry' }
        if ($entry.Name) {
            $localFiles++
            $Stats.totalUncompressedBytes += [int64]$entry.Length
            if ($Stats.totalUncompressedBytes -gt $MaxArchiveUncompressedBytes) { throw 'archive-too-large' }
            if ($entry.Name -imatch '\.zip$') {
                $ms = [IO.MemoryStream]::new()
                $es = $entry.Open()
                try {
                    $es.CopyTo($ms)
                } finally {
                    $es.Dispose()
                }
                $ms.Position = 0
                $nested = $null
                try {
                    $nested = [IO.Compression.ZipArchive]::new($ms, [IO.Compression.ZipArchiveMode]::Read, $false)
                    Inspect-ZipArchive -Archive $nested -Depth ($Depth + 1) -Stats $Stats | Out-Null
                } finally {
                    if ($nested) { $nested.Dispose() } else { $ms.Dispose() }
                }
            }
        }
    }
    return $localFiles
}

function Get-ArtifactEvidence {
    param($Item)

    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked-artifact' }
    if ($Item.PSIsContainer) {
        $files = @(Get-PublicFiles $Item.FullName $Item.FullName | Sort-Object FullName)
        if (!$files.Count) { throw 'empty-artifact' }
        $manifest = foreach ($file in $files) {
            $relative = $file.FullName.Substring($Item.FullName.Length).TrimStart([char[]]'/\')
            [pscustomobject]@{ path = $relative.Replace('\', '/'); sha256 = (Get-FileHash -LiteralPath $file.FullName).Hash }
        }
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject @($manifest) -Compress))
            $hash = [BitConverter]::ToString($sha256.ComputeHash($bytes)).Replace('-', '')
        } finally { $sha256.Dispose() }
        return @{ sha256 = $hash; files = $files.Count; kind = 'directory' }
    }

    if ($Item.Extension -ine '.zip') { throw 'unsupported-artifact' }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Item.FullName)
    $stats = @{ totalEntries = 0; totalUncompressedBytes = [int64]0 }
    try {
        $files = Inspect-ZipArchive -Archive $archive -Depth 1 -Stats $stats
    } finally { $archive.Dispose() }
    if (!$files) { throw 'empty-artifact' }
    return @{ sha256 = (Get-FileHash -LiteralPath $Item.FullName).Hash; files = $files; kind = 'zip' }
}

function Resolve-GitleaksScanner {
    if ($env:GITLEAKS_PATH -and (Test-Path -LiteralPath $env:GITLEAKS_PATH -PathType Leaf)) {
        return (Get-Item -LiteralPath $env:GITLEAKS_PATH -Force).FullName
    }
    $localCandidates = @(
        (Join-Path $PSScriptRoot '..\.tools\gitleaks\gitleaks.exe'),
        (Join-Path $PSScriptRoot '..\..\.tools\gitleaks\gitleaks.exe')
    )
    foreach ($candidate in $localCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Get-Item -LiteralPath $candidate -Force).FullName
        }
    }
    $cmd = Get-Command gitleaks -CommandType Application -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        return $cmd.Source
    }
    throw 'scanner-unavailable'
}

function Invoke-SecretScan {
    param([string]$Mode, [string]$Target, [string[]]$Options = @())

    # Never forward findings, excerpts or paths to the assistant's output.
    $flags = @(
        '--no-banner', '--redact=100', '--ignore-gitleaks-allow', '--exit-code=23',
        '--gitleaks-ignore-path', (Join-Path $PSScriptRoot 'no-local-exceptions'),
        '--config', $config, '--max-archive-depth=3', '--max-decode-depth=2', '--timeout=120'
    )
    $allArgs = @($Mode) + $flags + $Options + @('--', $Target)
    $code = Invoke-NativeQuiet -FilePath $scanner -ArgumentList $allArgs
    if ($code -eq 23) { throw 'secret-pattern' }
    if ($code -ne 0) { throw 'scanner-error' }
}

$stage = 'artifact-layout'
try {
    $artifact = Get-Item -LiteralPath $ArtifactPath -Force
    $evidence = Get-ArtifactEvidence $artifact

    $stage = 'scanner-setup'
    $scanner = Resolve-GitleaksScanner
    $config = Join-Path $PSScriptRoot 'release-secrets.toml'
    if (!(Test-Path -LiteralPath $config -PathType Leaf)) { throw 'missing-policy' }

    $stage = 'artifact-dlp'
    Invoke-ArtifactContentScan -Item $artifact -Mode 'dlp'

    $stage = 'artifact-sast'
    Invoke-ArtifactContentScan -Item $artifact -Mode 'sast'

    $stage = 'artifact-secrets'
    Invoke-SecretScan -Mode dir -Target $artifact.FullName

    $gitHistory = if ($Fast) { 'skipped-fast' } else { 'not-requested' }
    if ($GitRoot -and !$Fast) {
        $stage = 'git-layout'
        $repo = (Get-Item -LiteralPath $GitRoot -Force).FullName
        $shallowRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList @('--no-optional-locks', '-C', $repo, 'rev-parse', '--is-shallow-repository')
        if ($shallowRes.exitCode -ne 0 -or ([string]($shallowRes.output -join '')).Trim() -ne 'false') { throw 'invalid-or-shallow-git' }
        $topRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList @('--no-optional-locks', '-C', $repo, 'rev-parse', '--show-toplevel')
        $gitTop = ([string]($topRes.output -join '')).Trim()
        if ($topRes.exitCode -ne 0 -or !$gitTop -or [IO.Path]::GetFullPath($gitTop) -ine $repo) { throw 'invalid-git-root' }
        if (Test-Path -LiteralPath (Join-Path $repo '.gitleaksignore')) { throw 'project-scan-exceptions' }
        $trackedRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList @('--no-optional-locks', '-C', $repo, 'ls-files', '-z')
        if ($trackedRes.exitCode -ne 0) { throw 'git-files-unavailable' }
        foreach ($entry in (($trackedRes.output -join '') -split [char]0)) {
            if ($entry) { Assert-PublicPath $entry }
        }
        $stage = 'git-identity'
        $gitLogRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList @('--no-optional-locks', '-C', $repo, 'log', '--all', '--format=%ae%n%ce%n%B')
        if ($gitLogRes.exitCode -ne 0) { throw 'git-history-unavailable' }
        foreach ($logLine in @($gitLogRes.output)) {
            $lineStr = [string]$logLine
            if (!$lineStr) { continue }
            Assert-SafeEmailsInLine -Line $lineStr -ErrorReason 'git-personal-email'
        }
        $stage = 'git-secrets'
        Invoke-SecretScan -Mode git -Target $repo -Options @('--log-opts=--all --full-history --no-ext-diff --no-textconv')
        $gitHistory = 'checked'
    }

    $stage = 'artifact-integrity'
    if ((Get-ArtifactEvidence (Get-Item -LiteralPath $ArtifactPath -Force)).sha256 -ne $evidence.sha256) {
        throw 'artifact-changed'
    }
    $scanMode = if ($Fast) { 'fast' } else { 'full' }
    [pscustomobject]@{
        status = 'passed'; mode = $scanMode; fastMode = [bool]$Fast; artifact = $evidence.kind
        files = $evidence.files; sha256 = $evidence.sha256; gitHistory = $gitHistory
    } | ConvertTo-Json -Compress
} catch {
    $knownReasons = @(
        'secret-pattern', 'dlp-private-path', 'dlp-pii', 'sast-invisible-unicode',
        'sast-dangerous-pattern', 'private-entry', 'unsafe-entry', 'duplicate-entry',
        'linked-entry', 'linked-artifact', 'empty-artifact', 'unsupported-artifact',
        'archive-too-large', 'archive-too-deep', 'missing-policy', 'scanner-unavailable',
        'scanner-error', 'invalid-or-shallow-git', 'invalid-git-root',
        'project-scan-exceptions', 'git-files-unavailable', 'git-history-unavailable',
        'git-personal-email', 'artifact-changed', 'unscannable-archive', 'unscanned-text-file'
    )
    $msg = [string]$_.Exception.Message
    $reason = if ($knownReasons -contains $msg) { $msg } else { 'check-unavailable-or-invalid-input' }
    [pscustomobject]@{ status = 'blocked'; stage = $stage; reason = $reason } | ConvertTo-Json -Compress
    exit 1
}
