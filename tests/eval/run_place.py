"""Paid eval runner for the ADR-017 place-context stitch.

For each fixed case — a :class:`~trailstory.place.PlaceReference` plus the
hiker's beats — call :func:`trailstory.llm.place.generate_place_context`
with a **real** ``AnthropicClient``, apply the place rubric, print the
summaries + a rubric table, and exit non-zero if any check failed.

The cases hard-code the ``PlaceReference`` rather than reverse-geocoding, so
the eval measures the *stitch* (the LLM step) in isolation — the way
``make eval`` measures the writer. Geocoding + Wikipedia parsing are covered
by the free unit tests in ``tests/test_place.py``; the rubric itself by
``tests/test_eval_place_rubric.py``.

Usage::

    python -m tests.eval.run_place        # or: make eval-place

This calls the real Anthropic API. **It costs money** — one
``Settings.place_model`` call per case (Haiku-class by default).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from tests.eval.place_rubric import apply_place_rubric
from trailstory.config import load_settings
from trailstory.llm.client import AnthropicClient
from trailstory.llm.place import generate_place_context
from trailstory.place import PlaceReference


@dataclass(frozen=True)
class PlaceCase:
    """One place-eval case: the grounded inputs the stitch consumes."""

    name: str
    reference: PlaceReference
    beats: list[str]


CASES: list[PlaceCase] = [
    PlaceCase(
        name="bad-tolz-wax-church",
        reference=PlaceReference(
            town="Bad Tölz",
            region="Bavarian Prealps",
            extract=(
                "Bad Tölz is a town in Bavaria, Germany, and the administrative "
                "center of the Bad Tölz-Wolfratshausen district. It lies on the "
                "river Isar and is known for its old town with painted facades."
            ),
            source_url="https://en.wikipedia.org/wiki/Bad_T%C3%B6lz",
            source_title="Bad Tölz",
        ),
        beats=["river walk", "brunch", "the wax-figure church", "that strange wax-figure church"],
    ),
    PlaceCase(
        name="town-only-fallback",
        reference=PlaceReference(
            town="Wackersberg",
            region="Bad Tölz-Wolfratshausen",
            # No Wikipedia article — exercises the fallback one-liner the
            # prompt is told to produce from town + region alone.
            extract="",
            source_url=None,
            source_title=None,
        ),
        beats=["a quiet lane", "the meadow"],
    ),
]


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    client = AnthropicClient(
        settings.anthropic_api_key,
        model=settings.place_model,
        max_tokens=settings.narrative_max_tokens,
        max_retries=settings.narrative_max_retries,
    )
    console = Console()
    console.print(f"[bold]running place eval[/bold] — stitch={client.model}, {len(CASES)} case(s)")

    any_failed = False
    for case in CASES:
        console.rule(f"[bold cyan]{case.name}[/bold cyan]")
        pc = generate_place_context(case.reference, case.beats, client=client)
        if pc is None:
            console.print("[red]✗ stitch returned None (soft-failed)[/red]")
            any_failed = True
            continue

        console.print(f"[dim]EN[/dim] {pc.summary.en}")
        console.print(f"[dim]RU[/dim] {pc.summary.ru}")
        console.print(f"[dim]DE[/dim] {pc.summary.de}")

        results = apply_place_rubric(pc, reference=case.reference, beats=case.beats)
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
            any_failed = True
        else:
            console.print("[green]all checks passed[/green]")

    return 1 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
