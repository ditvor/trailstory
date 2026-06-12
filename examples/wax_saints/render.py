"""One-off demo: render the editorial template with the Bad Tölz example.

Pulls the 8 base64 photos out of the source HTML provided by the user,
saves them as JPEGs under ``examples/wax_saints/photos/``, builds a
``Memory`` from the example's tri-lingual text + a synthesized GPX, and
runs the existing pipeline to produce
``output/dev/wax-saints/wax-saints-bad-toelz-april-18.html``.

Throwaway. Not wired into tests or the web app. Run with::

    python examples/wax_saints/render.py
"""

from __future__ import annotations

import base64
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from PIL import Image

# Make `trailstory` importable when running this script from the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from trailstory.models import (  # noqa: E402
    GpxStats,
    HikeInput,
    LocalizedString,
    Memory,
    NarrativeOutput,
    Paragraph,
    PhotoMeta,
    Provenance,
    ProvenanceSource,
    Sentence,
    Style,
    Waypoint,
)
from trailstory.renderers.html import render_html  # noqa: E402

SOURCE_HTML = Path(
    "/Users/igorkochman/Downloads/journal-nice-ausflug-for-a-warm-saturday-2026-04-18.html"
)
PHOTOS_DIR = Path(__file__).parent / "photos"
OUTPUT_DIR = REPO_ROOT / "output" / "dev" / "wax-saints"
SLUG = "wax-saints-bad-toelz-april-18"


def extract_photos(source: Path, photos_dir: Path) -> list[Path]:
    """Walk the source HTML, decode every base64-embedded image, save as JPEG.

    The example uses CSS ``background-image: url('data:image/...;base64,...')``
    for every photo (no ``<img src=…>``), so we extract by matching the
    data URI in CSS attribute values. The source HTML reuses the same image
    in multiple places (hero + first photo card both point at the same
    bytes), so dedupe by SHA-256 of the decoded payload before writing.
    """
    import hashlib
    from io import BytesIO

    html = source.read_text(encoding="utf-8")
    data_uris = re.findall(
        r"data:image/([a-z]+);base64,([A-Za-z0-9+/=]+)",
        html,
    )
    photos_dir.mkdir(parents=True, exist_ok=True)
    # Wipe any prior extracts so old duplicates don't linger.
    for old in photos_dir.glob("*.jpg"):
        old.unlink()

    seen: set[str] = set()
    saved: list[Path] = []
    counter = 0
    for fmt, b64 in data_uris:
        raw = base64.b64decode(b64)
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            print(f"  skipped duplicate of {digest[:8]}")
            continue
        seen.add(digest)
        counter += 1
        try:
            img = Image.open(BytesIO(raw)).convert("RGB")
            out = photos_dir / f"{counter:02d}.jpg"
            img.save(out, "JPEG", quality=88)
            saved.append(out)
            print(f"  saved {out.name}  {out.stat().st_size // 1024} KB")
        except Exception as exc:
            print(f"  failed image #{counter} ({fmt}): {exc}")
    return saved


def synthesize_gpx_stats() -> GpxStats:
    """The example is map-based, not GPX-recorded. Fabricate enough waypoints
    along the Isar river south of Bad Tölz to draw a credible route SVG +
    elevation sparkline. Numbers are taken from the example's stat strip
    (8.3 km, 4h 16m)."""
    base_lat, base_lon = 47.7613, 11.5594  # Bad Tölz market square
    base_time = datetime(2026, 4, 18, 12, 5, tzinfo=UTC)
    # Roughly south-westward along the river, then loop back.
    track = [
        (0.0000, 0.0000, 660),
        (-0.0030, 0.0010, 657),
        (-0.0065, 0.0028, 654),
        (-0.0110, 0.0055, 652),
        (-0.0160, 0.0085, 658),
        (-0.0205, 0.0110, 665),
        (-0.0240, 0.0140, 672),
        (-0.0265, 0.0175, 685),
        (-0.0280, 0.0210, 698),
        (-0.0265, 0.0240, 706),
        (-0.0220, 0.0235, 695),
        (-0.0170, 0.0225, 682),
        (-0.0120, 0.0210, 672),
        (-0.0070, 0.0190, 664),
        (-0.0030, 0.0150, 660),
        (-0.0010, 0.0095, 658),
        (0.0000, 0.0040, 660),
    ]
    waypoints = [
        Waypoint(
            lat=base_lat + dlat,
            lon=base_lon + dlon,
            ele_m=float(ele),
            time=base_time + timedelta(minutes=i * 15),
        )
        for i, (dlat, dlon, ele) in enumerate(track)
    ]
    return GpxStats(
        distance_km=8.3,
        elevation_gain_m=68,  # mild — riverside, not alpine
        duration_min=256,  # 4h 16m
        start_elev_m=660,
        summit_elev_m=706,
        waypoints=waypoints,
    )


