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
$MaxBufferedEntryBytes = 104857600 # 100 MiB: largest archive entry held in memory (nested archive or text)

# Dynamic pattern construction so self-scans of check-release.ps1 never trigger false positives.
$PrivateWinCursorRegex = '(?i)\b[A-Z]:' + '[\\/]+' + 'CUR' + 'SOR[\\/]+'
$PrivateWinUsersRegex = '(?i)\b[A-Z]:' + '[\\/]+' + 'Users' + '[\\/]+(?!(?:Public|Default|All\s*Users)(?:[\\/]|$))[^\s\\/:*?"<>|`''"]+'
$PrivateUnixUsersRegex = '(?:^|[\s"''`(=:])/(?:Users|home)/(?!(?:runner|ubuntu|root|devcontainer|git|node|docker|vscode)(?:/|$))[a-zA-Z0-9._-]+/'
# Internal assistant folders (memento, codex, gemini) placed under a concrete parent folder. A bare folder
# name (a .gitignore entry, a relative mention) and generic home placeholders (~, $HOME, %USERPROFILE%) stay allowed.
$PrivateInternalDirRegex = '(?i)(?<!(?:~|\$HOME|%USERPROFILE%|\$env:USERPROFILE)[\\/]+)(?<=[\w-][\\/]+)\.(?:memento' + '|codex' + '|gemini)(?:[\\/]|$)'
# Personal folders on any drive (client files, synced clouds, documents) and named network shares.
$PrivateWinPersonalDirRegex = '(?i)\b[A-Z]:[\\/]+(?:[^\\/\r\n"''`<>|:*?]+[\\/]+){0,6}?(?:Clients?|OneDrive[^\\/\r\n]*|Dropbox|Google Drive|Documents|Mes documents|Desktop|Bureau|Downloads|T[ée]l[ée]chargements|Factures|Paie)(?=[\\/]|$|[\s"''`])'
$PrivateUncShareRegex = '(?i)(?<![\\\w])\\\\(?!(?:localhost|127\.0\.0\.1|wsl\$|wsl\.localhost|server|serveur|host|machine|nas|example[\w.-]*)\\)[A-Za-z0-9][A-Za-z0-9._-]{1,62}\\[^\\\s]+'
# A credential written as a literal value: `password = '...'`, "api_key": "...", and in configuration files
# (YAML, INI, .npmrc, .pypirc...) also an unquoted `secret: value`. Placeholders and environment lookups pass.
$CredentialKeyPattern = '[\w.-]*(?:pass' + 'word|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret|_auth)'
$HardcodedQuotedRegex = '(?i)(?<![\w])["'']?' + $CredentialKeyPattern + '["'']?\s*[:=]\s*["''](?<v>[^"''\s]{6,})["'']'
$HardcodedBareRegex = '(?i)(?<![\w])' + $CredentialKeyPattern + '\s*[:=]\s*(?<v>[^\s"''#;,]{6,})\s*$'
$ConfigFileNameRegex = '(?i)(?:\.(?:ya?ml|ini|cfg|conf|toml|properties|env)$|^\.(?:npmrc|pypirc|netrc)$)'
$CredentialPlaceholderRegex = '^(?:\$\{|\$\(|\{\{|<|%|\$[A-Z_]|os\.|process\.env|env\(|getenv|changeme|change_me|x{3,}|\*{3,}|example|exemple|placeholder|your[_-]|votre|dummy|fake|redacted|null|none|true|false|undefined)'
$PhoneFrRegex ='(?<![\d+])(?:\+33\s?[67]|0[67])(?:[\s.-]?\d{2}){4}(?!\d)'

# Well-known credential shapes, also checked by the DLP stage so that a key hidden where gitleaks does not
# look (UTF-16 text, Office documents, renamed archives) still blocks the release.
$SecretShapeRegex = '\b(?:AK' + 'IA[0-9A-Z]{16}|gh' + '[pousr]_[A-Za-z0-9]{36,}|github_' + 'pat_[A-Za-z0-9_]{22,}|sk_' + 'live_[A-Za-z0-9]{16,}|xox' + '[abposr]-[A-Za-z0-9-]{10,}|AIza' + '[0-9A-Za-z_-]{35})|-----BEGIN [A-Z ]*PRIVATE ' + 'KEY-----'
$IbanFrRegex ='\bFR\d{2}(?:[\s-]?\d{4}){5}[\s-]?\d{3}\b'
$NirFrRegex = '\b[12]\s?\d{2}\s?(?:0[1-9]|1[0-2]|2[0-9])\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}(?:\s?\d{2})?\b'
$EmailExtractRegex = '\b([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
$AllowedEmailDomainRegex = '(?i)^(?:example\.(?:com|org|net|test|edu)|localhost|(?:[a-z0-9-]+\.)*users\.noreply\.github\.com|.*\.(?:test|example|invalid|localhost))$'

