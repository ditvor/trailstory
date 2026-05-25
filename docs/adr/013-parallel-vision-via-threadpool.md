# ADR 013 — Parallel vision describer calls via ThreadPoolExecutor (Phase 3.2)

**Date:** 2026-05
**Status:** Accepted
**Decided by:** v0 product owner

---

## Context

[ADR-010](010-photo-grounding-via-vision.md) (Phase 3) wired the
vision describer into the pipeline. Per-photo calls run serially:
6-photo hike = 6 × ~1s vision latency = ~6 seconds before the writer
starts. That latency lands inside the web builder's "Generating…"
spinner, but the SSE first-byte time noticeably degraded vs Phase 2.

The describer calls are HTTP-bound (the Anthropic SDK blocks on the
network), perfectly parallel, and idempotent. The obvious fix is to
issue them concurrently. The question is which concurrency primitive
to use.

---

## Options considered

### Option A — ThreadPoolExecutor with synchronous SDK calls (chosen)

`concurrent.futures.ThreadPoolExecutor(max_workers=N).map(...)` over
the photo list. Each describer call holds the GIL only while
preparing the request and decoding the response; the long HTTP wait
releases the GIL, so other threads make real progress.

**Pros:**

- Zero changes to the existing `AnthropicClient`. The synchronous
  SDK is thread-safe (documented), so the existing `complete_vision`
  works as-is across threads.
- Order-preserving via `.map`. `describe_photos` returns photos in
  the same order they came in; tests pass `concurrency=1` for the
  rare cases where they need to assert mock-side-effect order.
- Configurable via `Settings.vision_concurrency` (default `4`).
  Operators throttle if they hit rate limits; increase if their
  Anthropic tier allows wider bursts.
- Failure semantics unchanged. Per-photo soft-fail still works
  inside the thread; the thread returns the photo with
  `description=None` exactly as before.
- Single-photo fast path. `describe_photos` skips the threadpool
  entirely when `len(photos) == 1` (or `concurrency <= 1`), so
  there's no per-thread overhead in the common-edge case.

**Cons:**

- Threads on the Anthropic SDK rely on its internal HTTP connection
  pool being thread-safe. Documented as such; if that ever changes,
  this design breaks loudly (errors per-thread). Acceptable risk.
- Bursting concurrent calls hits rate limits faster than serial.
  Default `concurrency=4` is conservative for v0; the existing
  per-client retry policy (3 attempts with backoff) absorbs the
  per-burst hiccups.

### Option B — Switch the entire client layer to asyncio

Refactor `AnthropicClient` to use `AsyncAnthropic`, expose
`complete_vision_async`, await per-photo coroutines via `asyncio.gather`.

**Pros:** Idiomatically concurrent. No thread pool needed.
**Cons:**

- Touches every caller. The CLI is synchronous; the web app is
  FastAPI (async-native but the pipeline today runs in sync
  context); the eval runner is synchronous. Async/sync coupling
  forces `asyncio.run` wrapping in awkward places.
- Bigger PR, more surface area to test, more places for await /
  no-await bugs to hide. The win (cleaner concurrency model) does
  not pay for the diff size at v0 traffic.
- The synchronous client is good for at least the next 2-3 phases of
  product work; deferring async to a real "we hit a perf wall"
  signal is the right move.

### Option C — Multiprocessing

`concurrent.futures.ProcessPoolExecutor`. Each photo gets its own
process; no GIL concerns.

**Pros:** Each call truly parallel even for CPU-bound work.
**Cons:**

- Vision calls are pure HTTP — there's no CPU work to parallelise.
  Multiprocessing pays setup cost (fork, IPC, pickling) without any
  payoff over threads on this workload.
- Pickling the AnthropicClient across processes is undefined
  behaviour by the SDK's docs. Each worker would need to construct
  its own client, doubling memory.

### Option D — Defer parallelism

Argue 6s latency is acceptable; do nothing.

**Pros:** Zero new code.
**Cons:** Latency budget is the most-felt UX regression from
Phase 3. The web spinner is already at the edge of "how long is
this taking" perception. A trivial-cost fix that drops 4s of
first-byte time is worth shipping.

---

## Decision

**Adopt Option A.** `describe_photos` uses a `ThreadPoolExecutor`
with `max_workers = min(Settings.vision_concurrency,
len(photos))`. Submission order is preserved via `.map`. Single-photo
case short-circuits the pool. `concurrency=1` is a valid value that
forces serial execution (tests use it for deterministic
side_effect consumption).

`Settings.vision_concurrency` defaults to `4` and is overridable via
the `VISION_CONCURRENCY` env var. Per-photo soft-fail semantics from
ADR-010 are unchanged — exceptions inside each thread are caught
inside `_describe_one_with_cache` and surfaced as `None` descriptions.

---

## Consequences

### What changes

- `trailstory/photos.py`:
  - `describe_photos` adds `concurrency: int = 4` kwarg.
  - When `concurrency > 1` and `len(photos) > 1`, uses
    `ThreadPoolExecutor(max_workers=min(concurrency,
    len(photos))).map(...)`.
  - Otherwise serial loop (single-photo fast path + explicit opt-out).
- `trailstory/config.py`: new `Settings.vision_concurrency: int =
  Field(default=4, ge=1)`.
- `trailstory/cli.py`: passes `concurrency=settings.vision_concurrency`
  to `describe_photos`.
- `tests/eval/run.py`: pins `concurrency=4` (matches default).
- `tests/test_photos.py`: tests that need ordered mock side-effects
  pass `concurrency=1` so behaviour is deterministic. A future
  parallel-specific test could exercise `concurrency=4` and assert
  call count + soft-fail behaviour without ordering assumptions.

### What becomes easier

- A future 12-photo hike completes in roughly the same wall time as
  a 6-photo one (3 batches of 4 instead of 6 serial calls).
- An operator can throttle (`VISION_CONCURRENCY=1`) without code
  changes if they hit rate limits.

### What becomes harder

- Tests that assert mock side-effect order must opt into serial
  execution (`concurrency=1`). The two test cases that needed
  re-tooling now have explicit comments explaining why.
- Wider concurrency means a single rate-limit error fans out into
  multiple threads' retry budgets, which can compound. The client's
  default retry policy (3 attempts, exponential backoff) handles
  the common case; an actual outage would still cascade — but the
  per-photo soft-fail policy ensures the whole render proceeds even
  if some descriptions are missing.

### Known limitations

- **No global concurrency budget.** If multiple renders happen
  simultaneously (e.g. a web instance under burst load), each one
  spins up its own pool of `concurrency` workers. v0 traffic doesn't
  warrant a global semaphore; Phase 3.3 (if it ever ships) could add
  one.
- **No async pipeline.** A future shift to FastAPI's native async
  flow (Phase 5+ scale) will want async vision calls. Threading
  bridges the gap until that scale signal actually arrives.
