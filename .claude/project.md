# Project facts for devkit commands

The flow commands read this file. Claude Code does not load it in a normal
session. Each fact is one line: a dash, the key, a colon, and the value in
backticks. The keys are in
<https://github.com/unibrain1/devkit/blob/main/docs/project-md.md>.

- test: `python3 -m pytest tests/ -v --tb=short`
- lint: `pre-commit run --all-files`
- plans: `plans`
- release_notes: `docs/releases/{version}.md`
- private_paths: `.env scripts/plan_config.py docker-compose.override.yml`
- deploy: `Local deploy skill (.claude/skills/deploy, not in git)`
- vault: `Projects/The Sommelier/The Sommelier.md`
- milestone_checks: `scripts/check-release-notes.sh`
- release_checks: `scripts/check-release-notes.sh`