def _sent(en: str, ru: str, de: str, source: ProvenanceSource, reference: str) -> Sentence:
    return Sentence(
        text=LocalizedString(en=en, ru=ru, de=de),
        provenance=Provenance(source=source, reference=reference),
    )


def build_paragraphs() -> list[Paragraph]:
    """ADR-014 sentence-level paragraphs with per-sentence provenance.

    The provenance tags are hand-assigned to mirror what the writer would
    self-report, so the rendered page demonstrates the editorial template's
    hover tooltip and INFERRED tint.
    """
    return [
        [
            _sent(
                "We pulled into the parking spot just around noon, the kind of warm April Saturday that makes you glad you didn't sleep in.",
                "Мы припарковались около полудня — в один из тех тёплых апрельских субботних дней, когда радуешься, что не залежался в постели.",
                "Wir kamen gegen Mittag am Parkplatz an — an einem jener warmen Aprilsamstage, an denen man froh ist, nicht länger im Bett geblieben zu sein.",
                ProvenanceSource.GPX,
                "track start 12:05, 18 April",
            ),
            _sent(
                "Bad Tölz greeted us with blue skies and a city centre that felt unhurried and inviting.",
                "Бад-Тёльц встретил нас голубым небом и неспешным, располагающим к прогулке центром города.",
                "Bad Tölz empfing uns mit blauem Himmel und einem Stadtzentrum, das einladend und entspannt wirkte.",
                ProvenanceSource.PHOTO,
                "photo 1 — town centre under blue sky",
            ),
            _sent(
                "We found a spot for brunch, and little Danny promptly became the star of the room — strangers couldn't help but stop and say hello.",
                "Нашли место для бранча, и маленький Дэнни моментально стал звездой заведения — прохожие то и дело останавливались, чтобы с ним поздороваться.",
                "Wir fanden einen Platz für einen ausgedehnten Brunch, und der kleine Danny wurde prompt zum Mittelpunkt des Raumes — Fremde konnten einfach nicht widerstehen, ihn anzulächeln und Hallo zu sagen.",
                ProvenanceSource.INFERRED,
                "brunch is in the seed; the Danny detail is not",
            ),
        ],
        [
            _sent(
                "Fed and happy, we followed the Isar out of town.",
                "Сытые и довольные, мы двинулись вдоль Изара за город.",
                "Gestärkt und gut gelaunt folgten wir der Isar aus der Stadt hinaus.",
                ProvenanceSource.SEED,
                "river walk",
            ),
            _sent(
                "The river has a way of pulling you along, and for the next few hours the four of us — Danny in the carrier — let it do exactly that.",
                "Река умеет увлекать за собой, и несколько ближайших часов мы просто шли туда, куда она вела, — все четверо, и Дэнни в слинг-рюкзаке.",
                "Der Fluss hat eine Art, einen mitzuziehen, und genau das ließen wir die nächsten Stunden zu — alle vier, Danny im Tragerucksack.",
                ProvenanceSource.INFERRED,
                "group detail not in the seed",
            ),
        ],
        [
            _sent(
                "8.3 kilometres and just over four hours later, we were tired in the best possible way.",
                "8,3 километра и чуть больше четырёх часов спустя усталость была приятной.",
                "8,3 Kilometer und gut vier Stunden später waren wir auf die schönste Art erschöpft.",
                ProvenanceSource.GPX,
                "distance 8.3 km, duration 4h 16m",
            ),
            _sent(
                "The highlight nobody had planned for was a small old church tucked along the route, its interior populated with wax figures of Jesus and his disciples lurking in unexpected corners.",
                "Незапланированным открытием стала маленькая старая церковь на маршруте: внутри нас поджидали восковые фигуры Иисуса и его учеников, расставленные в самых неожиданных местах.",
                "Das ungeplante Highlight war eine kleine alte Kirche am Wegesrand, in der Wachsfiguren von Jesus und seinen Jüngern an unverhofften Stellen auf uns warteten.",
                ProvenanceSource.SEED,
                "that strange wax-figure church",
            ),
            _sent(
                "Genuinely eerie, genuinely unforgettable.",
                "По-настоящему жутко — и по-настоящему незабываемо.",
                "Wirklich gruselig — und wirklich unvergesslich.",
                ProvenanceSource.SEED,
                "that strange wax-figure church",
            ),
        ],
        [
            _sent(
                "We capped the evening with Asian food to go, eaten on the riverbank as the light faded.",
                "Вечер завершили едой навынос из азиатского кафе, которую съели прямо на берегу реки, пока гасло небо.",
                "Den Abend ließen wir mit asiatischem Essen zum Mitnehmen ausklingen, gegessen am Flussufer, während das Licht langsam verblasste.",
                ProvenanceSource.INFERRED,
                "evening meal — not in the seed",
            ),
        ],
    ]


