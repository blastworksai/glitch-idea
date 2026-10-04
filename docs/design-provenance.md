# Design provenance: the glitch-idea web UI

This note records, for maintainers, where the browser flow's look and layout came from and what was deliberately left out of the public package.

## What was used

A supplied design export was used as a behavioural and layout reference only.
From it we kept the behaviour of the approved flow:

- seven steps, shown side by side as folding sections;
- the current step expanded, the others folded;
- a green saved check at the top of a step once it is saved;
- an amber keyboard focus ring;
- a narrow-panel alternative (compact step navigation and one current panel), approved at 440px wide; the stylesheet applies it at every width up to 720px, which includes 440px.

No code or asset from the export was copied or redistributed.
The shipped CSS and scripts were written independently, and the colours in `glitch-idea/web/styles.css` are independently chosen, not taken from the export's token file.

## What was excluded, and why

The owner ruled on 1 October 2026, verbatim: "Keep them out of the public package".
That ruling covers the supplied brand logo and the brand's own styling.
The public package (everything under `glitch-idea/`) therefore ships none of the following:

- the logo SVG or any other brand image;
- the brand font family files and their licence file (the UI uses system fonts);
- the design-system `bundle.css`;
- the values in `tokens.json`;
- the prototype runtime (`dc-runtime.js`, `support.js`);
- the prototype HTML pages (`*.dc.html`).

The UI also makes no third-party fetch: no remote font, script, stylesheet or image.

## How it is enforced

`tests/test_web_assets.py` is a stdlib-only guard test.
It fails when:

- a file under `glitch-idea/web/` is not listed in `glitch-idea/web/assets/NOTICE.md`, or the notice lists a file that does not exist;
- any font or image file ships anywhere under `glitch-idea/`;
- a prototype or design-system file (`dc-runtime*.js`, `support.js`, `bundle.css` and their `.min` builds, `tokens.json`, `*.dc.html`) ships under `glitch-idea/`;
- shipped web text contains a brand name, `@font-face`, `@import`, an absolute URL or a protocol-relative (`//host`) fetch, or `index.html` references a non-relative script or stylesheet;
- `styles.css` names a typeface in `font-family` or the `font` shorthand (only generic families and platform UI-font aliases are allowed);
- any outline declaration anywhere in `styles.css`, or any background declaration on a saved-state rule, differs from the approved amber focus ring and green saved fill locked in the test.
  The lock does not judge whether CSS paints a visible ring; the real-browser accessibility check does that, and a deliberate style change updates the lock alongside it;
- any shipped web file contains one of the brand's fixed colour values, as hex or `rgb()`;
- this note or the notice contains one of a fixed list of internal path prefixes or seat and personal names. That scan covers these two notes only; source-code comments are not scanned.

Add a new web file by listing it in the notice table with its origin.
If it is not original work, record its source and licence there and check that the licence is permissive.
