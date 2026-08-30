---
name: bootstrap
description: Adopt agent-org in the current repository as a non-invasive local overlay — install the kernel, seed the org, git-exclude the overlay, and verify coverage. Safe on an existing repo.
---

# Bootstrap agent-org

Set up the current repository so it is **owned by agents**, as a **local overlay**: everything this
skill writes is added to `.git/info/exclude`, so it never appears in the user's `git status` and is
never committed — adopting agent-org does not touch the tracked tree. Idempotent and non-destructive:
existing files are never overwritten.

Run from the repository root (PowerShell):

1. **Find the installed plugin** (works regardless of install path):
   ```pwsh
   $base = if ($env:COPILOT_HOME) { $env:COPILOT_HOME } else { Join-Path $env:USERPROFILE ".copilot" }
   $plugin = Get-ChildItem (Join-Path $base "installed-plugins") -Recurse -Filter plugin.json -ErrorAction SilentlyContinue |
     Where-Object { (Get-Content $_.FullName -Raw | ConvertFrom-Json).name -eq "agent-org" } |
     Select-Object -First 1 | ForEach-Object { $_.Directory.FullName }
   if (-not $plugin) { throw "agent-org plugin not found under $base\installed-plugins" }
   ```

2. **Copy the runtime tools and seed defs into the repo** (skip any that already exist):
   ```pwsh
   New-Item -ItemType Directory -Force -Path .github\tools, .github\agents, .github\instructions | Out-Null
   Copy-Item (Join-Path $plugin "tools\*") .github\tools\ -Recurse -Force
   Get-ChildItem (Join-Path $plugin "agents") -Filter *.md | ForEach-Object {
     $d = Join-Path ".github\agents" $_.Name; if (-not (Test-Path $d)) { Copy-Item $_.FullName $d }
   }
   $man = ".github\instructions\agent-org.instructions.md"
   if (-not (Test-Path $man)) { Copy-Item (Join-Path $plugin "instructions\agent-org.instructions.md") $man }
   pip install -q -r (Join-Path $plugin "tools\requirements.txt")   # pathspec, one-time
   ```

3. **Create the seed `org.json`** (only if absent) — root `main` owns the whole repo:
   ```pwsh
   if (-not (Test-Path org.json)) {
     @'
   { "version": 3, "root": "main", "nodes": [
     { "id": "main", "parent": null, "children": [], "mode": "Leaf",
       "charter": { "domain": ["**"], "concerns": ["the entire repository until the first split"], "excludes": [] } } ] }
   '@ | Set-Content -LiteralPath org.json -Encoding utf8
   }
   ```

4. **Git-exclude the overlay** (local only, never committed), idempotently:
   ```pwsh
   $exclude = ".git\info\exclude"
   $lines = @("/org.json", "/.github/tools/", "/.github/agents/", "/.github/instructions/", "/.worktrees/")
   $have = if (Test-Path $exclude) { Get-Content $exclude } else { @() }
   foreach ($l in $lines) { if ($have -notcontains $l) { Add-Content -LiteralPath $exclude -Value $l } }
   ```

5. **Install the containment hook** (user-level, so it fires in headless `-p` — plugin-contributed
   hooks do not). Guarded to no-op outside agent-org repos, so it is safe globally:
   ```pwsh
   $hooks = Join-Path $base "hooks"; New-Item -ItemType Directory -Force -Path $hooks | Out-Null
   $ps = 'if (Test-Path .github\tools\owner_validator.py) { python .github\tools\owner_validator.py --hook --org org.json } else { ''{"permissionDecision":"allow"}'' }'
   $bash = 'if [ -f .github/tools/owner_validator.py ]; then python .github/tools/owner_validator.py --hook --org org.json; else echo "{\"permissionDecision\":\"allow\"}"; fi'
   $hook = @{ version = 1; hooks = @{ preToolUse = @(@{ type = 'command'; powershell = $ps; bash = $bash; timeoutSec = 20 }) } }
   $hook | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $hooks 'agent-org.json') -Encoding utf8
   ```

6. **Verify coverage** and report:
   ```pwsh
   python .github\tools\owner_validator.py --root . --org org.json
   ```

Report what was created vs. already present, and the oracle verdict. If coverage is not `ok`, surface
the violations — do not hand-edit `org.json` to force a pass.
