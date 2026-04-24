# ADR 001 — Embed photos as base64 data URIs in the HTML output

**Date:** 2025-04  
**Status:** Accepted  
**Decided by:** initial architecture

---

## Context

The output of Trailstory is an HTML memory page containing 6–8 selected photos.
We need to decide how those photos are included in the HTML file.

The primary distribution scenario is:
- Parent generates the HTML file locally
- File is shared with family in Russia via WhatsApp, email, or a direct link
- Recipients open it in a standard browser, often without a stable internet connection

The secondary distribution scenario is Instagram: the parent also wants to post from the same workflow.

---

## Options considered

### Option A — Relative image paths (`<img src="./photos/photo1.jpg">`)

The HTML file references photos by relative path. Photos must accompany the HTML.

**Pros:** Smaller HTML file, photos can be swapped.  
**Cons:** Sharing requires sending a folder or zip. Most messaging apps and email clients
strip or break relative paths when the files are not packaged together. Not linkable.

### Option B — Hosted image URLs

Photos are uploaded to a hosting service and the HTML references absolute URLs.

**Pros:** Small HTML file, linkable.  
**Cons:** Requires a server or cloud storage account. Adds infrastructure dependency.
Images may become unavailable if the hosting service changes. Does not work offline.
Does not work if the hosting domain is blocked in Russia.

### Option C — Base64 data URIs (chosen)

Photos are embedded directly in the HTML as `data:image/jpeg;base64,...` strings.

**Pros:**
- Truly self-contained — one file, nothing else needed
- Works offline, in any browser
- Works in Russia without any CDN or server access
- Sendable as a single file attachment
- Hostable on any static server or GitHub Pages without additional assets

**Cons:**
- Larger file size (~33% overhead from base64 encoding)
- Cannot be cached independently by the browser
- Cannot be swapped after generation

---

## Decision

Use Option C (base64 data URIs).

The file size overhead is acceptable: 6 photos at 200KB each (after resizing to 1800px) is
~1.2MB of image data, producing an HTML file of ~1.6MB. This is within WhatsApp's file sharing
limit and acceptable as an email attachment.

The core requirement — that the file works for family in Russia without any external dependency —
outweighs the size disadvantage.

---

## Consequences

- `renderers/html.py` encodes each selected photo before passing it to the Jinja2 template.
- Photos are resized to max 1800px on the longest edge before encoding (see `photos.py`).
- The template uses `<div style="background-image: url('data:image/jpeg;base64,...')">` for
  photo slots rather than `<img>` tags, to allow CSS `object-fit` styling.
- If we ever add a "hosted mode" (Option B), it must be opt-in via a `--hosted` flag.
  The default must remain self-contained.
