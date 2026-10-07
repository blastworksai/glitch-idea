# Notice: web UI assets

The UI code in this folder (pages, scripts and the page stylesheet) is original work, released under the repository's MIT License (see the LICENSE file at the repository root).

The UI also ships the Blastworks brand files: the logo, the Blastworks Sans font and the design-system component stylesheet.
The owner ruled on 6 October 2026 that the public package carries the Blastworks branding for now.
Those files come from the Blastworks Applications design system; the font is released under the Unlicense (public domain), and its licence text ships beside it as `assets/fonts/BlastworksSans-UNLICENSE.txt`.

- The pages load nothing from third parties: every script and stylesheet is a relative file inside this folder, and no remote address is requested.
- Fonts are served from the font files listed below, never fetched from a remote host.
- Each brand file is pinned by its sha256 in `tests/test_web_assets.py`; a changed or additional font, image or design-system file fails that test.

## Files shipped under `web/`

| File | Origin |
| --- | --- |
| `index.html` | original |
| `app.js` | original |
| `api.js` | original |
| `folds.js` | original |
| `ideas.js` | original |
| `setup.js` | original |
| `styles.css` | original |
| `steps/assess.js` | original |
| `steps/discovery.js` | original |
| `steps/exploration.js` | original |
| `steps/method.js` | original |
| `steps/review.js` | original |
| `steps/visualize.js` | original |
| `assets/NOTICE.md` | original |
| `assets/logo.svg` | Blastworks Applications design system (logo) |
| `assets/fonts/BlastworksSans-Regular.woff2` | Blastworks Applications design system (Blastworks Sans font, Unlicense) |
| `assets/fonts/BlastworksSans-SemiBold.woff2` | Blastworks Applications design system (Blastworks Sans font, Unlicense) |
| `assets/fonts/BlastworksSans-ExtraBold.woff2` | Blastworks Applications design system (Blastworks Sans font, Unlicense) |
| `assets/fonts/BlastworksSans-UNLICENSE.txt` | Blastworks Applications design system (Blastworks Sans font licence, Unlicense) |
| `assets/bwpm/bundle.css` | Blastworks Applications design system (component stylesheet) |

A test (`tests/test_web_assets.py`) fails if a file is added here without being listed in this table.
