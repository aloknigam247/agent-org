# Agent-org runtime tools

Run these commands in PowerShell from a bootstrapped repository root. Runtime tools live in
`.github\agent-org\tools`; node-owned automation remains separate under `tools\<node>`.
The [bootstrap skill](..\skills\bootstrap\SKILL.md) documents adoption and configuration.

## Bootstrap

```pwsh
python .github\agent-org\tools\bootstrap.py --help
```

Rerun an initialized target with its existing configuration:

```pwsh
$org = Get-Content -LiteralPath .\org.json -Raw | ConvertFrom-Json
$bootstrapArgs = @("--repo", ".", "--root-name", $org.root)
foreach ($field in @("collaboration", "scope", "storage")) {
    if ($null -ne $org.$field) {
        $bootstrapArgs += "--$field"
        $bootstrapArgs += @($org.$field)
    }
}
python .github\agent-org\tools\bootstrap.py @bootstrapArgs
```

Omitted legacy fields retain the CLI defaults instead of being passed as null options.

The copied CLI uses `.github\agent-org` for its seed, template, loops, skills, schema, and tools. It reads the
selected profiles, splitter, hooks, and extension from their sibling `.github` discovery directories. Neither
same-configuration CLI reruns nor the Python API's role rendering need the original plugin.

Only selected instruction profiles are copied. Use the original plugin's bootstrap skill to select different
profiles for another target. Existing configuration conflicts are rejected, not migrated.

Python APIs in `bootstrap.py`:

- `bootstrap_repo(repo, *, root_name="main", scope=None, collaboration="agents", storage="local", source=None)`
  preflights every target and reports created/existing files without overwriting files or changing the index.
- `runtime_files(org, source=None)` returns repository-relative paths mapped to bytes, excluding the live
  `org.json`. It renders every live node and can render Parent promotion plus new Leaf definitions after a split.

Both Python APIs accept `source` as an explicit plugin root or installed runtime root. By default, source is
resolved from the script's location, not the working directory or a search of the user's plugin configuration.

Bootstrap itself needs only Python's standard library and Git. Runtime Python dependencies are in
`requirements.txt`; if an owner-validator command reports a missing dependency:

```pwsh
python -m pip install -r .github\agent-org\tools\requirements.txt
```

## Owner validator

```pwsh
$org = Get-Content -LiteralPath .\org.json -Raw | ConvertFrom-Json
python .github\agent-org\tools\owner_validator.py --help
python .github\agent-org\tools\owner_validator.py --org org.json --root .
python .github\agent-org\tools\owner_validator.py --org org.json --owner README.md
python .github\agent-org\tools\owner_validator.py --org org.json --paths README.md src\app.py
python .github\agent-org\tools\owner_validator.py --org org.json --acting $org.root --root .
python .github\agent-org\tools\owner_validator.py --org org.json --size $org.root --root .
python .github\agent-org\tools\owner_validator.py --org org.json --split-advice $org.root --root .
```

Validation reports `ok` or ownership/tree violations and exits nonzero on a failed check. The pure Python APIs
are `managed(org, path)` for scope membership and `ownership(org, path)` for the `overlap`, `owned`, `unmanaged`,
or `unowned` status also returned by `--owner`. `--acting` checks containment of a supplied or changed path set;
a shared worktree diff alone does not identify its author. Do not substitute the root's id for a child's.

The selected hybrid instruction profile defines when to run these commands. Their Python counterparts are
`checkpoint(org, root)` and `drift(org, root)`:

```pwsh
python .github\agent-org\tools\owner_validator.py --drift --root .
python .github\agent-org\tools\owner_validator.py --checkpoint --root .
```

Canonical hook commands come from the plugin's `hooks.json`; bootstrap copies them rather than reconstructing
them or changing global hooks. See the common operating core for the explicit-file-tool boundary policy and
its shell-enforcement limitations. The bundle validator is evaluation-only and is not copied.

## Session worktree

Creation requires a clean source worktree on a named branch with an existing commit. After tracked bootstrap,
review and commit the generated overlay through the repository's normal workflow before starting a session.
Bootstrap does not do this for you. Do not automatically stage, commit, or stash unrelated source changes.

The root-only lifecycle is defined in the installed `.github\agent-org\loops\common.md`. Follow that reference
for creation, audit, integration, and cleanup. Reuse the returned descriptor and absolute paths in every tool call;
do not rely on variables or `Set-Location` from an earlier shell invocation.

```pwsh
python .github\agent-org\tools\worktree.py --help
```