def build_narrative() -> NarrativeOutput:
    return NarrativeOutput(
        title=LocalizedString(
            en="Wax Saints & River Air",
            ru="Восковые святые и речной воздух",
            de="Wachsheilige und Flussluft",
        ),
        subtitle=LocalizedString(
            en="A warm April Saturday along the Isar — brunch, a creepy church, and dinner by the water.",
            ru="Тёплая апрельская суббота вдоль Изара — бранч, жуткая церковь и ужин у воды.",
            de="Ein warmer Aprilsamstag an der Isar — Brunch, eine schaurige Kirche und Abendessen am Wasser.",
        ),
        paragraphs=build_paragraphs(),
        pull_quote=LocalizedString(
            en="Genuinely eerie, genuinely unforgettable.",
            ru="По-настоящему жутко — и по-настоящему незабываемо.",
            de="Wirklich gruselig — und wirklich unvergesslich.",
        ),
        milestone=LocalizedString(
            en="First Bad Tölz Saturday",
            ru="Первая суббота в Бад-Тёльце",
            de="Erster Samstag in Bad Tölz",
        ),
        # Filled in by main() once we know how many uniques we extracted.
        selected_photo_indices=[],
    )


def main() -> None:
    if not SOURCE_HTML.is_file():
        sys.exit(f"source HTML not found: {SOURCE_HTML}")

    print(f"→ extracting photos from {SOURCE_HTML.name}")
    photo_paths = extract_photos(SOURCE_HTML, PHOTOS_DIR)
    if not photo_paths:
        sys.exit("no photos extracted — source format may have changed")
    print(f"  {len(photo_paths)} photos saved to {PHOTOS_DIR}\n")

    base_time = datetime(2026, 4, 18, 12, 5, tzinfo=UTC)
    photo_metas = [
        PhotoMeta(path=p, timestamp=base_time + timedelta(minutes=i * 25), index=i)
        for i, p in enumerate(photo_paths)
    ]

    narrative = build_narrative()
    narrative.selected_photo_indices = list(range(len(photo_paths)))
    stats = synthesize_gpx_stats()
    memory = Memory(
        hike_input=HikeInput(
            gpx_path=PHOTOS_DIR / "fake.gpx",
            photos_dir=PHOTOS_DIR,
            seed_text="Bad Tölz on a warm April Saturday: river walk + brunch + that strange wax-figure church.",
            location_name="Bad Tölz, Bavaria",
        ),
        gpx_stats=stats,
        narrative=narrative,
        selected_photos=photo_metas,
        style=Style.editorial,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out = render_html(
        memory=memory,
        output_dir=OUTPUT_DIR,
        slug=SLUG,
        hike_date=date(2026, 4, 18),
        location="Bad Tölz, Bavaria",
    )
    print(f"→ rendered {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
