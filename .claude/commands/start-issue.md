---
description: Start work on a GitHub issue within a milestone workflow
---

# GitHub Issue Workflow Command

## Available Agents

| Agent | `subagent_type` | Model | Use When |
| --- | --- | --- | --- |
| Explore | `Explore` | `haiku` | Codebase research |
| Plan | `Plan` | `sonnet` | Implementation strategy |
| Software Developer | `software-developer` | `sonnet` | **Primary coding agent** |
| Senior Architect | `senior-architect` | `sonnet` | Architecture, security, code review |
| Senior Product Manager | `senior-product-manager` | `sonnet` | Issue refinement, scope, criteria |
| Senior Test Engineer | `senior-test-engineer` | `sonnet` | Test strategy and writing |
| Technical Documentation Writer | `technical-documentation-writer` | `haiku` | Docs updates |

> **Note:** Only `Explore`, `Plan`, `pipeline-reviewer`, and `site-reviewer` are
> defined locally in `.claude/agents/`. The role-based agents above
> (`software-developer`, `senior-architect`, etc.) come from an installed agent
> plugin. If they are unavailable, fall back to the generic `Agent` tool with a
> role-specific prompt.

**Always invoke:** Explore (Step 5), senior-product-manager (Step 6), senior-architect (Step 7.3).
**Always invoke for code changes:** software-developer, senior-test-engineer.
**Skip** docs agent for internal refactoring; test agent for docs-only changes.

## Workflow Steps

### Step 1: Ask for Issue Number (if not provided)

### Step 2: Fetch Issue Details

```bash
gh issue view ISSUE_NUMBER --json number,title,body,milestone,labels
```

### Step 3: Verify Milestone Branch

1. `git branch --show-current` — must match `milestone/*`
   - Zero milestone branches: stop, tell user to run `/start-milestone` first
   - Multiple: stop, ask which one
2. **Ensure clean working tree** — run `git status --porcelain`. If any output appears, stop and ask the user to commit or stash before proceeding.
3. **Branch naming** — `bug/` for bug label, `feature/` for enhancement, `issue/` for all others. Confirm name with user before creating.

### Step 4: Create Issue Branch

```bash
git checkout -b BRANCH_NAME
git push -u origin BRANCH_NAME
```

### Step 4.5: Update GitHub Issue

```bash
gh label create "in progress" --color 0075CA --description "Work is actively underway" 2>/dev/null || true
gh issue edit ISSUE_NUMBER --add-label "in progress" --add-assignee @me
```

### Step 5: Launch Explore Agents (parallel)

One agent per affected subsystem (`scripts/`, `site/`, `pipeline.sh`). Each agent must check:
- `scripts/wine_utils.py`, `scripts/scoring.py`, `scripts/wine_keywords.py` — do not duplicate
- `tests/` — existing coverage
- For pipeline changes: which JSON contracts are produced/consumed (must not break `site/index.html`)

### Step 6: PM Assessment + User Interview

Launch **senior-product-manager** with the issue details and Explore results. Ask it to evaluate: completeness, missing acceptance criteria, decomposition needs, and questions for the user.

Then interview the user **one question at a time**: scope clarity, LLM vs rules boundary, pipeline ordering concerns, edge cases.

### Step 7: Plan Mode

Use EnterPlanMode. While planning:

1. Launch additional Explore agents as needed.
2. Ask clarifying questions one at a time.
3. **Pipeline impact check:** Does this change a JSON schema? Add a script to `pipeline.sh`? Require new env vars (update `.env.sample`)? Cross the LLM boundary?
4. **Consult specialists in parallel** (Step 7.3):

   - **senior-architect** (always): issue details + Explore results + proposed approach → review pipeline ordering, JSON contract integrity, shared utility placement, shell safety (`set -euo pipefail`), secret handling (`op read`)
   - **senior-test-engineer** (code changes): propose test strategy covering unit tests, edge cases (empty inventory, missing menu, unavailable Claude CLI), JSON contract validation, regression risks
   - **technical-documentation-writer** (when docs affected):

     | Change | Docs |
     |--------|------|
     | New scripts/pipeline steps | CLAUDE.md directory + pipeline sections |
     | New env vars | CLAUDE.md + `.env.sample` |
     | Pairing/scoring changes | CLAUDE.md conventions + `docs/menu-guide.md` |
     | Docker/HA changes | CLAUDE.md deployment/HA sections |

5. Incorporate feedback into plan: Pipeline Considerations → Architecture → Implementation Steps → Test Plan → Docs Plan.

### Step 8: Exit Plan Mode + Present for Approval

### Step 9: Implement (after approval)

1. Update issue with plan details.
2. Launch parallel **software-developer** agents for independent files.
3. Launch parallel post-implementation agents: **senior-test-engineer** (write + run tests), **technical-documentation-writer** (update docs).
4. Validate:
   - Run `python3 -m pytest tests/ -v`.
   - Run `pre-commit run --all-files`. It runs `ruff`, `ruff format` and `pyright` with the pinned versions. The `pyright` command is not on the shell PATH, so do not run it directly.
   - If the `mcp__ide__getDiagnostics` tool is available, also run it.
5. Run `/security-review` — address Critical/High findings before proceeding.
6. Launch **senior-architect** for final review: pipeline order, JSON contracts, shell safety, test coverage.
7. Fix any issues, then hand off:

```text
Implementation complete for issue #ISSUE_NUMBER. Next steps:
1. /simplify        — Review changed code (optional)
2. /security-review — Audit changes before push
3. Commit your changes manually (`git commit`) and push the branch.
   Open a PR targeting `MILESTONE_BRANCH` and include "Closes #ISSUE_NUMBER" in the body.
4. /finish-issue    — Squash-merge, close the issue, return to milestone branch
```

> Do NOT run any of these steps automatically.

## Key Constraints

- **Never commit** — this command does not commit, push, or create PRs
- **Issue PRs target the milestone branch**, never `main`
- **LLM boundary**: only `generate_notes.py` and `enrich_menu.py` use Claude; everything else is scripted rules
- **Pipeline order is sacred**: fetch → parse → plan → notes → enrich → pair → publish
