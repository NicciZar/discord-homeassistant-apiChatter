param(
    [string]$Version,
    [ValidateSet("auto", "major", "minor", "patch")]
    [string]$BumpType = "auto",
    [switch]$DryRun,
    [switch]$SkipGitHubRelease,
    [switch]$Draft,
    [switch]$Prerelease,
    [switch]$ReuseTag,
    [switch]$AllowDirty,
    # Kept for backward compatibility; script is fully non-interactive now.
    [switch]$Yes
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-CommandExists {
    param([string]$CommandName)

    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "Required command '$CommandName' was not found in PATH."
    }
}

function Get-HttpsRepoUrl {
    param([string]$RemoteUrl)

    if ($RemoteUrl -match "^https://") {
        return ($RemoteUrl -replace "\.git$", "")
    }

    if ($RemoteUrl -match "^git@github.com:(.+?)\.git$") {
        return "https://github.com/$($Matches[1])"
    }

    if ($RemoteUrl -match "^git@github.com:(.+)$") {
        return "https://github.com/$($Matches[1])"
    }

    return $RemoteUrl
}

function Convert-ToTrimmedText {
    param([AllowNull()]$Value)

    if ($null -eq $Value) {
        return ""
    }

    if ($Value -is [array]) {
        if ($Value.Count -eq 0) {
            return ""
        }
        return (($Value -join "`n").Trim())
    }

    return ([string]$Value).Trim()
}

function Test-SemVer {
    param([string]$InputVersion)

    return ($InputVersion -match '^\d+\.\d+\.\d+([\-+][0-9A-Za-z\.-]+)?$')
}

function Get-BumpedVersion {
    param(
        [string]$CurrentVersion,
        [ValidateSet("major", "minor", "patch")]
        [string]$Kind
    )

    if (-not (Test-SemVer -InputVersion $CurrentVersion)) {
        throw "Current version '$CurrentVersion' is not a valid semver-like value."
    }

    if ($CurrentVersion -notmatch '^(\d+)\.(\d+)\.(\d+)(?:[\-+].*)?$') {
        throw "Could not parse current version '$CurrentVersion'."
    }

    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    $patch = [int]$Matches[3]

    switch ($Kind) {
        "major" {
            $major += 1
            $minor = 0
            $patch = 0
        }
        "minor" {
            $minor += 1
            $patch = 0
        }
        "patch" {
            $patch += 1
        }
    }

    return "$major.$minor.$patch"
}

