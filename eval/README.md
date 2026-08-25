# agentOrg eval harness

Offline, headless, fixture-based testing of the agent org. Each case gives an agent an **intent**,
runs it in a disposable sandbox, and checks its **output** — response, actions, and resulting repo
state — against a **human-curated manifest** that is independent of `org.json` (so routing checks are
not circular).

Status: **E1** — the runner skeleton (build sandbox → invoke → capture → teardown). Grading arrives
in E2.

## Layout

```
eval/
  run.py                 # runner (E1); reuses .github/tools/owner_validator as the grader backbone
  requirements.txt       # pyyaml (the oracle needs pathspec, see .github/tools/requirements.txt)
  fixtures/<case>/
    manifest.yml         # the case definition (below)
    seed/                # repo state under test: org.json + domain files (kept small)
```

The runner overlays the kernel (`.github/`, `org.schema.json`) onto the fixture `seed/`, so a fixture
only carries its unique state. The sandbox is a fresh git repo with a single `seed` commit as the
baseline for diffs.

## Manifest

```yaml
id: <case-id>
unit: node | splitter          # what is under test
agent: main                    # agent to invoke (main, splitter, or a node id)
intent: "<the prompt given to the agent>"
expected_owner: [main]         # human-labeled owning node(s) — routing ground truth (not from org.json)
allowed_paths: ["**"]          # regions the change may touch
forbidden_paths: []            # regions it must not touch
required_behavior: []          # human-readable outcome checks (E2+; judge only where a command can't decide)
build_cmd: null                # optional build/test command; null = skip
timeout: 240                   # seconds
threshold_override: null       # optional split-threshold fraction (scale down so a small fixture trips a split)
```

Fixtures are **versioned and immutable** — new reality means a new fixture version, never a silent
edit of an existing one.

## Run

```pwsh
pip install -r eval/requirements.txt
python eval/run.py eval/fixtures/smoke-create            # one run
python eval/run.py eval/fixtures/smoke-create --repeats 5 --keep
```

Auth: the harness sets `COPILOT_GITHUB_TOKEN` from `gh auth token`, because a spawned `copilot`
subprocess does not inherit an agent session's managed auth.
