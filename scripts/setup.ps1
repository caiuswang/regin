# Install regin on Windows, or upgrade an existing install - the same command
# does both. The Windows counterpart of setup.sh; keep the two in step.
# Usage: .\scripts\setup.cmd [scan_path]
#        $env:PYTHON = "C:\Python312\python.exe"; .\scripts\setup.cmd
#
# Re-running it is safe: the database, local settings and users are kept;
# dependencies, the web UI and the schema are brought up to date.
#
# Without a console on stdin every question takes its default answer
# (standalone mode, wire the Claude hooks, no user created).
#
# ASCII only: Windows PowerShell 5.1 reads a BOM-less script in the ANSI
# code page, so one non-ASCII character can break parsing.

param([string]$ScanPath = "")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Root = (Get-Location).Path
$Interactive = -not [Console]::IsInputRedirected

function Fail([string]$Message) {
  Write-Host "ERROR: $Message" -ForegroundColor Red
  exit 1
}

# Native commands do not throw on a non-zero exit, even under "Stop".
function Run {
  $exe = $args[0]
  $rest = @($args | Select-Object -Skip 1)
  & $exe @rest
  if ($LASTEXITCODE -ne 0) { Fail "'$($args -join ' ')' exited with $LASTEXITCODE" }
}

# A native command whose failure is an answer, not an error: stderr dropped,
# output returned, exit code left in $LASTEXITCODE. Windows PowerShell turns
# redirected stderr into an error record, which "Stop" would make fatal.
function Probe {
  $ErrorActionPreference = "Continue"
  $exe = $args[0]
  $rest = @($args | Select-Object -Skip 1)
  try { & $exe @rest 2>$null } catch { $global:LASTEXITCODE = 1 }
}

function Ask([string]$Prompt, [string]$Default) {
  if (-not $Interactive) { return $Default }
  $reply = Read-Host $Prompt
  if ([string]::IsNullOrWhiteSpace($reply)) { return $Default }
  return $reply
}

# UTF-8 without a BOM: Windows PowerShell's "-Encoding utf8" writes one, and
# Python's json module rejects it.
function Write-Utf8([string]$Path, [string]$Text) {
  [IO.File]::WriteAllText((Join-Path $Root $Path), $Text)
}

Write-Host "=== regin setup ==="
Write-Host ""

# --- Preflight: interpreter and toolchain, before anything is written ---
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

function Py-Version([string[]]$Cmd) {
  $out = Probe @Cmd -c "import sys; print('%d.%d' % sys.version_info[:2])"
  if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
  return "$out".Trim()
}

function Py-AtLeastMin([string]$Ver) {
  $major, $minor = $Ver.Split(".") | ForEach-Object { [int]$_ }
  return ($major -gt 3) -or ($major -eq 3 -and $minor -ge 11)
}

if (Test-Path $Venv) {
  # A venv whose interpreter vanished (e.g. after uninstalling that Python)
  # can't be repaired in place.
  if (-not (Py-Version @($VenvPy))) {
    Fail "The existing .venv has no working interpreter. Remove .venv and re-run with `$env:PYTHON set to Python 3.11 or newer."
  }
  $PyCmd = @($VenvPy)
} elseif ($env:PYTHON) {
  $PyCmd = @($env:PYTHON)
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  # The py launcher picks the newest installed Python 3, and unlike a bare
  # "python" it is never the Microsoft Store stub.
  $PyCmd = @("py", "-3")
} else {
  $PyCmd = @("python")
}
$PyVer = Py-Version $PyCmd
if (-not $PyVer) {
  Fail "Python not found (tried '$($PyCmd -join ' ')'). Install Python 3.11+ from python.org, or set `$env:PYTHON to its python.exe."
}
if (-not (Py-AtLeastMin $PyVer)) {
  if ($PyCmd[0] -eq $VenvPy) {
    Fail "The existing .venv runs Python $PyVer; regin needs Python >= 3.11. Remove .venv and re-run."
  }
  Fail "Python $PyVer at '$($PyCmd -join ' ')' is too old; regin needs Python >= 3.11. Set `$env:PYTHON to a newer python.exe."
}
Write-Host "[check] Python $PyVer"

