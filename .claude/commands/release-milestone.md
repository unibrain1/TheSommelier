# Release Milestone

Squash merge a milestone PR into main, tag the release, and publish a GitHub release.

Picks up where `/finish-milestone` left off — after the milestone PR has been created and reviewed.

Replace each `<name>` placeholder in this file with the real value before you use it.

## Arguments

- `$ARGUMENTS` — (optional) the milestone version number (e.g., `v1.2.0`). If omitted, auto-detect from the open `milestone/*` → `main` PR.

## Workflow

1. **Find the milestone PR**:

   ```bash
   gh pr list --base main --state open --json number,title,headRefName,url
   ```

   Filter for `milestone/*` branches. If `$ARGUMENTS` provided, match against it. If zero or multiple matches, ask the user to specify.

2. **Verify preconditions**:

   ```bash
   gh pr view <pr-number> --json mergeable,mergeStateStatus
   git status --porcelain
   ```

   PR must be mergeable. Working tree must be clean.

3. **Show summary and ask for confirmation**:

   Display PR number, title, URL, commit count, version to tag. Say: "This will squash merge, tag, and push. Confirm?"

   **Wait for explicit confirmation.**

4. **Merge**:

   ```bash
   git checkout main
   git pull origin main
   gh pr merge <pr-number> --squash --delete-branch
   git pull origin main
   ```

5. **Tag**:

   ```bash
   git tag -a <version> -m "<version> — <milestone-title>"
   git push origin main --tags
   ```

6. **Clean up local branch**:

   ```bash
   git branch -d milestone/<version> 2>/dev/null
   ```

7. **Create GitHub release**:

   ```bash
   gh issue list --milestone "<milestone-title>" --state closed --json number,title
   gh release create <version> --title "<version> — <milestone-title>" --generate-notes
   ```

8. **Close the GitHub milestone**:

   ```bash
   gh api repos/{owner}/{repo}/milestones/<milestone-number> -X PATCH -f state=closed
   ```

9. **Output**:
   - Release link
   - Milestone closed
   - Deployment reminder:
     ```text
     To deploy: ssh docker02, then docker compose down && docker compose up -d
     Or wait for the next scheduled run — the container will git pull automatically.
     ```

## Important

- **Confirmation required** before the merge. Do not proceed without it.
- If any step fails, stop and report. Do not continue with partial state.
- This assumes `/finish-milestone` has already been run.
