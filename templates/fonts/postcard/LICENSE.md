# Postcard Set style font licenses

The font families bundled in this directory ship under the
**SIL Open Font License 1.1**. Copying the WOFF2 files into the
repo and embedding them as base64 in the rendered HTML is
explicitly permitted by the license.

| File family | Foundry | Source | License |
|---|---|---|---|
| `YesevaOne-Regular.*.woff2` | Jovanny Lemonad / Google Fonts project | https://github.com/cyrealtype/Yeseva-One / Google Fonts | OFL 1.1 |
| `Caveat-VF.*.woff2` | Impallari Type / Google Fonts project | https://github.com/googlefonts/caveat / Google Fonts | OFL 1.1 |

The WOFF2 files in this directory are the subset builds served by
Google Fonts (latin + cyrillic). Yeseva One is a single-weight (400)
display serif with the high-contrast, slightly ornamental drawing of
mid-century travel-card lettering — chosen because it carries a full
Cyrillic set, which the more obvious Latin-only "Greetings from"
script faces on Google Fonts do not. Caveat (variable, wght 400–700)
is the handwritten face for the message side of each card; it also
covers Cyrillic, so a Russian reader gets the same handwriting, not a
system-font fallback.

The small-print face (postal labels, address lines, stats) is
JetBrains Mono, reused from `templates/fonts/letter/` (see the
LICENSE.md there) rather than committed twice.

Full OFL text: https://openfontlicense.org/open-font-license-official-text/
