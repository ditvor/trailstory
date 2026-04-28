"""Programmatic narrative-quality eval runner.

For each case in ``tests/eval/cases/``: load the hike inputs, call
:func:`trailstory.llm.narrative.generate_narrative` with a **real**
``AnthropicClient``, apply the rubric in :mod:`tests.eval.rubric`, print a
per-case table, and exit non-zero if any rubric check failed.

Usage:

    python -m tests.eval.run --case 01-fixture-baseline
    python -m tests.eval.run --all
    python -m tests.eval.run --all --update-golden

This calls the real Anthropic API. **It costs money.** It also always
disables the narrative cache: a cache hit from a previous prompt version
would defeat the entire point of the eval — we want each run to exercise
the current ``llm/prompts.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from rich.console import Console
from rich.table import Table

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
                baby_name=data["baby_name"],
                baby_age_months=data["baby_age_months"],
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
        help="Write the generated narrative to tests/eval/golden/<case>.json.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    cases = _select_cases(args)
    if not cases:
        return 2

    settings = load_settings()
    client = AnthropicClient(settings.anthropic_api_key, model=settings.model)

    console = Console()
    console.print(
        f"[bold]running narrative eval[/bold] — model={client.model}, "
        f"{len(cases)} case(s), cache disabled"
    )

    any_failed = False
    for case in cases:
        passed = _run_case(case, client=client, console=console, update_golden=args.update_golden)
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
    client: AnthropicClient,
    console: Console,
    update_golden: bool,
) -> bool:
    """Run one case end-to-end. Return ``True`` iff every rubric check passed."""
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
                client=client,
                use_cache=False,
            )
        except NarrativeGenerationError as exc:
            console.print(f"[red]✗ narrative generation failed:[/red] {exc}")
            return False

    results = apply_rubric(narrative, n_photos=len(photos))
    _print_table(console, results)

    if update_golden:
        _write_golden(case.name, narrative)
        console.print(f"[dim]→ wrote tests/eval/golden/{case.name}.json[/dim]")

    return all(r.passed for r in results)


def _print_table(console: Console, results: list[RubricResult]) -> None:
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


def _write_golden(name: str, narrative: NarrativeOutput) -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    payload = narrative.model_dump(mode="json")
    (GOLDEN_DIR / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
