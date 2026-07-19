# Zine style font licenses

The font family bundled in this directory ships under the
**SIL Open Font License 1.1**. Copying the WOFF2 files into the
repo and embedding them as base64 in the rendered HTML is
explicitly permitted by the license.

| File family | Foundry | Source | License |
|---|---|---|---|
| `Oswald-VF.*.woff2` | Vernon Adams / Google Fonts project | https://github.com/googlefonts/OswaldFont / Google Fonts | OFL 1.1 |

The WOFF2 files in this directory are the subset builds served by
Google Fonts (latin + cyrillic), variable weight 200–700. Oswald was
chosen instead of the Big Shoulders Display family named in the
original design brief because Big Shoulders on Google Fonts does not
include a Cyrillic subset — Russian readers (a primary audience for
this project) would have fallen back to a system sans and lost the
condensed-display register entirely. Oswald is the closest
widely-hinted condensed grotesque with full Cyrillic + Latin
coverage.

The zine body face is JetBrains Mono, reused from
`templates/fonts/letter/` (see the LICENSE.md there) as the
Cyrillic-capable stand-in for the brief's Space Mono, which also has
no Cyrillic subset on Google Fonts.

Full OFL text: https://openfontlicense.org/open-font-license-official-text/