$InvisibleUnicodeRegex = '[\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]|\uDB40[\uDC00-\uDC7F]'
# A word mixing Latin and Cyrillic letters in code (a look-alike identifier such as "p\u0430ssword").
$HomoglyphRegex = '[A-Za-z][\u0400-\u04FF]|[\u0400-\u04FF][A-Za-z]'
$SastCodeExtensionRegex = '(?i)\.(?:ps1|psm1|psd1|mjs|cjs|js|ts|tsx|jsx|py|sh|bash|zsh|bat|cmd|vbs|wsf)$'
$SastDownloadExecRegex = '(?i)\b(?:I' + 'EX|Invoke-' + 'Expression)\b[^\r\n]{0,200}\bDownload(?:String|Data)\b|\bDownload' + 'String\s*\([^\r\n]{0,200}\|\s*(?:I' + 'EX|Invoke-' + 'Expression)\b'
$SastScriptblockRegex = '(?i)\[script' + 'block\]::Create\s*\([^\r\n]{0,200}(?:\bi' + 'wr\b|\bi' + 'rm\b|Invoke-WebRequest|Invoke-RestMethod|Download' + 'String|\bcurl\b|\bwget\b)'
$SastPyExecRegex = '(?i)\b(?:ex' + 'ec|ev' + 'al)\s*\([^\r\n]{0,200}(?:url' + 'open|requests\.get|urllib)'
$SastEncodedCmdRegex = '(?i)\b(?:powershell|pwsh)(?:\.exe)?\b[^\r\n]{0,80}\s-(?:Encoded' + 'Command|enc|ec|e)\s+[A-Za-z0-9+/=]{16,}'
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
        $entry -match '(?i)(^|/)\.env($|\.(?!example$))|\.(sqlite3?|db|pem|pfx|key|p12|keystore|jks|kdbx|ovpn)(-(wal|shm|journal))?$' -or
        $entry -match '(?i)\.tfstate(\.backup)?$|(^|/)(\.docker/config\.json|\.kube/config|\.htpasswd)$' -or
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
        # A CANARY_TEST_ local part does not make a real mailbox fictitious: only reserved domains pass.
        if ($localPart -ieq 'noreply' -and $domainPart -ieq 'github.com') { continue }
        if ($domainPart -notmatch $AllowedEmailDomainRegex) {
            throw $ErrorReason
        }
    }
}

function Assert-NoHardcodedCredential {
    param([string]$Line, [string]$Name)

    $patterns = @($HardcodedQuotedRegex)
    if ($Name -and $Name -match $ConfigFileNameRegex) { $patterns += $HardcodedBareRegex }
    foreach ($re in $patterns) {
        foreach ($m in [regex]::Matches($Line, $re)) {
            $value = $m.Groups['v'].Value
            if (!$value -or $value -imatch $CredentialPlaceholderRegex -or $value -imatch 'CANARY_TEST_') { continue }
            if (($value.ToCharArray() | Select-Object -Unique).Count -le 2) { continue }
            throw 'hardcoded-credential'
        }
    }
}

function Invoke-DlpCheckOnText {
    param([string]$Text, [string]$Name)

    foreach ($line in ($Text -split "\r?\n")) {
        if (!$line) { continue }
        # The CANARY_TEST_ marker never exempts a whole line. Only a matched value that carries the marker
        # itself (a path such as C:\Users\CANARY_TEST_user) is exempted. IBAN, NIR and phone numbers cannot
        # carry it: test fixtures must build such values at run time instead of storing them.
        if ($line -cmatch $SecretShapeRegex) { throw 'secret-pattern' }
        foreach ($pathRegex in @($PrivateWinCursorRegex, $PrivateWinUsersRegex, $PrivateUnixUsersRegex, $PrivateInternalDirRegex, $PrivateWinPersonalDirRegex, $PrivateUncShareRegex)) {
            foreach ($m in [regex]::Matches($line, $pathRegex)) {
                if ($m.Value -inotmatch 'CANARY_TEST_') { throw 'dlp-private-path' }
            }
        }
        if ($line -match $IbanFrRegex -or $line -match $NirFrRegex -or $line -match $PhoneFrRegex) {
            throw 'dlp-pii'
        }
        Assert-SafeEmailsInLine -Line $line -ErrorReason 'dlp-pii'
        Assert-NoHardcodedCredential -Line $line -Name $Name
    }
}

