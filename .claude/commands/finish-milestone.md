# Finish Milestone

Create a PR to merge a completed milestone branch into main. Includes a docs audit.

Replace each `<name>` placeholder in this file with the real value before you use it.

## Arguments

- `$ARGUMENTS` — the milestone version number (e.g., `v1.2.0`)

## Workflow

1. **Verify the milestone branch exists**:

   ```bash
   git branch -a | grep "milestone/$ARGUMENTS"
   ```

2. **Check for open issues** in the milestone:

   ```bash
   gh issue list --milestone "<milestone-title>" --state open --json number,title
   ```

   If open issues exist, warn and ask whether to proceed or finish them first.

3. **Switch to the milestone branch**:

   ```bash
   git checkout milestone/$ARGUMENTS
   git pull origin milestone/$ARGUMENTS
   ```

4. **Gather merged PRs** for the PR body:

   ```bash
   gh pr list --base milestone/$ARGUMENTS --state merged --json number,title,url
   ```

5. **Get the diff summary**:

   ```bash
   git diff --name-only main...milestone/$ARGUMENTS
   git log main..milestone/$ARGUMENTS --oneline
   ```

6. **Run validation**:

   ```bash
   pre-commit run --all-files
   ```

   If checks fail, report and stop.

   If any pipeline scripts changed (`scripts/*.py`, `pipeline.sh`, `fetch.sh`, `fetch_docker.sh`, `entrypoint.sh`), launch the **pipeline-reviewer** subagent (`.claude/agents/pipeline-reviewer.md`) for a final review. Address any CRITICAL issues before proceeding.

7. **Quick docs audit** (replaces standalone `/docs-update`):

   Review `CLAUDE.md` against the milestone's changes. Check for needed updates:
   - New scripts or files → directory structure section
   - Pipeline flow changes → pipeline section
   - New environment variables → CLAUDE.md + .env.sample
   - Plan generation rule changes → plan rules section
   - Pairing engine changes → pairing engine section
   - Docker/deployment changes → deployment section

   If `scripts/wine_keywords.py` or `scripts/pairing.py` changed, also check `docs/menu-guide.md`.

   Make targeted edits and commit:

   ```bash
   git add CLAUDE.md docs/
   git commit -m "docs: update documentation for $ARGUMENTS"
   ```

   Skip the commit if nothing changed.

8. **Create the PR** targeting `main`:

   ```bash
   gh pr create \
     --base main \
     --head milestone/$ARGUMENTS \
     --title "$ARGUMENTS — <milestone-title>" \
     --body "$(cat <<'EOF'
   ## Summary

   <short-description>

   ## Issues Resolved

   - #<issue-number> — <issue-title> (PR #<pr-number>)

   ## Test Plan

   - [ ] All issue PRs reviewed and merged
   - [ ] `pre-commit run --all-files` passes
   - [ ] `python3 -m pytest tests/ -v` passes
   - [ ] Docker build succeeds: `docker compose build`

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

9. **Suggest next steps**:
   - "Review the PR, then run `/release-milestone $ARGUMENTS` to merge, tag, and release"
