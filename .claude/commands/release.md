---
description: Automated release workflow with version analysis, release notes generation, and GitHub release
---

# Release Command

When the user runs `/release`, execute this comprehensive release workflow:

## Phase 1: Pre-Flight Checks & Analysis

1. **Verify Prerequisites**
   - Check the branch: `git branch --show-current`. The output must be exactly `main`. If not, stop.
   - Check the working tree: `git status --porcelain`. The output must be empty. If not, stop.
   - Check the sync with origin: run `git fetch origin`. If the fetch fails, stop. Then compare `git rev-parse HEAD` with `git rev-parse origin/main`. The two hashes must be equal. If not, stop.
   - If a check fails, tell the user which check failed. Do not continue the release.

2. **Get Current State**
   - Get last release tag: `git describe --tags --abbrev=0`
   - Count commits since last release: `git rev-list --count [last_tag]..HEAD`

3. **Analyze Commits**
   - Breaking changes: grep for "BREAKING" or "breaking"
   - Features: grep for `^[a-f0-9]+ (feat|feature):`
   - Fixes: grep for `^[a-f0-9]+ (fix|hotfix|bug):`
   - Get changed files: `git diff --name-only [last_tag]..HEAD`
   - Get resolved issues: extract #NNN from commits
   - Get merged PRs: `gh pr list --state merged --search "merged:>[last_release_date]"`

4. **Security Review**
   - If more than 50 changed files, warn and offer to skip
   - Launch the security-reviewer agent via the Agent tool with
     `subagent_type: "security-reviewer"` to audit changed `.py`, `.js`, and `.sh`
     files since the last release tag
   - If Critical or High severity issues are found, **halt the release**
   - If only Medium/Low or no issues, proceed

5. **Detect Deployment Implications**

   Scan changed files and flag any that require extra steps on docker02:

   - `Dockerfile` changed → requires `docker compose up --build -d` (not just down/up)
   - `requirements.txt` changed → note: container rebuild handles pip install
   - `.env.sample` changed → remind user to check `.env` on docker02 for new vars
   - `entrypoint.sh` changed → requires container restart to take effect

6. **Recommend Version Bump**
   - Breaking changes → major
   - New features → minor
   - Bug fixes only → patch
   - Show recommendation with reasoning
   - Ask user for approval or manual override

## Phase 2: Generate Release Notes

**Use the Agent tool to analyze changes and generate comprehensive release notes:**

1. **Analyze Repository Changes**
   - Read recent commits and their full messages
   - Get PR descriptions from GitHub
   - Get issue details from GitHub
   - Analyze changed files by category

2. **Generate Complete Sections**

   ### Required Actions After Deployment

   Detect and document any manual steps needed on docker02:
   - `Dockerfile` changed: `docker compose up --build -d` (full rebuild required)
   - `.env.sample` changed: verify `.env` on docker02 has new vars
   - Otherwise: container self-updates via git pull on next scheduled run,
     or run `docker compose down && docker compose up -d` for immediate deploy
   - Format: Clear numbered steps with commands

   ### Changes

   Extract and describe:
   - New features from `feat:` commits with detailed descriptions
   - Bug fixes from `fix:` commits with impact explanation
   - Pipeline changes (scoring, pairing, plan generation, digest)
   - Frontend/UI changes (site/index.html, style.css)
   - Infrastructure changes (Docker, nginx, entrypoint, supercronic)
   - Format: Bullet points grouped by area

   ### Issues Resolved

   - List all #NNN issues with titles from GitHub

   ### Technical Summary

   - Statistics: X commits, Y files changed
   - Major technical changes
   - Test coverage changes

3. **Save and Present to User for Review**
   - Write the generated notes to `RELEASE_NOTES.md` at the repo root.
   - Show the generated release notes in the conversation.
   - Ask: "Review the release notes above. Reply with 'approve' to continue,
     'edit' to modify, or provide specific changes."
   - If the user says 'edit', open `RELEASE_NOTES.md` in their editor
     (try `${EDITOR:-code} --wait RELEASE_NOTES.md`; fall back to instructing
     the user to edit the file manually if no editor is available).
   - Wait for user approval.

## Phase 3: Execute Release

1. **Create Release Commit**

   ```bash
   git commit --allow-empty -m "$(cat <<'EOF'
   Release: v[VERSION]

   [Brief summary from release notes]

   🤖 Generated with Claude Code (https://claude.com/claude-code)

   Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
   EOF
   )"
   ```

   (Use `--allow-empty` only if there are no staged changes; otherwise stage release notes first.)

2. **Create Annotated Tag**

   ```bash
   git tag -a v[VERSION] -m "Release v[VERSION]: [Brief description]

   [Key highlights from release notes]"
   ```

3. **Confirm Push**

   Show summary:
   ```text
   Ready to push:
   - Tag: v[VERSION]
   - Destination: origin/main
   ```

   Ask: "Push to origin and create GitHub release? (yes/no)"

4. **Push to Origin**
   ```bash
   git push origin main
   git push origin v[VERSION]
   ```

5. **Create GitHub Release**

   Use the curated `RELEASE_NOTES.md` produced in Phase 2 — do **not** pass
   `--generate-notes`, which would discard them in favor of GitHub's
   auto-generated commit summary.

   ```bash
   gh release create v[VERSION] \
     --title "The Sommelier v[VERSION]" \
     --notes-file RELEASE_NOTES.md
   ```

6. **Provide Deployment Instructions**

   Show these steps to the user. Do not run them.

   Release v[VERSION] created

   Deploy to docker02.

   If the Dockerfile changed, rebuild the container:

   ```bash
   ssh docker02
   cd /home/ansible/container/the-sommelier
   docker compose up --build -d
   ```

   Otherwise (code or script changes only), the container updates itself
   on the next scheduled run. Runs happen every 30 minutes from 06:00 to
   22:00 Pacific. To deploy now:

   ```bash
   ssh docker02
   cd /home/ansible/container/the-sommelier
   docker compose down && docker compose up -d
   ```

   GitHub Release: [URL]

   If `.env.sample` changed, check `.env` on docker02. New variables may be required.

## Error Handling

If any step fails:
- Stop immediately and show clear error message
- If tag created but push failed: show commands to complete or rollback
- If push succeeded but GitHub release failed: show command to create release manually
