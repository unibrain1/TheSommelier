# Finish Issue

Squash-merge a PR, close the issue, delete the branch, and return to the milestone branch.

## Arguments

- `$ARGUMENTS` — the GitHub issue number (e.g., `16`). If omitted, infer from the current branch name (`bug/<number>-*`, `feature/<number>-*` or `issue/<number>-*`, see the branch-naming convention in `CLAUDE.md`).

## Workflow

1. **Determine the issue number and PR**:

   Run `git branch --show-current`. Keep the output as `<issue-branch>` for step 6.

   Set `<issue-number>` to `$ARGUMENTS`. If there is no argument, extract the number from the current branch name:

   ```bash
   git branch --show-current | sed -nE 's#^(bug|feature|issue)/([0-9]+)-.*#\2#p'
   ```

   If the output is empty, stop and ask the user for the issue number.

   Find the open PR:

   ```bash
   gh pr list --head '<issue-branch>' --state open --json number,title,url,baseRefName
   ```

   If no PR exists, stop and tell the user to run `/commit-push-pr` first.

2. **Identify the target branch** from the PR's `baseRefName` (should be `milestone/*`). If it targets `main`, warn the user.

3. **Pre-merge check**:

   ```bash
   pre-commit run --all-files
   ```

   If any checks fail, report and stop.

4. **Squash-merge the PR**:

   ```bash
   gh pr merge <pr-number> --squash --delete-branch
   ```

5. **Close the GitHub issue**:

   ```bash
   gh issue close <issue-number> --comment "Resolved via PR #<pr-number>."
   ```

6. **Return to the milestone branch**:

   ```bash
   git checkout <milestone-branch>
   git pull origin <milestone-branch>
   ```

   `gh pr merge --delete-branch` usually deletes the local branch. If the local branch still exists, try to delete it. After a squash merge, `git branch -d` can refuse. If it refuses, tell the user that `<issue-branch>` remains, and do not use `-D`:

   ```bash
   git branch -d '<issue-branch>'
   ```

7. **List remaining milestone issues**:

   ```bash
   gh issue list --milestone "<milestone title>" --state open --json number,title
   ```

8. **Report**:
   - Issue closed, PR merged, branch deleted, now on milestone branch
   - Remaining open issues
   - If issues remain: "Run `/start-issue <number>` for the next issue"
   - If none remain: "All issues complete. Run `/finish-milestone <version>` to create the milestone PR"

## Important

- Never force-merge if pre-commit checks are failing.
- Issue PRs must target the milestone branch, not `main`.
