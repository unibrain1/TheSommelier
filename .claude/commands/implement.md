# Implement

Plan and implement the current issue. Reads the issue from the current branch name.

## Arguments

- `$ARGUMENTS` — (optional) additional instructions or constraints for the implementation.

## Workflow

1. **Identify the issue**:

   Extract the issue number from the current branch. The branch must match `bug/<number>-*`, `feature/<number>-*` or `issue/<number>-*` (see the branch-naming convention in `CLAUDE.md`). If the branch doesn't match, stop and ask the user to switch to the issue branch, or to run `/start-issue` if no branch exists.

   ```bash
   gh issue view <number> --json number,title,body,milestone,labels
   ```

2. **Read the relevant code directly** (do NOT delegate to agents):

   - Read the files mentioned in or relevant to the issue
   - Understand the current implementation
   - Identify what needs to change

3. **Create an implementation plan**:

   Present a concise plan with:
   - **Files to modify** — list with what changes each needs
   - **Implementation steps** — ordered list of code changes
   - **Testing** — what to test (always include `python3 -m pytest tests/ -v` if Python files change)
   - **Docs** — flag if CLAUDE.md or docs/menu-guide.md need updating (don't update yet — that happens in `/finish-milestone`)
   - **Open questions** — anything needing user input

   For bug-labeled issues, add:
   - **Escape analysis** — why this wasn't caught, what test to add to prevent recurrence

   Say: "Review the plan, then say 'go' to begin or ask questions."

4. **Implement** (after user says 'go'):

   Do the work directly in the main conversation. Only spawn **software-developer** agents if there are truly independent file changes that benefit from parallelism (e.g., a script change + a completely separate site change).

   If any `scripts/*.py` or pipeline shell scripts are changed, launch the **pipeline-reviewer** subagent (`.claude/agents/pipeline-reviewer.md`) as a background review after implementation is complete.

5. **Validate**:

   ```bash
   pre-commit run --all-files
   ```

   Fix any failures before proceeding. If pyright reports type errors, fix them.

   ```bash
   python3 -m pytest tests/ -v
   ```

   Fix any test failures.

6. **Hand off**:

   ```text
   Implementation complete for issue #<number>. Next steps:
   1. Review the changes: `git diff`
   2. `/simplify`        — Review changed code for reuse, quality, and efficiency
   3. `/commit`          — Commit your changes
   4. `/commit-push-pr`  — Push and create a PR targeting milestone/<version>
   5. `/finish-issue`    — Squash-merge and close the issue
   ```

## Important

- Do the work yourself. Only spawn agents when parallelism genuinely helps.
- The PR must target the **milestone branch**, not `main`.
- The plan is a proposal — wait for user approval before writing code.
