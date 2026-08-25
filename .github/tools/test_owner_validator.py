"""Grader self-tests for the owner-oracle (agentOrg §2.7 / rubber-duck s13).

These pin the *behaviour* the whole design depends on — the glob dialect, path-separator
normalization, case sensitivity, dotfile handling, cross-cutting excludes, UNOWNED/overlap detection,
and the structural tree checks. Run: ``python .github/tools/test_owner_validator.py`` (exit 0 = all
pass, non-zero = failure). No test framework required.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import owner_validator as ov  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def leaf(nid, domain, excludes=None, parent="main"):
    return {"id": nid, "parent": parent, "children": [], "mode": "Leaf",
            "charter": {"domain": domain, "concerns": [], "excludes": excludes or []}}


# A valid 4-node org mirroring the Spec Review Platform (design §2.2 canonical example).
PLATFORM = {
    "version": 3,
    "root": "main",
    "nodes": [
        {"id": "main", "parent": None, "children": ["backend", "frontend", "infra"], "mode": "Parent",
         "charter": {"domain": ["README.md", "org.json", ".github/**", "wiki/**/*.contract.md"], "concerns": [], "excludes": []}},
        leaf("backend", ["src/Api/**", "tests/**"], ["**/Dockerfile"]),
        leaf("frontend", ["src/web/**"], ["**/Dockerfile"]),
        leaf("infra", ["infra/**", "**/Dockerfile", "azure.yaml", "docker-compose*.yml"]),
    ],
}


def owner_of(org, path):
    hits = ov.owners_of(ov.compile_nodes(org["nodes"]), path)
    return hits[0] if len(hits) == 1 else (None if not hits else hits)


def main():
    print("glob dialect: 'everything' token")
    everything = {"version": 3, "root": "main",
                  "nodes": [{"id": "main", "parent": None, "children": [], "mode": "Leaf",
                             "charter": {"domain": ["**"], "concerns": [], "excludes": []}}]}
    for p in ["README.md", "org.json", ".github/x.md", "src/a/b/c.cs", ".dotfile"]:
        check(f"** matches root/nested/dotfile: {p}", owner_of(everything, p) == "main",
              f"got {owner_of(everything, p)!r}")

    print("path-separator normalization (Windows backslash -> posix)")
    check("normalize backslashes", ov.normalize("src\\Api\\x.cs") == "src/Api/x.cs")
    check("normalize leading ./", ov.normalize("./src/x.cs") == "src/x.cs")
    check("backslash path still owned", owner_of(PLATFORM, "src\\Api\\Program.cs") == "backend",
          f"got {owner_of(PLATFORM, 'src\\\\Api\\\\Program.cs')!r}")

    print("effective domain + cross-cutting exclude (Dockerfile aspect)")
    check("src/Api/Program.cs -> backend", owner_of(PLATFORM, "src/Api/Program.cs") == "backend")
    check("src/Api/Dockerfile -> infra (backend excludes it)", owner_of(PLATFORM, "src/Api/Dockerfile") == "infra",
          f"got {owner_of(PLATFORM, 'src/Api/Dockerfile')!r}")
    check("src/web/App.tsx -> frontend", owner_of(PLATFORM, "src/web/App.tsx") == "frontend")
    check("infra/main.bicep -> infra", owner_of(PLATFORM, "infra/main.bicep") == "infra")
    check("tests/x.cs -> backend", owner_of(PLATFORM, "tests/x.cs") == "backend")

    print("parent shared-set ownership")
    check("README.md -> main", owner_of(PLATFORM, "README.md") == "main")
    check(".github/org-design.md -> main", owner_of(PLATFORM, ".github/org-design.md") == "main")
    check("wiki/api.contract.md -> main", owner_of(PLATFORM, "wiki/api.contract.md") == "main")

    print("case sensitivity (git-style)")
    check("src/api/x.cs (lowercase) NOT backend", owner_of(PLATFORM, "src/api/x.cs") is None,
          f"got {owner_of(PLATFORM, 'src/api/x.cs')!r}")

    print("coverage verdicts over a path set")
    good = ["README.md", "org.json", ".github/org-design.md", "src/Api/Program.cs",
            "src/Api/Dockerfile", "src/web/App.tsx", "infra/main.bicep", "tests/x.cs"]
    check("clean platform set -> ok", ov.validate(PLATFORM, good)["status"] == "ok",
          str(ov.validate(PLATFORM, good)))
    unowned = ov.validate(PLATFORM, ["src/shared/types.ts"])
    check("src/shared/types.ts -> uncovered/UNOWNED",
          any(v["rule"] == "uncovered" for v in unowned["violations"]), str(unowned))

    print("overlap detection")
    overlap_org = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "parent": None, "children": ["backend", "frontend"], "mode": "Parent",
         "charter": {"domain": ["README.md"], "concerns": [], "excludes": []}},
        leaf("backend", ["src/Api/**"]),
        leaf("frontend", ["src/web/**", "src/Api/**"]),  # deliberately overlaps backend
    ]}
    ovr = ov.validate(overlap_org, ["src/Api/Program.cs"])
    check("two claimants -> overlap", any(v["rule"] == "overlap" for v in ovr["violations"]), str(ovr))

    print("structural tree checks")
    check("valid platform tree -> no tree violations", ov.check_tree(PLATFORM) == [])

    parent_one_child = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "parent": None, "children": ["backend"], "mode": "Parent",
         "charter": {"domain": ["**"], "concerns": [], "excludes": []}},
        leaf("backend", ["src/Api/**"]),
    ]}
    check("Parent with <2 children -> tree", any(v["rule"] == "tree" for v in ov.check_tree(parent_one_child)))

    leaf_with_child = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "parent": None, "children": ["backend"], "mode": "Leaf",
         "charter": {"domain": ["**"], "concerns": [], "excludes": []}},
        leaf("backend", ["src/Api/**"]),
    ]}
    check("Leaf with children -> tree", any(v["rule"] == "tree" for v in ov.check_tree(leaf_with_child)))

    backref = {"version": 3, "root": "main", "nodes": [
        {"id": "main", "parent": None, "children": ["backend", "frontend"], "mode": "Parent",
         "charter": {"domain": ["**"], "concerns": [], "excludes": []}},
        leaf("backend", ["src/Api/**"], parent="frontend"),  # wrong parent
        leaf("frontend", ["src/web/**"]),
    ]}
    check("back-reference mismatch -> tree", any(v["rule"] == "tree" for v in ov.check_tree(backref)))

    no_root = {"version": 3, "root": "main", "nodes": [leaf("main", ["**"], parent="ghost")]}
    check("no null-parent root -> tree", any(v["rule"] == "tree" for v in ov.check_tree(no_root)))

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: {FAILURES}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