function Invoke-SastCheckOnText {
    param([string]$Text, [string]$RelativePath)

    # A byte-order mark is only legitimate as the very first character of a file.
    if ($Text.Length -gt 0 -and $Text[0] -eq [char]0xFEFF) { $Text = $Text.Substring(1) }
    if ($Text -match $InvisibleUnicodeRegex) {
        throw 'sast-invisible-unicode'
    }
    if ($RelativePath -match $SastCodeExtensionRegex -and $Text -match $HomoglyphRegex) {
        throw 'sast-homoglyph'
    }
    if ($RelativePath -imatch '\.(?:md|ps1|psm1|psd1|mjs|cjs|js|ts|tsx|jsx|py|sh|bash|zsh|bat|cmd|vbs|wsf|yaml|yml|toml|json)$') {
        # Scanner fixtures may hold a dangerous pattern only under tests/fixtures/ and only on a line that
        # carries the CANARY_TEST_ marker. Everywhere else the marker exempts nothing.
        $isFixture = $RelativePath.Replace('\', '/') -imatch '^tests/fixtures/'
        foreach ($line in ($Text -split "\r?\n")) {
            if (!$line) { continue }
            if ($isFixture -and $line -imatch 'CANARY_TEST_') { continue }
            if ($line -match $SastIexRegex -or
                $line -match $SastCurlPipeRegex -or
                $line -match $SastEvalAtobRegex -or
                $line -match $SastFuncCtorRegex -or
                $line -match $SastChildExecB64Regex -or
                $line -match $SastDownloadExecRegex -or
                $line -match $SastScriptblockRegex -or
                $line -match $SastPyExecRegex -or
                $line -match $SastEncodedCmdRegex) {
                throw 'sast-dangerous-pattern'
            }
        }
    }
}

function New-ArchiveStats {
    return @{ totalEntries = 0; declaredBytes = [int64]0; readBytes = [int64]0 }
}

function Test-ZipSymlinkEntry {
    param([IO.Compression.ZipArchiveEntry]$Entry)
    return ((($Entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000)
}

# Reads one archive entry with a hard bound. Returns the bytes when the entry fits in memory, otherwise
# $null with its first bytes, and enforces both the declared size and the cumulative read budget.
function Read-ZipEntryBounded {
    param([IO.Compression.ZipArchiveEntry]$Entry, [hashtable]$Stats)

    $buffer = New-Object byte[] 81920
    $ms = [IO.MemoryStream]::new()
    $head = New-Object byte[] 0
    $kept = $true
    $total = [int64]0
    $es = $Entry.Open()
    try {
        while (($n = $es.Read($buffer, 0, $buffer.Length)) -gt 0) {
            if ($total -eq 0) { $head = $buffer[0..([Math]::Min($n, 4) - 1)] }
            $total += $n
            $Stats.readBytes += $n
            # A stream longer than its declared size is a forged header (zip bomb).
            if ($total -gt $Entry.Length) { throw 'archive-too-large' }
            if ($Stats.readBytes -gt $MaxArchiveUncompressedBytes) { throw 'archive-too-large' }
            if ($kept) {
                if (($ms.Length + $n) -gt $MaxBufferedEntryBytes) {
                    $kept = $false
                    $ms.Dispose()
                } else {
                    $ms.Write($buffer, 0, $n)
                }
            }
        }
    } finally { $es.Dispose() }
    $bytes = $null
    if ($kept) { $bytes = $ms.ToArray(); $ms.Dispose() }
    return @{ bytes = $bytes; head = [byte[]]$head; length = $total }
}

# Single inspection of an archive, whatever its origin (the artifact itself, a .zip or renamed archive
# inside a folder, or an archive nested in another). Structure rules always apply; content rules depend
# on $Mode ('layout', 'dlp' or 'sast'). Returns the number of file entries of this archive level.
function Inspect-ZipArchive {
    param(
        [Parameter(Mandatory = $true)][IO.Compression.ZipArchive]$Archive,
        [int]$Depth,
        [hashtable]$Stats,
        [string]$Mode
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
        if (Test-ZipSymlinkEntry $entry) { throw 'linked-entry' }
        if ($Mode -eq 'dlp') { Assert-SafeEmailsInLine -Line $entry.FullName -ErrorReason 'dlp-pii' }
        if (!$entry.Name) { continue }
        $localFiles++
        $Stats.declaredBytes += [int64]$entry.Length
        if ($Stats.declaredBytes -gt $MaxArchiveUncompressedBytes) { throw 'archive-too-large' }
        if ($entry.Length -le 0) { continue }

        $read = Read-ZipEntryBounded -Entry $entry -Stats $Stats
        # Any ZIP container (.zip, Office and OpenDocument files, renamed archives) is opened by its signature.
        if (Test-ZipMagic $read.head) {
            if ($null -eq $read.bytes) { throw 'archive-too-large' }
            $nestedStream = [IO.MemoryStream]::new($read.bytes, $false)
            $nested = $null
            try {
                try {
                    $nested = [IO.Compression.ZipArchive]::new($nestedStream, [IO.Compression.ZipArchiveMode]::Read, $false)
                } catch { throw 'unscannable-archive' }
                Inspect-ZipArchive -Archive $nested -Depth ($Depth + 1) -Stats $Stats -Mode $Mode | Out-Null
            } finally {
                if ($nested) { $nested.Dispose() } else { $nestedStream.Dispose() }
            }
            continue
        }
        if ($Mode -eq 'layout') { continue }
        if ($null -eq $read.bytes -or $read.length -gt $MaxScannableTextBytes) {
            if ($entry.Name -match $TextExtensionRegex) { throw 'unscanned-text-file' }
            continue
        }
        $text = Get-ScanText -Bytes $read.bytes -Name $entry.Name
        if ($null -eq $text) { continue }
        if ($Mode -eq 'dlp') {
            Invoke-DlpCheckOnText -Text $text -Name $entry.Name
        } elseif ($Mode -eq 'sast') {
            Invoke-SastCheckOnText -Text $text -RelativePath $entry.FullName
        }
    }
    return $localFiles
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

function Open-ZipFileOrBlock {
    param([string]$Path)
    try { return [IO.Compression.ZipFile]::OpenRead($Path) } catch { throw 'unscannable-archive' }
}

# One pass over the artifact for a given mode. Archives found anywhere go through Inspect-ZipArchive,
# so a folder artifact gets the same structural checks on its archives as a .zip artifact.
function Invoke-ArtifactScan {
    param($Item, [string]$Mode)

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stats = New-ArchiveStats
    if ($Item.PSIsContainer) {
        $files = @(Get-PublicFiles $Item.FullName $Item.FullName | Sort-Object FullName)
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($Item.FullName.Length).TrimStart([char[]]'/\').Replace('\', '/')
            # A personal address in a file name is published as surely as one in the file content.
            if ($Mode -eq 'dlp') { Assert-SafeEmailsInLine -Line $relative -ErrorReason 'dlp-pii' }
            if ($file.Length -le 0) { continue }
            if (Test-ZipMagic ([byte[]](Read-FileHead $file.FullName))) {
                $archive = Open-ZipFileOrBlock $file.FullName
                try {
                    Inspect-ZipArchive -Archive $archive -Depth 1 -Stats $stats -Mode $Mode | Out-Null
                } finally { $archive.Dispose() }
                continue
            }
            if ($Mode -eq 'layout') { continue }
            if ($file.Length -gt $MaxScannableTextBytes) {
                # A text file too large to inspect blocks the check instead of passing in silence.
                if ($file.Name -match $TextExtensionRegex) { throw 'unscanned-text-file' }
                continue
            }
            $text = Get-ScanText -Bytes ([IO.File]::ReadAllBytes($file.FullName)) -Name $file.Name
            if ($null -eq $text) { continue }
            if ($Mode -eq 'dlp') {
                Invoke-DlpCheckOnText -Text $text -Name $file.Name
            } elseif ($Mode -eq 'sast') {
                Invoke-SastCheckOnText -Text $text -RelativePath $relative
            }
        }
        return 0
    }

    $archive = Open-ZipFileOrBlock $Item.FullName
    try {
        return (Inspect-ZipArchive -Archive $archive -Depth 1 -Stats $stats -Mode $Mode)
    } finally { $archive.Dispose() }
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
    $archive = Open-ZipFileOrBlock $Item.FullName
    try {
        $files = @($archive.Entries | Where-Object { $_.Name }).Count
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

# Read-only git calls on a candidate repository: never let its configuration start a helper program.
$GitSafeArgs = @('--no-optional-locks', '-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false')

$stage = 'artifact-layout'
try {
    $artifact = Get-Item -LiteralPath $ArtifactPath -Force
    $evidence = Get-ArtifactEvidence $artifact
    Invoke-ArtifactScan -Item $artifact -Mode 'layout' | Out-Null

    $stage = 'scanner-setup'
    $scanner = Resolve-GitleaksScanner
    $config = Join-Path $PSScriptRoot 'release-secrets.toml'
    if (!(Test-Path -LiteralPath $config -PathType Leaf)) { throw 'missing-policy' }

    $stage = 'artifact-dlp'
    Invoke-ArtifactScan -Item $artifact -Mode 'dlp' | Out-Null

    $stage = 'artifact-sast'
    Invoke-ArtifactScan -Item $artifact -Mode 'sast' | Out-Null

    $stage = 'artifact-secrets'
    Invoke-SecretScan -Mode dir -Target $artifact.FullName

    $gitHistory = if ($Fast) { 'skipped-fast' } else { 'not-requested' }
    if ($GitRoot -and !$Fast) {
        $stage = 'git-layout'
        $repo = (Get-Item -LiteralPath $GitRoot -Force).FullName
        $shallowRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList ($GitSafeArgs + @('-C', $repo, 'rev-parse', '--is-shallow-repository'))
        if ($shallowRes.exitCode -ne 0 -or ([string]($shallowRes.output -join '')).Trim() -ne 'false') { throw 'invalid-or-shallow-git' }
        $topRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList ($GitSafeArgs + @('-C', $repo, 'rev-parse', '--show-toplevel'))
        $gitTop = ([string]($topRes.output -join '')).Trim()
        if ($topRes.exitCode -ne 0 -or !$gitTop -or [IO.Path]::GetFullPath($gitTop) -ine $repo) { throw 'invalid-git-root' }
        if (Test-Path -LiteralPath (Join-Path $repo '.gitleaksignore')) { throw 'project-scan-exceptions' }
        $trackedRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList ($GitSafeArgs + @('-C', $repo, 'ls-files', '-z'))
        if ($trackedRes.exitCode -ne 0) { throw 'git-files-unavailable' }
        foreach ($entry in (($trackedRes.output -join '') -split [char]0)) {
            if ($entry) { Assert-PublicPath $entry }
        }
        $stage = 'git-identity'
        $gitLogRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList ($GitSafeArgs + @('-C', $repo, 'log', '--all', '--format=%ae%n%ce%n%B'))
        if ($gitLogRes.exitCode -ne 0) { throw 'git-history-unavailable' }
        foreach ($logLine in @($gitLogRes.output)) {
            $lineStr = [string]$logLine
            if (!$lineStr) { continue }
            Assert-SafeEmailsInLine -Line $lineStr -ErrorReason 'git-personal-email'
        }
        $stage = 'git-secrets'
        Invoke-SecretScan -Mode git -Target $repo -Options @('--log-opts=--all --full-history --no-ext-diff --no-textconv')
        # Everything ever added to the history and every commit message goes through the DLP rules:
        # a path or a personal datum removed later is still published with the history.
        $stage = 'git-dlp'
        $historyRes = Invoke-NativeCaptureStdout -FilePath 'git' -ArgumentList ($GitSafeArgs + @('-C', $repo, 'log', '--all', '-p', '-U0', '--no-ext-diff', '--no-textconv', '--no-color', '--format=%B'))
        if ($historyRes.exitCode -ne 0) { throw 'git-history-unavailable' }
        $added = foreach ($histLine in @($historyRes.output)) {
            $h = [string]$histLine
            if (!$h -or $h -match '^(?:diff --git |index |@@ |--- |\+\+\+ |new file mode|deleted file mode|old mode|new mode|similarity index|rename (?:from|to) |copy (?:from|to) |Binary files |\\ No newline)') { continue }
            if ($h.StartsWith('-')) { continue }
            if ($h.StartsWith('+')) { $h.Substring(1) } else { $h }
        }
        Invoke-DlpCheckOnText -Text (@($added) -join "`n")
        $gitHistory = 'checked'
    }

    $stage = 'artifact-integrity'
    if ((Get-ArtifactEvidence (Get-Item -LiteralPath $ArtifactPath -Force)).sha256 -ne $evidence.sha256) {
        throw 'artifact-changed'
    }
    # 'full' only when the Git history was checked too; without -GitRoot the result is partial.
    $scanMode = if ($Fast) { 'fast' } elseif ($gitHistory -eq 'checked') { 'full' } else { 'partial' }
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
        'git-personal-email', 'artifact-changed', 'unscannable-archive', 'unscanned-text-file', 'sast-homoglyph', 'hardcoded-credential'
    )
    $msg = [string]$_.Exception.Message
    $reason = if ($knownReasons -contains $msg) { $msg } else { 'check-unavailable-or-invalid-input' }
    [pscustomobject]@{ status = 'blocked'; stage = $stage; reason = $reason } | ConvertTo-Json -Compress
    exit 1
}
