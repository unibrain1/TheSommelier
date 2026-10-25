---
description: Create a new GitHub issue with PM-driven scope refinement and expert input
---

# Create Issue Command

This command helps create well-defined GitHub issues by engaging specialized
agents to refine scope, architecture, testing, and documentation requirements.

Replace each `<name>` placeholder in this file with the real value before you use it.

## Workflow Steps

### Step 1: Get the Problem Statement

If the user provided a problem statement with the command, use it. Otherwise ask:

"What problem or feature would you like to create an issue for? Describe it in
your own words — I'll help refine it into a well-structured issue."

Wait for their response before proceeding.

### Step 2: Initial Research

Launch Explore agents to understand the relevant parts of the codebase:

- One agent to find code areas related to the problem statement
- One agent to check for existing related issues or prior art

Replace `<keywords>` with keywords from the user's problem statement.

```bash
gh issue list --state all --search "<keywords>" --limit 10
```

Check for duplicate or related issues. If found, inform the user:

"I found these related issues: <related-issues>. Should we continue with a new issue,
or does one of these already cover your need?"

Wait for confirmation before proceeding.

### Step 3: PM-Driven Scope Definition

> **Note:** The role-based agents below (`senior-product-manager`,
> `senior-architect`, `senior-test-engineer`, `technical-documentation-writer`)
> come from an installed agent plugin and are not defined locally in
> `.claude/agents/`. If they are unavailable, fall back to the generic `Agent`
> tool with a role-specific prompt.

Launch the **senior-product-manager** agent with:

- The user's problem statement
- The Explore results (codebase context)
- Any related issues found

Ask the PM agent to produce:

1. A draft issue title (concise, actionable)
2. A draft description with:
   - **Problem statement**: What's wrong or what's needed
   - **Proposed solution**: High-level approach
   - **Acceptance criteria**: Specific, testable conditions for "done"
   - **Out of scope**: What this issue explicitly does NOT cover
3. Suggested labels (bug, enhancement, tech-debt, etc.)
4. Suggested milestone (if applicable)
5. Questions the PM needs answered to finalize scope

### Step 4: Interview — Ask Questions One at a Time

Present the PM's draft to the user, then ask the PM's questions **one at a
time** using the following approach:

- Present each question clearly with context for why it matters
- When providing options, indicate the best known practice or industry standard
- Wait for each answer before asking the next question
- After each answer, determine if follow-up questions are needed

**Do NOT batch questions.** Ask them individually and let each answer inform
the next question.

Continue until scope is fully clarified.

### Step 5: Expert Refinement

Once the user has answered the PM's questions, launch these agents **in
parallel** to refine the issue from their perspectives:

- **senior-architect**: Review the proposed scope for:
  - Technical feasibility and complexity estimate (S/M/L/XL)
  - Architecture risks or concerns
  - Dependencies on other systems or issues
  - Suggested implementation approach

- **senior-test-engineer**: Review the proposed scope for:
  - Testability of the acceptance criteria
  - Test types needed (unit, integration)
  - Existing test coverage in affected areas
  - Potential regression risks

- **technical-documentation-writer**: Review the proposed scope for:
  - Documentation that will need updating (CLAUDE.md, docs/menu-guide.md)
  - Developer documentation needs

Wait for all agents to complete.

### Step 6: Synthesize and Present Final Draft

Incorporate expert feedback into the issue. Present the final draft to the
user with:

1. **Title**
2. **Description** (problem, solution, acceptance criteria, out of scope)
3. **Labels**
4. **Milestone** (if applicable)
5. **Expert Notes** section summarizing key input from agents:
   - Architecture considerations
   - Complexity estimate
   - Test requirements
   - Documentation impact

Ask: "Here's the refined issue. Would you like to change anything before I
create it?"

If the user requests changes, update the draft and re-present. If the experts
raised concerns that the user hasn't addressed, flag them:

"The architect noted <concern>. Should we address this in the issue scope or
create a separate issue for it?"

### Step 7: Create the Issue

Once the user approves, create the issue on GitHub. Pass each approved label
with a separate `--label` flag, and include `--milestone` if one was selected:

```bash
gh issue create \
  --title "Issue title" \
  --label "<label>" \
  --milestone "<milestone-title>" \
  --body "$(cat <<'EOF'
## Problem

Description of the problem or need.

## Proposed Solution

High-level approach.

## Acceptance Criteria

- [ ] Criterion 1
- [ ] Criterion 2
- [ ] Criterion 3

## Out of Scope

- Item 1
- Item 2

## Technical Notes

- **Complexity:** S/M/L/XL
- **Architecture:** Key considerations from architect
- **Testing:** Required test types
- **Documentation:** Docs that need updating
EOF
)"
```

Omit `--milestone` if none was chosen, and omit `--label` flags if no labels
were approved.

After creation, display the issue URL and number.

### Step 8: Offer Next Steps

After creating the issue, ask:

"Issue #<issue-number> created: <issue-url>

Would you like to:

1. Start working on it now? (`/start-issue <issue-number>`)
2. Create another related issue?
3. That's all for now."
