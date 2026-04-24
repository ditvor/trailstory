#!/usr/bin/env bash
# setup_github.sh
#
# Creates the Trailstory GitHub repository and configures it correctly.
# Requires the GitHub CLI: https://cli.github.com/
#
# Usage:
#   chmod +x scripts/setup_github.sh
#   ./scripts/setup_github.sh
#
# What it does:
#   1. Creates a private GitHub repository
#   2. Pushes the initial commit
#   3. Creates and pushes the develop branch
#   4. Sets branch protection rules on main and develop
#   5. Creates standard issue labels
#   6. Sets the ANTHROPIC_API_KEY secret for CI
#
# Prerequisites:
#   - gh auth login  (authenticate GitHub CLI first)
#   - git init + initial commit already made locally
#   - ANTHROPIC_API_KEY set in your .env file

set -euo pipefail

# ── Config — edit these ────────────────────────────────────────────────────────
REPO_NAME="trailstory"
REPO_DESCRIPTION="Turn a hike into a memory worth keeping — and sharing."
REPO_VISIBILITY="private"   # change to "public" when ready to open-source
GITHUB_USERNAME="$(gh api user --jq '.login')"
# ──────────────────────────────────────────────────────────────────────────────

echo "→ Setting up Trailstory repository for @${GITHUB_USERNAME}"

# ── 1. Create repository ───────────────────────────────────────────────────────
echo "→ Creating GitHub repository..."
gh repo create "${REPO_NAME}" \
  --description "${REPO_DESCRIPTION}" \
  --"${REPO_VISIBILITY}" \
  --source=. \
  --remote=origin \
  --push

echo "✓ Repository created: https://github.com/${GITHUB_USERNAME}/${REPO_NAME}"

# ── 2. Create and push develop branch ─────────────────────────────────────────
echo "→ Creating develop branch..."
git checkout -b develop
git push -u origin develop
git checkout main

echo "✓ develop branch created and pushed"

# ── 3. Branch protection — main ────────────────────────────────────────────────
echo "→ Configuring branch protection for main..."
gh api \
  --method PUT \
  "repos/${GITHUB_USERNAME}/${REPO_NAME}/branches/main/protection" \
  --input - <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["quality"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false
}
EOF

echo "✓ main branch protected"

# ── 4. Branch protection — develop ────────────────────────────────────────────
echo "→ Configuring branch protection for develop..."
gh api \
  --method PUT \
  "repos/${GITHUB_USERNAME}/${REPO_NAME}/branches/develop/protection" \
  --input - <<EOF
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["quality"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF

echo "✓ develop branch protected"

# ── 5. Labels ──────────────────────────────────────────────────────────────────
echo "→ Creating labels..."

# Delete GitHub defaults that we don't use
for label in "good first issue" "help wanted" "invalid" "question" "wontfix" "duplicate"; do
  gh label delete "${label}" --yes 2>/dev/null || true
done

create_label() {
  local name="$1" color="$2" description="$3"
  gh label create "${name}" --color "${color}" --description "${description}" \
    --force 2>/dev/null || true
}

create_label "type: feature"   "0075ca" "New capability"
create_label "type: fix"       "d93f0b" "Bug fix"
create_label "type: chore"     "e4e669" "Maintenance, deps, CI"
create_label "type: docs"      "0052cc" "Documentation only"
create_label "priority: high"  "b60205" "Blocking or time-sensitive"
create_label "priority: low"   "cfd3d7" "Nice to have"
create_label "needs: review"   "fbca04" "Ready for review"
create_label "needs: tests"    "e11d48" "Missing test coverage"
create_label "llm"             "7c3aed" "Relates to prompt or LLM logic"
create_label "breaking change" "ff0000" "Requires migration or major version bump"

echo "✓ Labels created"

# ── 6. Repository secret for CI ───────────────────────────────────────────────
echo "→ Setting ANTHROPIC_API_KEY secret for CI..."

# Read key from .env file
if [ -f .env ]; then
  ANTHROPIC_API_KEY_VALUE=$(grep -E '^ANTHROPIC_API_KEY=' .env | cut -d '=' -f2-)
  if [ -n "${ANTHROPIC_API_KEY_VALUE}" ]; then
    gh secret set ANTHROPIC_API_KEY --body "${ANTHROPIC_API_KEY_VALUE}"
    echo "✓ ANTHROPIC_API_KEY secret set"
  else
    echo "⚠ ANTHROPIC_API_KEY not found in .env — set it manually:"
    echo "  gh secret set ANTHROPIC_API_KEY"
  fi
else
  echo "⚠ No .env file found. Set the secret manually:"
  echo "  gh secret set ANTHROPIC_API_KEY"
fi

# ── 7. Set default branch ──────────────────────────────────────────────────────
echo "→ Setting default branch to develop..."
gh api \
  --method PATCH \
  "repos/${GITHUB_USERNAME}/${REPO_NAME}" \
  -f default_branch=develop

echo "✓ Default branch set to develop"

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✓ Trailstory repository is ready."
echo ""
echo "  Repository:  https://github.com/${GITHUB_USERNAME}/${REPO_NAME}"
echo "  Branches:    main (protected), develop (default)"
echo "  Next step:   open Claude Code, point it at this repo, and start with Step 1"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
