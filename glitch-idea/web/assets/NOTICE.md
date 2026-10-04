# Notice: web UI assets

The web UI in this folder is original work, released under the repository's MIT License (see the LICENSE file at the repository root).

- Text is set in the fonts your platform already provides (`system-ui` and generic families). No font files ship here.
- The pages load nothing from third parties: every script and stylesheet is a relative file inside this folder, and no remote address is requested.
- No third-party brand assets, logos, fonts, icon sets or design-system code are included or redistributed.

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
| `steps/method.js` | original |
| `steps/review.js` | original |
| `steps/shape.js` | original |
| `steps/visualize.js` | original |
| `assets/NOTICE.md` | original |

A test (`tests/test_web_assets.py`) fails if a file is added here without being listed in this table.
