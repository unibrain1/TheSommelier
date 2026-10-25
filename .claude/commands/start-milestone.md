# Start Milestone

Begin work on a milestone by creating a milestone branch from main.

Replace each `<name>` placeholder in this file with the real value before you use it.

## Arguments

- `$ARGUMENTS` — the milestone version number (e.g., `v1.2.0`)

## Workflow

1. **Validate the milestone exists** on GitHub:

   ```bash
   gh api repos/:owner/:repo/milestones --jq '.[] | select(.title | startswith("'"$ARGUMENTS"'"))'
   ```

   If not found, stop and report the error.

2. **Ensure clean working tree**:

   ```bash
   git status --porcelain
   ```

   If uncommitted changes exist, stop and ask the user to commit or stash.

3. **Create the milestone branch from main**:

   ```bash
   git checkout main
   git pull origin main
   git checkout -b milestone/$ARGUMENTS
   git push -u origin milestone/$ARGUMENTS
   ```

4. **List the milestone's open issues**:

   ```bash
   gh issue list --milestone "<milestone-title>" --state open --json number,title,labels,body
   ```

5. **Recommend an issue order** based on:
   - Dependencies (foundational changes first)
   - Severity (CRITICAL → HIGH → MEDIUM → LOW)
   - Shared code paths (group to minimize merge conflicts)
   - Pipeline/utility before site/presentation

   Present as a numbered list with brief rationale.

6. **Output**:
   - The milestone branch name
   - The recommended issue order
   - "Use `/start-issue <issue-number>` to begin work on the first issue"