# vite 8 declares engines.node "^20.19.0 || >=22.12.0".
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Fail "Node.js not found. The web UI build (vite 8) needs Node ^20.19 or >= 22.12."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Fail "npm not found. Install npm alongside Node.js (^20.19 or >= 22.12)."
}
$NodeVer = (& node --version).Trim()
$nodeMajor, $nodeMinor = $NodeVer.TrimStart("v").Split(".")[0..1] | ForEach-Object { [int]$_ }
$nodeOk = ($nodeMajor -eq 20 -and $nodeMinor -ge 19) -or ($nodeMajor -eq 22 -and $nodeMinor -ge 12) -or ($nodeMajor -gt 22)
if (-not $nodeOk) { Fail "Node $NodeVer is not supported by vite 8; install Node ^20.19 or >= 22.12." }
Write-Host "[check] Node $NodeVer, npm $((& npm --version).Trim())"

# --- 0. Git hooks (only inside a git repo; never switches branch) ---
Probe git rev-parse --git-dir | Out-Null
if ($LASTEXITCODE -eq 0) {
  Write-Host "[0] Installing git hooks from .githooks/..."
  Run git config core.hooksPath .githooks
} else {
  Write-Host "[0] Not a git repository - skipping git hook install"
}

# --- 1. Python environment ---
if (-not (Test-Path $Venv)) {
  Write-Host "[1/7] Creating Python virtual environment..."
  Run @PyCmd -m venv .venv
} else {
  Write-Host "[1/7] Python venv already exists"
}
Write-Host "  Installing Python dependencies (this may take a few minutes)..."
Run $VenvPy -m pip install -e ".[agent-sdk]"
Write-Host "  Dependencies installed"

$Cli = Join-Path $Root "cli\regin.py"
function Invoke-Regin { Run $VenvPy $Cli @args }

function Py-Out([string]$Code, [string]$Default) {
  $out = Probe $VenvPy -c $Code
  if ($LASTEXITCODE -ne 0 -or -not $out) { return $Default }
  return "$out".Trim()
}

