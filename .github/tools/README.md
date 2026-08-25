# Kernel tooling (`.github/tools/`)

Governance-owned tooling for the agentOrg kernel — distinct from node-owned `tools/<node>/` bundles.

## owner-oracle — `owner_validator.py`

The deterministic coverage validator (design §2.7). Computes `owner(path)` for the repo's files
against `org.json` and reports violations (`uncovered`/`UNOWNED`, `overlap`, `tree`). One source of
truth, called by the `splitter` (pre-commit) and the integration gate.

```pwsh
pip install -r .github/tools/requirements.txt      # one-time: installs pathspec

# validate the whole repo (tracked + untracked-non-ignored)
python .github/tools/owner_validator.py --org org.json --root .

# validate an explicit path set (e.g. a change's diff)
python .github/tools/owner_validator.py --org org.json --paths src/Api/x.cs src/web/y.tsx

# who owns a path?
python .github/tools/owner_validator.py --org org.json --owner src/Api/Program.cs
```

Exit code is `0` when `status: ok`, non-zero on any violation.

## Self-tests — `test_owner_validator.py`

Pins the glob dialect (gitignore semantics), path-separator normalization, case sensitivity, dotfile
handling, cross-cutting excludes, UNOWNED/overlap, and the structural tree checks.

```pwsh
python .github/tools/test_owner_validator.py      # exit 0 = all pass
```
