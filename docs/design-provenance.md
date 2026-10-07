# Design provenance: the glitch-idea web UI

This note records, for maintainers, where the browser flow's look and layout came from and which brand files the public package ships.

## Source

- Canvas: "Glitch idea UI rework", https://claude.ai/artifact/Qem5EnTV1wpKnuRFroxYro, version 1791274920-f071.
- Design system: "Blastworks Applications" (namespace `bwpm`), https://claude.ai/artifact/4YHquCiskbKfbKHPXH2UbY, version 1790704902-35b1.

The layout now follows the canvas: an app bar, a step rail, and one current-step panel.
The behaviour of the approved flow is kept: seven steps, a saved check on a saved step, a keyboard focus ring, and the narrow-panel alternative approved at 440px wide.

## Ruling

The owner ruled on 6 October 2026, verbatim: "BW branding for now, if it ever goes glitch native, theyll style it".
That ruling overrules the owner's 1 October 2026 ruling "Keep them out of the public package".
The public package (everything under `glitch-idea/`) therefore ships the Blastworks brand files below, and the token values live in `glitch-idea/web/styles.css`.

## Brand files shipped

Paths are under `glitch-idea/web/`. Every file comes from the Blastworks Applications design system, byte for byte.

| File | Licence | sha256 |
| --- | --- | --- |
| `assets/fonts/BlastworksSans-Regular.woff2` | Unlicense (public domain) | `72e9a6fddd7ac56f272db959afbb2c103bffc5918c80804c6d5c0faea61ce951` |
| `assets/fonts/BlastworksSans-SemiBold.woff2` | Unlicense (public domain) | `1cc71aad35242164f93f218298abbb3fa1e4f1f9bf59500a8fb75449be003346` |
| `assets/fonts/BlastworksSans-ExtraBold.woff2` | Unlicense (public domain) | `88ff99cca1216fa879ce553f23d6d9457e40905da21f45de8976ae487299017e` |
| `assets/fonts/BlastworksSans-UNLICENSE.txt` | the licence text itself | `6b0382b16279f26ff69014300541967a356a666eb0b91b422f6862f6b7dad17e` |
| `assets/logo.svg` | Blastworks logo, shipped under the owner's ruling above | `7196a059444d46f6203f6a50278f6668f1fd35b67dda556d9ba4fd45ca68e4e8` |
| `assets/bwpm/bundle.css` | design-system component stylesheet, shipped under the owner's ruling above | `0ae74948984054948e796ed03e25675ea9d03ae95ff349a27a40001df680ad61` |

Still not shipped: the prototype runtime (`dc-runtime.js`, `support.js`), the prototype HTML pages (`*.dc.html`) and `tokens.json`.
The UI makes no third-party fetch: no remote font, script, stylesheet or image.

## How it is enforced

`tests/test_web_assets.py` is a stdlib-only guard test.
It fails when:

- a file under `glitch-idea/web/` is not listed in `glitch-idea/web/assets/NOTICE.md`, or the notice lists a file that does not exist;
- a font or image file ships anywhere under `glitch-idea/` that is not declared in the test's brand-file map, or a declared file is missing or its bytes differ from the pinned sha256;
- a prototype or design-system file (`dc-runtime*.js`, `support.js`, `*.dc.html`, `tokens.json`) ships, or `bundle.css` ships anywhere except its declared path;
- shipped web text contains `@import`, an absolute URL (apart from one declared install pointer) or a protocol-relative (`//host`) fetch, or `index.html` references a non-relative script or stylesheet;
- `@font-face` appears in any web file other than `styles.css`, or any `url(...)` in web text, including every `@font-face` source, points anywhere but a declared brand file (font sources must be under `assets/fonts/`);
- the stylesheet names a typeface other than "Blastworks Sans" (generic families and platform UI-font aliases remain allowed), in `font-family`, the `font` shorthand or a font custom property;
- the brand name appears in a `.js` file, or in web text outside `styles.css`, `index.html`, `assets/NOTICE.md` and `assets/bwpm/bundle.css`;
- one of the brand's fixed colour values appears outside `styles.css` and `assets/bwpm/bundle.css`;
- any outline declaration anywhere in `styles.css`, or any background declaration on a saved-state rule, differs from the approved focus ring and saved fill locked in the test;
- this note or the notice contains an internal path prefix or a private name from a fixed list.

Each rule has a test that feeds it a violation and expects a failure, so a green run is not a vacuous pass.
Add a new brand file by declaring it (path and sha256) in the test and listing it in the notice table with its origin and licence.