# --- 1b. CLI on PATH ---
# Agents run `regin ...` from other repos' sessions, so the CLI must resolve
# outside the venv. Copied rather than linked: a symlink needs admin rights
# (or Developer Mode), and pip's launcher embeds its interpreter's absolute
# path, so the copy keeps working on its own.
$BinDir = if ($env:REGIN_BIN_DIR) { $env:REGIN_BIN_DIR } else { Join-Path $HOME ".local\bin" }
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
Copy-Item -Force (Join-Path $Venv "Scripts\regin.exe") (Join-Path $BinDir "regin.exe")
Write-Host "[1b] Installed regin CLI -> $BinDir\regin.exe"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$onPath = ($userPath -split ";") | Where-Object { $_.TrimEnd("\") -ieq $BinDir.TrimEnd("\") }
if (-not $onPath) {
  $newPath = if ($userPath) { "$userPath;$BinDir" } else { $BinDir }
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  $env:Path = "$env:Path;$BinDir"
  Write-Host "  Added $BinDir to your user PATH - open a new terminal for it to apply."
}

# --- 2. Frontend build ---
Write-Host "[2/7] Building frontend..."
Push-Location frontend
try {
  Run npm ci
  Run npx vite build
} finally {
  Pop-Location
}
Write-Host "  Frontend built"

# --- 3. Database ---
Write-Host "[3/7] Preparing the database..."
Invoke-Regin init
Invoke-Regin migrate
Write-Host "  Database at the current schema"

# --- 4. Local settings ---
Write-Host "[4/7] Configuring local settings..."
if (Test-Path config\settings.local.json) {
  Write-Host "  config\settings.local.json already exists - keeping it"
} else {
  Write-Host ""
  Write-Host "  Choose server mode:"
  Write-Host "    shared     = team mode with MySQL for user accounts/audit"
  Write-Host "    standalone = single-user mode with local SQLite only"
  $Mode = Ask "  mode [standalone]" "standalone"
  $cfg = [ordered]@{ mode = $Mode }
  if ($Mode -eq "standalone") {
    Write-Host "  Standalone mode selected - no MySQL needed."
  } else {
    Write-Host "  MySQL connection for shared user accounts."
    Write-Host "  Format: mysql://user:password@host:3306/dbname"
    $DbUrl = Ask "  database_url (or Enter to skip)" ""
    if ($DbUrl) {
      $cfg.database_url = $DbUrl
    } else {
      Write-Host "  Skipped - configure later in config\settings.local.json"
    }
  }
  Write-Utf8 "config\settings.local.json" (($cfg | ConvertTo-Json) + "`n")
}
Write-Host "  Local settings saved"

# --- 4b. Agent bridge ---
# Steering live sessions from /live types into their tmux pane, and tmux has
# no native Windows build, so setup.sh's bridge question is not asked here.

# --- 4c. Register source repos ---
# `repo_paths` holds explicit git working trees. Register the given path if it
# is itself a repo, else each immediate child that is one.
function Register-Repo([string]$Path) {
  & $VenvPy $Cli add-repo $Path
  if ($LASTEXITCODE -ne 0) { Write-Host "  warning: could not register $Path" }
}
if ($ScanPath) {
  if (Test-Path (Join-Path $ScanPath ".git")) {
    Write-Host "  Registering repo: $ScanPath"
    Register-Repo $ScanPath
  } else {
    Write-Host "  Scanning $ScanPath for git repos to register..."
    $found = $false
    foreach ($child in Get-ChildItem -Directory $ScanPath) {
      if (Test-Path (Join-Path $child.FullName ".git")) {
        Register-Repo $child.FullName
        $found = $true
      }
    }
    if (-not $found) { Write-Host "  No git repos found directly under $ScanPath" }
  }
  Invoke-Regin discover
} else {
  Write-Host "  No source path given - register repos later with:"
  Write-Host "    regin add-repo C:\path\to\your\source-repo"
  Write-Host "    (or the /repos page in the web UI)"
}

# --- 5. Claude Code hook wiring ---
Write-Host "[5/7] Claude Code hook wiring..."
$statusJson = Probe $VenvPy $Cli hooks status --provider claude --json
$wired = $false
if ($LASTEXITCODE -eq 0 -and $statusJson) {
  try {
    $rows = ($statusJson -join "`n") | ConvertFrom-Json
    $wired = [bool]($rows | Where-Object { $_.hook -eq "hook_manager" -and $_.installed })
  } catch { $wired = $false }
}
if ($wired) {
  Write-Host "  Already wired - repairing any drifted command"
  & $VenvPy $Cli hooks repair --provider claude
  if ($LASTEXITCODE -ne 0) { Write-Host "  warning: hook repair reported a problem - see the output above" }
} else {
  $hooksInput = Ask "  Wire regin's hooks into Claude Code (~\.claude\settings.json)? [Y/n]" "y"
  if ($hooksInput -match "^[Yy]") {
    & $VenvPy $Cli hooks install --provider claude
    if ($LASTEXITCODE -ne 0) { Write-Host "  warning: hook install failed - retry with: regin hooks install --provider claude" }
  } else {
    Write-Host "  Skipped - wire later with: regin hooks install --provider claude"
  }
}

# --- 6. Auth/audit tables ---
# Standalone keeps users/audit_log in the main SQLite DB, which `init` built.
Write-Host "[6/7] Auth/audit tables..."
$ModeCheck = Py-Out "from lib.settings import settings; print(settings.mode)" "shared"
$MysqlReady = $false
if ($ModeCheck -eq "standalone") {
  Write-Host "  Standalone mode - users live in the local SQLite database"
} else {
  Probe $VenvPy -c "from lib.mysql_db import is_configured; exit(0 if is_configured() else 1)" | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Invoke-Regin users init-db
    $MysqlReady = $true
    Write-Host "  MySQL tables ready"
  } else {
    Write-Host "  MySQL not configured - skipping. Add database_url to config\settings.local.json"
  }
}

# --- 7. First user ---
Write-Host ""
Write-Host "[7/7] User account"
if ($ModeCheck -eq "standalone" -or $MysqlReady) {
  $existing = [int](Py-Out "from lib.auth import user_count; print(user_count())" "0")
  if ($existing -gt 0) {
    Write-Host "  $existing user(s) already exist - not creating another"
  } elseif (-not $Interactive) {
    Write-Host "  No users yet. Create the admin with:"
    Write-Host "    regin users create <username> <password> --role admin"
  } else {
    Write-Host "  First user will be admin."
    $username = Read-Host "  Username"
    $displayName = Read-Host "  Display name"
    $secure = Read-Host "  Password" -AsSecureString
    $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
      [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $displayName) { $displayName = $username }
    Invoke-Regin users create $username $password --display-name $displayName --role admin
  }
} else {
  Write-Host "  Skipped - MySQL not configured yet."
}

$WebPort = Py-Out "from lib.settings import settings; print(settings.web_port)" "8321"

Write-Host ""
Write-Host "=== Setup complete ==="
Write-Host ""
Write-Host "Start the dashboard:"
Write-Host "  regin serve"
Write-Host "Then open http://localhost:$WebPort"
Write-Host ""
Write-Host "Upgrade later (keeps your database, settings and users):"
Write-Host "  git pull; .\scripts\setup.cmd"
Write-Host ""
Write-Host "Uninstall (removes hook wiring, deployed skills and the regin launcher; add --purge-data to drop data):"
Write-Host "  regin uninstall"
