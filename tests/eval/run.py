"""Programmatic narrative-quality eval runner.

For each case in ``tests/eval/cases/``: load the hike inputs, call
:func:`trailstory.llm.narrative.generate_narrative` with a **real**
``AnthropicClient``, apply the rubric in :mod:`tests.eval.rubric`, print a
per-case table, and exit non-zero if any rubric check failed.

Pass ``--live-judge`` to additionally call the paid LLM-as-judge layer in
:mod:`tests.eval.judge` after each rubric run. The judge uses a separate
client configured with ``EVAL_JUDGE_MODEL`` (defaulting to
``claude-sonnet-4-6`` — see :mod:`tests.eval.judge` for the rationale).
When a ``tests/eval/golden/<case>-judge.json`` exists, the runner prints
the per-axis delta and exits non-zero if any axis dropped by at least
``EVAL_REGRESSION_THRESHOLD`` (default ``1.0``).

Usage:

    python -m tests.eval.run --case 01-fixture-baseline
    python -m tests.eval.run --all
    python -m tests.eval.run --all --live-judge
    python -m tests.eval.run --all --live-judge --update-golden

This calls the real Anthropic API. **It costs money.** It also always
disables the narrative cache: a cache hit from a previous prompt version
would defeat the entire point of the eval — we want each run to exercise
the current ``llm/prompts.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from rich.console import Console
from rich.table import Table

from tests.eval.judge import (
    DEFAULT_JUDGE_MODEL,
    JudgeError,
    JudgeScore,
    judge_narrative,
)
from tests.eval.rubric import RubricResult, apply_rubric
from trailstory.config import load_settings
from trailstory.gpx import parse_gpx
from trailstory.llm.client import AnthropicClient
from trailstory.llm.narrative import NarrativeGenerationError, generate_narrative
from trailstory.models import HikeInput, NarrativeOutput
from trailstory.photos import load_photos

CASES_DIR = Path(__file__).parent / "cases"
GOLDEN_DIR = Path(__file__).parent / "golden"
# tests/eval/run.py → tests/eval → tests → repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Env-var names. Kept as constants so the drift between code and docs
# (CLAUDE.md, ADR-003) is grep-able.
ENV_JUDGE_MODEL: str = "EVAL_JUDGE_MODEL"
ENV_REGRESSION_THRESHOLD: str = "EVAL_REGRESSION_THRESHOLD"

# Drop in any judge axis ≥ this value vs golden is treated as a
# regression. Picked as a "noticeable but not catastrophic" gap on the
# 0-5 scale; a single missed paragraph or one flat axis usually shows up
# as a 1-1.5 swing in pilot runs. Override with EVAL_REGRESSION_THRESHOLD.
DEFAULT_REGRESSION_THRESHOLD: float = 1.0

# Axes inspected for delta-vs-golden, in display order. Single source of
# truth so the table and the regression check stay in sync. ``faithfulness``
# is a ``@computed_field`` on JudgeScore (derived from claim_verdicts), not
# a model field — it shows in the table like any other axis but is computed
# in Python, so adding/removing it here is the only change needed when its
# weighting or formula evolves. See ADR-007.
JUDGE_AXES: tuple[str, ...] = (
    "warmth",
    "narrative_arc",
    "russian_fidelity",
    "photo_selection_plausibility",
    "faithfulness",
)


@dataclass(frozen=True)
class EvalCase:
    """One row of the eval suite — name plus the hike inputs to feed the LLM."""

    name: str
    hike_input: HikeInput

    @classmethod
    def from_path(cls, path: Path) -> EvalCase:
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            name=data["name"],
            hike_input=HikeInput(
                gpx_path=REPO_ROOT / data["gpx_path"],
                photos_dir=REPO_ROOT / data["photos_dir"],
                seed_text=data["seed_text"],
                location_name=data.get("location_name"),
            ),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.run",
        description="Run the programmatic narrative-quality rubric. Calls the real Anthropic API.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--case",
        metavar="NAME",
        help="Run a single case by name (file stem in tests/eval/cases/).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run every case in tests/eval/cases/.",
    )
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help=(
            "Write the generated narrative to tests/eval/golden/<case>.json. "
            "When combined with --live-judge, also writes "
            "tests/eval/golden/<case>-judge.json."
        ),
    )
    parser.add_argument(
        "--live-judge",
        action="store_true",
        help=(
            "Also run the paid LLM-as-judge layer after each rubric run. "
            f"Uses ${ENV_JUDGE_MODEL} (default {DEFAULT_JUDGE_MODEL!r})."
        ),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    cases = _select_cases(args)
    if not cases:
        return 2

    settings = load_settings()
    writer_client = AnthropicClient(settings.anthropic_api_key, model=settings.model)

    judge_client: AnthropicClient | None = None
    threshold = DEFAULT_REGRESSION_THRESHOLD
    if args.live_judge:
        judge_model = os.environ.get(ENV_JUDGE_MODEL, DEFAULT_JUDGE_MODEL)
        judge_client = AnthropicClient(settings.anthropic_api_key, model=judge_model)
        threshold = _read_regression_threshold()

    console = Console()
    header = (
        f"[bold]running narrative eval[/bold] — writer={writer_client.model}, "
        f"{len(cases)} case(s), cache disabled"
    )
    if judge_client is not None:
        header += f", judge={judge_client.model}, regression threshold={threshold:.2f}"
    console.print(header)

    any_failed = False
    for case in cases:
        passed = _run_case(
            case,
            writer_client=writer_client,
            judge_client=judge_client,
            console=console,
            update_golden=args.update_golden,
            regression_threshold=threshold,
        )
        if not passed:
            any_failed = True

    return 1 if any_failed else 0


def _select_cases(args: argparse.Namespace) -> list[EvalCase]:
    if args.all:
        paths = sorted(CASES_DIR.glob("*.json"))
        if not paths:
            sys.stderr.write(f"error: no cases found in {CASES_DIR}\n")
        return [EvalCase.from_path(p) for p in paths]

    target = CASES_DIR / f"{args.case}.json"
    if not target.is_file():
        sys.stderr.write(f"error: case {args.case!r} not found at {target}\n")
        return []
    return [EvalCase.from_path(target)]


def _run_case(
    case: EvalCase,
    *,
    writer_client: AnthropicClient,
    judge_client: AnthropicClient | None,
    console: Console,
    update_golden: bool,
    regression_threshold: float,
) -> bool:
    """Run one case end-to-end. Return ``True`` iff every gate passed.

    Gates: rubric (always) and — when ``judge_client`` is set — the
    judge regression check vs ``tests/eval/golden/<case>-judge.json``.
    """
    console.rule(f"[bold cyan]{case.name}[/bold cyan]")

    try:
        gpx_stats = parse_gpx(case.hike_input.gpx_path)
    except Exception as exc:  # GpxParseError + missing-file IO errors
        console.print(f"[red]✗ gpx parse failed:[/red] {exc}")
        return False

    with TemporaryDirectory(prefix=f"trailstory-eval-{case.name}-") as tmp:
        try:
            photos = load_photos(case.hike_input.photos_dir, Path(tmp))
        except Exception as exc:
            console.print(f"[red]✗ photos load failed:[/red] {exc}")
            return False

        try:
            narrative = generate_narrative(
                case.hike_input,
                gpx_stats,
                photos,
                client=writer_client,
                use_cache=False,
            )
        except NarrativeGenerationError as exc:
            console.print(f"[red]✗ narrative generation failed:[/red] {exc}")
            return False

    results = apply_rubric(narrative, n_photos=len(photos))
    _print_rubric_table(console, results)
    rubric_passed = all(r.passed for r in results)

    judge_passed = True
    judge_score: JudgeScore | None = None
    if judge_client is not None:
        try:
            judge_score = judge_narrative(narrative, case.hike_input, client=judge_client)
        except JudgeError as exc:
            console.print(f"[red]✗ judge failed:[/red] {exc}")
            return False

        golden = _read_golden_judge(case.name)
        _print_judge_table(console, judge_score, golden)
        if golden is not None:
            judge_passed = _judge_did_not_regress(
                console, judge_score, golden, threshold=regression_threshold
            )

    if update_golden:
        _write_golden(case.name, narrative)
        console.print(f"[dim]→ wrote tests/eval/golden/{case.name}.json[/dim]")
        if judge_score is not None:
            _write_golden_judge(case.name, judge_score)
            console.print(f"[dim]→ wrote tests/eval/golden/{case.name}-judge.json[/dim]")

    return rubric_passed and judge_passed


# ── rubric printing ──────────────────────────────────────────────────────────


def _print_rubric_table(console: Console, results: list[RubricResult]) -> None:
    table = Table(show_lines=False, header_style="bold")
    table.add_column("check", style="bold", no_wrap=True)
    table.add_column("result", no_wrap=True)
    table.add_column("detail", overflow="fold")
    for r in results:
        mark = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        table.add_row(r.name, mark, r.detail)
    console.print(table)
    failed = [r.name for r in results if not r.passed]
    if failed:
        console.print(f"[red]{len(failed)} check(s) failed:[/red] {', '.join(failed)}")
    else:
        console.print("[green]all checks passed[/green]")


# ── judge printing & regression ──────────────────────────────────────────────


def _print_judge_table(
    console: Console,
    score: JudgeScore,
    golden: JudgeScore | None,
) -> None:
    table = Table(show_lines=False, header_style="bold", title="judge scores (0-5)")
    table.add_column("axis", style="bold", no_wrap=True)
    table.add_column("score", no_wrap=True, justify="right")
    if golden is not None:
        table.add_column("golden", no_wrap=True, justify="right")
        table.add_column("Δ", no_wrap=True, justify="right")
    for axis in JUDGE_AXES:
        fresh = float(getattr(score, axis))
        row = [axis, f"{fresh:.2f}"]
        if golden is not None:
            base = float(getattr(golden, axis))
            delta = fresh - base
            row.append(f"{base:.2f}")
            row.append(_format_delta(delta))
        table.add_row(*row)
    console.print(table)
    if score.notes:
        console.print(f"[dim]judge notes:[/dim] {score.notes}")


def _format_delta(delta: float) -> str:
    if delta > 0:
        return f"[green]+{delta:.2f}[/green]"
    if delta < 0:
        return f"[red]{delta:.2f}[/red]"
    return "0.00"


def _judge_did_not_regress(
    console: Console,
    score: JudgeScore,
    golden: JudgeScore,
    *,
    threshold: float,
) -> bool:
    """Return False (and log) if any axis dropped by ≥ ``threshold`` vs golden."""
    regressed: list[tuple[str, float, float]] = []
    for axis in JUDGE_AXES:
        fresh = float(getattr(score, axis))
        base = float(getattr(golden, axis))
        if base - fresh >= threshold:
            regressed.append((axis, base, fresh))

    if regressed:
        bits = ", ".join(f"{axis} {base:.2f}→{fresh:.2f}" for axis, base, fresh in regressed)
        console.print(f"[red]✗ judge regression (≥ {threshold:.2f}):[/red] {bits}")
        return False

    console.print(f"[green]judge non-regressing (threshold {threshold:.2f})[/green]")
    return True


# ── golden I/O ───────────────────────────────────────────────────────────────


def _write_golden(name: str, narrative: NarrativeOutput) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    payload = narrative.model_dump(mode="json")
    (GOLDEN_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_golden_judge(name: str, score: JudgeScore) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    payload = score.model_dump(mode="json")
    (GOLDEN_DIR / f"{name}-judge.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_golden_judge(name: str) -> JudgeScore | None:
    path = GOLDEN_DIR / f"{name}-judge.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return JudgeScore.model_validate(data)


# ── env parsing ──────────────────────────────────────────────────────────────


def _read_regression_threshold() -> float:
    """Read ``EVAL_REGRESSION_THRESHOLD`` env var, defaulting to 1.0.

    Falls back to the default and warns on stderr if the variable is set
    but cannot be parsed as a positive float — better than silently
    using a meaningless threshold.
    """
    raw = os.environ.get(ENV_REGRESSION_THRESHOLD)
    if raw is None or raw == "":
        return DEFAULT_REGRESSION_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        sys.stderr.write(
            f"warning: {ENV_REGRESSION_THRESHOLD}={raw!r} is not a float; "
            f"falling back to {DEFAULT_REGRESSION_THRESHOLD}\n"
        )
        return DEFAULT_REGRESSION_THRESHOLD
    if value <= 0:
        sys.stderr.write(
            f"warning: {ENV_REGRESSION_THRESHOLD}={raw!r} must be > 0; "
            f"falling back to {DEFAULT_REGRESSION_THRESHOLD}\n"
        )
        return DEFAULT_REGRESSION_THRESHOLD
    return value


if __name__ == "__main__":
    raise SystemExit(main())
