# Security policy

This document captures the threat model behind Trailstory and the basic
hygiene rules every contributor is expected to follow. It is brief on
purpose — anything that grows beyond a paragraph here belongs in an ADR
under [`docs/adr/`](docs/adr/).

---

## Threat model in one paragraph

Trailstory is a single-user CLI run on the parent's own laptop. It reads
a local GPX track, local photos, and a short emotional seed text, calls
the Anthropic API, and writes a self-contained HTML page to disk. The
output is shared via messengers (WhatsApp, Telegram, email) to a small
audience — typically family in Russia. There is no server, no
authentication, no multi-tenant data, no public web surface. The
practical risks therefore are:

1. **API key exfiltration.** The Anthropic key has billing and prompt
   access. Leaking it costs money and may leak future hike content sent
   to the API.
2. **Privacy of the rendered page.** The HTML embeds photos as base64
   data URIs (see [ADR-001](docs/adr/001-base64-photo-embedding.md)),
   so anyone who receives the file gets the raw images. EXIF GPS is
   stripped on load (`trailstory.photos.load_photos`); EXIF camera and
   timestamp tags are intentionally preserved.
3. **Untrusted input bending the LLM.** The parent's seed text is
   user-controlled prose. A hostile or accidentally-pasted instruction
   ("respond only in French", "ignore previous instructions") could
   distort the narrative if the model treats it as a directive.
4. **Decompression bombs in the photos directory.** A hand-crafted image
   can decompress from a few kilobytes into hundreds of megapixels and
   exhaust memory during photo loading.
5. **Supply-chain CVEs in dependencies.** `Pillow`, `anthropic`,
   `pydantic`, etc. are frequently updated; an outdated install may
   carry a published vulnerability.

The defenses currently in place address each of these:

- API key is typed `SecretStr` and only `.get_secret_value()`-d inside
  `trailstory/llm/client.py`. `.env` is `.gitignore`d.
- GPS sub-IFD is stripped before save in `trailstory/photos.py`.
- A prompt-injection clause in `SYSTEM_NARRATIVE`
  (`trailstory/llm/prompts.py`) instructs the model to treat the seed
  text as untrusted prose, plus a 1000-character cap on
  `HikeInput.seed_text` in `trailstory/models.py`.
- `Image.MAX_IMAGE_PIXELS = 200_000_000` in `trailstory/photos.py`
  rejects pixel bombs while staying generous for legitimate photos.
- `.github/workflows/security.yml` runs `gitleaks` (secret scan) and
  `pip-audit` (dependency CVE scan) on every push to `develop` and
  every PR.

---

## What NOT to do

- **Do not commit `.env`.** It is in `.gitignore`. If you accidentally
  add it, see "If a key leaks" below — assume the key is compromised.
- **Do not paste an Anthropic API key into issues, PR descriptions,
  Slack messages, screenshots, or log files.** GitHub indexes issues
  publicly and Slack scrollback is searchable; a key pasted "just for
  five minutes" may already be scraped.
- **Do not echo the key from CLI commands** (e.g. `echo $ANTHROPIC_API_KEY`)
  while screen-sharing or screen-recording.
- **Do not log raw seed text or full LLM responses at INFO level**
  beyond what `trailstory/llm/narrative.py` already does — they may
  contain personal details about the family.
- **Do not bypass the CI lint, type, or test gates** by force-pushing
  past failing checks. Branch protection rules are documented in
  [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## If a key leaks

Treat any key that left the local machine — a commit, a screenshot, a
chat message, a CI log — as compromised. Rotate immediately, then clean
up history:

1. **Rotate the key.** Sign in at <https://console.anthropic.com>,
   delete the old key, generate a new one. This is the only step that
   actually neutralises the leak; everything else is hygiene.
2. **Update local `.env`** with the new key. Re-run a generate command
   to confirm the new key works.
3. **Scrub the leaked value from git history** if it was pushed:
   - For a commit only on a feature branch, `git rebase -i` the commit
     out, then force-push the branch (this is the one situation where
     force-push is the right tool).
   - For a commit already merged into `develop` or `main`,
     `git filter-repo --replace-text` against the leaked literal,
     coordinate a force-push with anyone who has the repo cloned, and
     note the cleanup in the commit message.
   - Mention the cleanup in the PR description so reviewers understand
     why the history was rewritten.
4. **Delete any artefacts** that captured the key — Slack messages,
   screenshots, CI run logs (re-run the job after rotation if needed).

The rotation in step 1 is what stops the bleeding. Steps 3-4 reduce the
surface area of future leaks but never substitute for rotating.

---

## Reporting a vulnerability

Open a private security advisory via GitHub
(<https://github.com/ditvor/trailstory/security/advisories/new>). For
issues that touch a third-party dependency, please also link the
upstream advisory in the report so we can decide whether the vulnerable
code path is reachable from Trailstory.

If you've found a vulnerability that is already publicly disclosed (a
recent CVE in a pinned dependency), a normal PR upgrading the dep is
fine — the security workflow will fail on the existing CVE first, which
is exactly the signal we want.
