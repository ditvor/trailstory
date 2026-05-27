# Font licenses

All font families bundled in this directory ship under the
**SIL Open Font License 1.1**. Copying the WOFF2 files into the
repo and embedding them as base64 in the rendered HTML is
explicitly permitted by the license.

| File family | Used by | Foundry | Source | License |
|---|---|---|---|---|
| `SourceSerif4-Italic-VF.*.woff2`, `SourceSerif4-Roman-VF.*.woff2` | Letter (output) | Frank Grießhammer / Adobe | https://github.com/adobe-fonts/source-serif / Google Fonts | OFL 1.1 |
| `JetBrainsMono-VF.*.woff2` | Letter (output), Notebook (builder) | JetBrains | https://github.com/JetBrains/JetBrainsMono | OFL 1.1 |
| `Onest-VF.*.woff2` | Notebook (builder) | Rosetta Type / Martin Vácha | https://github.com/martinvacha/onest / Google Fonts | OFL 1.1 |
| `Caveat-VF.*.woff2` | Notebook (builder) | Pablo Impallari | https://github.com/impallari/Caveat / Google Fonts | OFL 1.1 |

The WOFF2 files in this directory are the subset builds served by
Google Fonts (latin + cyrillic + latin-ext). Subsets are split so the
browser activates only what it needs.

## Substitutions from the original design brief

**Letter (output renderer):** the brief specified Newsreader. Newsreader
on Google Fonts does not include a Cyrillic subset — Russian readers
(a primary audience for this project) would have fallen back to system
serif. Source Serif 4 shares the same optical-size variable axis and
italic + roman pair as Newsreader, and ships full Cyrillic + Latin
coverage.

**Notebook (builder UI):** the brief specified Bricolage Grotesque
(variable display) and Hanken Grotesk (body). Neither has a basic
Cyrillic subset on Google Fonts — same problem as Newsreader. Onest
(by Rosetta Type) covers both roles: it is a wide-range variable font
(wght 300..900), ships full Cyrillic + Latin coverage, and has the
organic, slightly-warm letterforms that match the workshop register
the Notebook is going for. One file plays both the display and body
roles via weight.

Caveat is used identically to the brief: handwritten Cyrillic-capable
script, two appearances only — the "your day" doodle annotation in the
hero and the "— composed by you" signature in the footer.

Full OFL text: https://openfontlicense.org/open-font-license-official-text/
