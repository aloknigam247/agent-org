---
name: bootstrap
description: Adopt agent-org in the current repository — create the seed org, install the seed agents and Host manual, and verify coverage. Safe to run on an existing repo.
---

# Bootstrap agent-org

Set up the current repository so it is **owned by agents**. Idempotent and non-destructive: existing
files are never overwritten. The runtime tools and law live in the installed plugin at
`~/.copilot/installed-plugins/agent-org/` (override with `COPILOT_HOME`); this skill only writes repo
state.

Run these steps from the repository root (PowerShell shown; the plugin dir is the fixed install path):

1. **Locate the plugin.**
   ```pwsh
   $base = if ($env:COPILOT_HOME) { $env:COPILOT_HOME } else { Join-Path $env:USERPROFILE ".copilot" }
   $plugin = Join-Path $base "installed-plugins\agent-org"
   ```

2. **Create the seed `org.json`** (only if absent) — a single root node `main` owning the whole repo:
   ```pwsh
   if (-not (Test-Path org.json)) {
     @'
   {
     "version": 3,
     "root": "main",
     "nodes": [
       { "id": "main", "parent": null, "children": [], "mode": "Leaf",
         "charter": { "domain": ["**"], "concerns": ["the entire repository until the first split"], "excludes": [] } }
     ]
   }
   '@ | Set-Content -LiteralPath org.json -Encoding utf8
   }
   ```

3. **Materialize the seed agent defs** into `.github/agents/` (skip any that already exist), so every
   live node has a def (self-ownership SO2) and the splitter has its child template:
   ```pwsh
   New-Item -ItemType Directory -Force -Path .github\agents | Out-Null
   Get-ChildItem (Join-Path $plugin "agents") -Filter *.md | ForEach-Object {
     $dest = Join-Path ".github\agents" $_.Name
     if (-not (Test-Path $dest)) { Copy-Item $_.FullName $dest }
   }
   ```

4. **Install the Host manual** (additive; the Host routes every request to `main`):
   ```pwsh
   New-Item -ItemType Directory -Force -Path .github\instructions | Out-Null
   $man = Join-Path ".github\instructions" "agent-org.instructions.md"
   if (-not (Test-Path $man)) { Copy-Item (Join-Path $plugin "instructions\agent-org.instructions.md") $man }
   ```

5. **Verify coverage** with the oracle and report the result:
   ```pwsh
   python (Join-Path $plugin "tools\owner_validator.py") --root . --org org.json
   ```

Report what was created vs. already present, and the oracle verdict. If coverage is not `ok`, surface
the violations — do not attempt to hand-edit `org.json` to force a pass.