function Get-ChangeStats {
    param([string]$CompareRef)

    if ([string]::IsNullOrWhiteSpace($CompareRef)) {
        try {
            $allFilesRaw = git ls-tree -r --name-only HEAD 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw "HEAD is not available yet."
            }
        }
        catch {
            $allFilesRaw = git ls-files --cached --others --exclude-standard 2>$null
        }

        $allFiles = @($allFilesRaw | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        return [pscustomobject]@{
            Files = $allFiles
            FilesChanged = $allFiles.Count
            Insertions = 0
            Deletions = 0
            ShortStat = ""
        }
    }

    $filesRaw = git diff --name-only $CompareRef 2>$null
    $files = @($filesRaw | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

    $shortStatRaw = git diff --shortstat $CompareRef 2>$null
    $shortStat = Convert-ToTrimmedText -Value $shortStatRaw

    $insertions = 0
    $deletions = 0

    if ($shortStat -match '(\d+) insertion') {
        $insertions = [int]$Matches[1]
    }
    if ($shortStat -match '(\d+) deletion') {
        $deletions = [int]$Matches[1]
    }

    return [pscustomobject]@{
        Files = $files
        FilesChanged = $files.Count
        Insertions = $insertions
        Deletions = $deletions
        ShortStat = $shortStat
    }
}

function Resolve-AutoBumpType {
    param(
        [int]$FilesChanged,
        [int]$Insertions,
        [int]$Deletions,
        [string[]]$ChangedFiles
    )

    $totalLines = $Insertions + $Deletions
    $coreFilesTouched = $false
    foreach ($path in $ChangedFiles) {
        if ($path -match 'custom_components/discord_apichatter/(api|services|notify|config_flow|const|__init__)\.py') {
            $coreFilesTouched = $true
            break
        }
    }

    if ($totalLines -ge 350 -or $FilesChanged -ge 12) {
        return "minor"
    }

    if ($coreFilesTouched -and $totalLines -ge 120) {
        return "minor"
    }

    return "patch"
}

function Write-Utf8NoBomFile {
    param(
        [string]$Path,
        [string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

try {
    Assert-CommandExists -CommandName "git"

    $repoRootRaw = git rev-parse --show-toplevel 2>$null
    $repoRoot = Convert-ToTrimmedText -Value $repoRootRaw
    if (-not $repoRoot) {
        throw "Could not determine git repository root."
    }

    Set-Location $repoRoot

    $manifestPath = Join-Path $repoRoot "custom_components/discord_apichatter/manifest.json"
    if (-not (Test-Path $manifestPath)) {
        throw "manifest.json not found at expected path: $manifestPath"
    }

    $manifestJson = Get-Content -Raw -Path $manifestPath | ConvertFrom-Json
    $currentVersion = [string]$manifestJson.version
    if ([string]::IsNullOrWhiteSpace($currentVersion)) {
        throw "manifest.json does not contain a valid 'version'."
    }

    if (-not (Test-SemVer -InputVersion $currentVersion)) {
        throw "Current manifest version '$currentVersion' is not a valid semver-like value."
    }

    $currentBranchRaw = git branch --show-current 2>$null
    $currentBranch = Convert-ToTrimmedText -Value $currentBranchRaw
    if (-not $currentBranch) {
        throw "Could not determine current branch."
    }

    if ($currentBranch -ne "main") {
        throw "Current branch is '$currentBranch'. Switch to 'main' before releasing."
    }

    if (-not $AllowDirty -and -not $DryRun) {
        $dirty = git status --porcelain
        if ($dirty) {
            $dirtyLines = @($dirty)
            $previewLines = @($dirtyLines | Select-Object -First 8)
            $previewText = ($previewLines | ForEach-Object { "  $_" }) -join "`n"
            $remaining = $dirtyLines.Count - $previewLines.Count
            $remainingText = if ($remaining -gt 0) {
                "`n  ... and $remaining more"
            }
            else {
                ""
            }

            throw "Working tree has uncommitted changes.`n$previewText$remainingText`nCommit/stash first, or rerun with -AllowDirty."
        }
    }

    $nativePrefVar = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    if ($nativePrefVar) {
        $oldNativePref = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $lastTagRaw = git describe --tags --abbrev=0 2>$null
        $lastTagExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $oldErrorActionPreference
        if ($nativePrefVar) {
            $PSNativeCommandUseErrorActionPreference = $oldNativePref
        }
    }

    $lastTag = Convert-ToTrimmedText -Value $lastTagRaw
    $hasLastTag = ($lastTagExitCode -eq 0) -and (-not [string]::IsNullOrWhiteSpace($lastTag))

    $compareRef = if ($hasLastTag) { "$lastTag..HEAD" } else { "" }
    $stats = Get-ChangeStats -CompareRef $compareRef

    $comparisonLabel = if ($hasLastTag) { $lastTag } else { "initial repository state" }
    Write-Host "Change summary:" -ForegroundColor Cyan
    Write-Host "- Compared against: $comparisonLabel"
    Write-Host "- Files changed: $($stats.FilesChanged)"
    Write-Host "- Insertions: $($stats.Insertions)"
    Write-Host "- Deletions: $($stats.Deletions)"

    if ($stats.FilesChanged -gt 0) {
        Write-Host "- Changed file preview:" -ForegroundColor Cyan
        $stats.Files | Select-Object -First 10 | ForEach-Object { Write-Host "  - $_" }
        if ($stats.FilesChanged -gt 10) {
            Write-Host "  - ... and $($stats.FilesChanged - 10) more"
        }
    }

    if (-not $AllowDirty -and $hasLastTag -and $stats.FilesChanged -eq 0) {
        if ($DryRun) {
            Write-Host "No committed changes found since $lastTag." -ForegroundColor Yellow
            Write-Host "Dry run complete. Nothing would be released." -ForegroundColor Green
            return
        }
        throw "No committed changes found since $lastTag. Nothing to release."
    }

    $resolvedBumpType = ""
    $newVersion = ""

    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        if (-not (Test-SemVer -InputVersion $Version)) {
            throw "Version '$Version' is not a valid semver-like value (example: 1.2.3 or 1.2.3-beta.1)."
        }

        $newVersion = $Version
        $resolvedBumpType = "manual"
    }
    else {
        if ($BumpType -eq "auto") {
            $resolvedBumpType = Resolve-AutoBumpType `
                -FilesChanged $stats.FilesChanged `
                -Insertions $stats.Insertions `
                -Deletions $stats.Deletions `
                -ChangedFiles $stats.Files
            Write-Host "Auto bump selected: $resolvedBumpType" -ForegroundColor Cyan
        }
        else {
            $resolvedBumpType = $BumpType
        }

        $newVersion = Get-BumpedVersion -CurrentVersion $currentVersion -Kind $resolvedBumpType
    }

    Write-Host "Version:" -ForegroundColor Cyan
    Write-Host "- Current: $currentVersion"
    Write-Host "- Next:    $newVersion"

    $tag = "v$newVersion"

    $localTagExistsRaw = git tag --list $tag 2>$null
    $localTagExists = Convert-ToTrimmedText -Value $localTagExistsRaw
    $hasLocalTag = -not [string]::IsNullOrWhiteSpace($localTagExists)
    if ($hasLocalTag -and -not $ReuseTag) {
        throw "Local tag '$tag' already exists. Use -ReuseTag to continue with the existing tag."
    }
    if ($hasLocalTag -and $ReuseTag) {
        Write-Warning "Local tag '$tag' already exists. Reusing existing local tag."
    }

    $hasOriginRemote = $true
    try {
        $remoteUrlRaw = git remote get-url origin 2>$null
        $remoteUrl = Convert-ToTrimmedText -Value $remoteUrlRaw
        if (-not $remoteUrl) {
            $hasOriginRemote = $false
        }
    }
    catch {
        $hasOriginRemote = $false
        $remoteUrl = ""
    }

    $hasRemoteTag = $false
    if ($hasOriginRemote) {
        Write-Host "Fetching remote tags..."
        git fetch --tags origin | Out-Null

        $remoteTagExistsRaw = git ls-remote --tags origin "refs/tags/$tag" 2>$null
        $remoteTagExists = Convert-ToTrimmedText -Value $remoteTagExistsRaw
        $hasRemoteTag = -not [string]::IsNullOrWhiteSpace($remoteTagExists)
        if ($hasRemoteTag -and -not $ReuseTag) {
            throw "Remote tag '$tag' already exists on origin. Use -ReuseTag to continue with the existing tag."
        }
        if ($hasRemoteTag -and $ReuseTag) {
            Write-Warning "Remote tag '$tag' already exists on origin."
        }

        $repoUrl = Get-HttpsRepoUrl -RemoteUrl $remoteUrl
    }
    elseif ($DryRun) {
        $repoUrl = "(origin remote not configured)"
        Write-Warning "Origin remote is not configured. Continuing dry run without remote tag checks."
    }
    else {
        throw "Could not determine origin remote URL."
    }

    Write-Host "Ready to create release:" -ForegroundColor Cyan
    Write-Host "- Repository: $repoUrl"
    Write-Host "- Branch: $currentBranch"
    Write-Host "- Version: $newVersion"
    Write-Host "- Tag: $tag"
    Write-Host "- Bump type: $resolvedBumpType"
    Write-Host "- Dry run: $DryRun"
    Write-Host "- Reuse existing tag: $ReuseTag"
    Write-Host "- Create GitHub release: $(-not $SkipGitHubRelease)"
    Write-Host "Proceeding automatically (no interactive prompt)." -ForegroundColor Yellow

    if ($DryRun) {
        Write-Host ""
        Write-Host "Dry run complete. No files were changed, committed, pushed, or released." -ForegroundColor Green
        return
    }

    $versionChanged = $newVersion -ne $currentVersion
    if ($versionChanged) {
        Write-Host "Updating manifest version..." -ForegroundColor Cyan
        $manifestJson.version = $newVersion
        $manifestContent = $manifestJson | ConvertTo-Json -Depth 10
        Write-Utf8NoBomFile -Path $manifestPath -Content $manifestContent

        Write-Host "Committing version bump..." -ForegroundColor Cyan
        git add -- $manifestPath
        git commit -m "chore(release): bump version to v$newVersion" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to commit manifest version bump to $newVersion."
        }
    }
    else {
        Write-Host "Version unchanged ($currentVersion)." -ForegroundColor Yellow
    }

    Write-Host "Pushing branch '$currentBranch'..."
    git push origin $currentBranch

    if (-not $hasLocalTag) {
        Write-Host "Creating annotated tag '$tag'..."
        git tag -a $tag -m "Release $tag"
    }
    else {
        Write-Host "Skipping tag creation, '$tag' already exists locally."
    }

    if (-not $hasRemoteTag) {
        Write-Host "Pushing tag '$tag'..."
        git push origin $tag
    }
    else {
        Write-Host "Skipping tag push, '$tag' already exists on origin."
    }

    if (-not $SkipGitHubRelease) {
        if (Get-Command gh -ErrorAction SilentlyContinue) {
            $releaseExists = $false

            # gh returns exit code 1 when a release does not exist; treat that as normal.
            $nativePrefVar = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
            if ($nativePrefVar) {
                $oldNativePref = $PSNativeCommandUseErrorActionPreference
                $PSNativeCommandUseErrorActionPreference = $false
            }

            $oldErrorActionPreference = $ErrorActionPreference
            $ErrorActionPreference = "Continue"

            try {
                $null = & gh release view $tag --json tagName 2>$null
                $releaseExists = ($LASTEXITCODE -eq 0)
            }
            finally {
                $ErrorActionPreference = $oldErrorActionPreference
                if ($nativePrefVar) {
                    $PSNativeCommandUseErrorActionPreference = $oldNativePref
                }
            }

            if ($releaseExists) {
                Write-Host "GitHub release '$tag' already exists. Skipping release creation."
                Write-Host "Release flow completed successfully for $tag"
                return
            }

            $ghArgs = @(
                "release",
                "create",
                $tag,
                "--title",
                $tag,
                "--generate-notes"
            )

            if ($Draft) {
                $ghArgs += "--draft"
            }

            if ($Prerelease) {
                $ghArgs += "--prerelease"
            }

            Write-Host "Creating GitHub release via gh CLI..."
            gh @ghArgs
        }
        else {
            Write-Warning "gh CLI not found. Tag was pushed, but release was not created automatically."
            Write-Host "Create release manually: $repoUrl/releases/new?tag=$tag"
        }
    }

    Write-Host "Release flow completed successfully for $tag"
}
catch {
    $message = $_.Exception.Message
    Write-Host ""
    Write-Host "Release script failed:" -ForegroundColor Red
    Write-Host "  $message" -ForegroundColor Red
    Write-Host ""
    Write-Host "Hints:" -ForegroundColor Yellow
    Write-Host "  - Use -AllowDirty if you want to release with local changes"
    Write-Host "  - Use -ReuseTag if the release tag already exists"
    Write-Host "  - Use -Version X.Y.Z to force a specific release version"
    exit 1
}
