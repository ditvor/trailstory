# Editorial style font licenses

Both font families bundled in this directory ship under the
**SIL Open Font License 1.1**. Copying the WOFF2 files into the
repo and embedding them as base64 in the rendered HTML is
explicitly permitted by the license.

| File family | Foundry | Source | License |
|---|---|---|---|
| `SourceSerif4-Italic-VF.*.woff2`, `SourceSerif4-Roman-VF.*.woff2` | Frank Grießhammer / Adobe | https://github.com/adobe-fonts/source-serif / Google Fonts | OFL 1.1 |
| `JetBrainsMono-VF.*.woff2` | JetBrains | https://github.com/JetBrains/JetBrainsMono | OFL 1.1 |

The WOFF2 files in this directory are the subset builds served by
Google Fonts (latin + cyrillic). They were chosen, instead of the
Newsreader family named in the original design brief, because
Newsreader on Google Fonts does not include a Cyrillic subset —
Russian readers (a primary audience for this project) would have
fallen back to system serif. Source Serif 4 shares the same
optical-size variable axis and italic + roman pair as Newsreader,
and ships full Cyrillic + Latin coverage.

Full OFL text: https://openfontlicense.org/open-font-license-official-text/
