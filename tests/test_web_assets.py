"""Guard: the public glitch-idea web package ships no brand assets, fonts,
prototype runtime or third-party fetches, and declares every file it ships.

Stdlib only, no network. Helpers take a package root so they can be red-tested
against a temporary copy.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "glitch-idea"
PROVENANCE = REPO / "docs" / "design-provenance.md"

FONT_EXT = {".woff", ".woff2", ".ttf", ".otf", ".eot"}
IMAGE_EXT = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp"}
# Prototype runtime and design-system files, including minified or renamed builds.
BANNED_NAME = re.compile(r"(^dc-runtime\b.*\.js$|^support(\.min)?\.js$|^bundle(\.min)?\.css$|^tokens\.json$|\.dc\.html$)", re.I)
TEXT_EXT = {".html", ".js", ".css", ".md"}
BANNED_TERMS = ["blastworks", "bwpm", "@font-face", "@import"]
# Exact URL strings a shipped file legitimately needs. None today.
ALLOWED_URLS: set = set()
# CSS generic families and the platform UI-font aliases only: no named typeface.
GENERIC_FAMILIES = {
    "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded",
    "sans-serif", "serif", "monospace", "cursive", "fantasy", "-apple-system",
    "blinkmacsystemfont", "segoe ui", "inherit", "initial", "unset",
}
# Brand-fixed colour values from the supplied design's token file (the ones
# labelled as fixed brand values), as (red, green, blue). Hard-coded here: the
# export is not in the repo.
BRAND_FIXED_COLOURS = [(0xFF, 0xAB, 0x01), (0x23, 0x23, 0x23)]
# Names that must never ship: generic patterns here, plus an optional local list of private
# terms (one per line) in tests/private-terms.txt, which is git-ignored and never published.
_LOCAL_TERMS_FILE = Path(__file__).resolve().parent / "private-terms.txt"
LOCAL_TERMS = ([line.strip().lower() for line in _LOCAL_TERMS_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")] if _LOCAL_TERMS_FILE.is_file() else [])
# A line "notice-only: term" applies to the notice and provenance files only.
NOTICE_ONLY = [term.split(":", 1)[1].strip() for term in LOCAL_TERMS if term.startswith("notice-only:")]
LOCAL_TERMS = [term for term in LOCAL_TERMS if not term.startswith("notice-only:")]
PRIVATE_TERMS = ["/opt/", "/home/"] + LOCAL_TERMS + NOTICE_ONLY
# Shipped web code carries no author seats or internal build labels (owner ruling, 2 Oct
# 2026: neutral wording; authorship stays in version-control history).
SEAT_NAMES = re.compile(r"\b\w+bwai\b|\bcp\d+\b|\bforge\b|\bskills-\d+\b"
                        + "".join("|\\b" + re.escape(term) + "\\b" for term in LOCAL_TERMS), re.I)


def web_files(pkg):
    web = Path(pkg) / "web"
    return sorted(
        p.relative_to(web).as_posix()
        for p in web.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def notice_files(pkg):
    text = (Path(pkg) / "web" / "assets" / "NOTICE.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|", text, re.M)
    return sorted(rows)


def all_files(pkg):
    return [p for p in Path(pkg).rglob("*") if p.is_file() and "__pycache__" not in p.parts]


def font_image_violations(pkg):
    return [p.name for p in all_files(pkg) if p.suffix.lower() in FONT_EXT | IMAGE_EXT]


def banned_name_violations(pkg):
    return [p.name for p in all_files(pkg) if BANNED_NAME.search(p.name)]


def web_texts(pkg):
    web = Path(pkg) / "web"
    for p in all_files(web):
        if p.suffix.lower() in TEXT_EXT:
            yield p.relative_to(web).as_posix(), p.read_text(encoding="utf-8")


def text_violations(pkg):
    out = []
    web = Path(pkg) / "web"
    for p in all_files(web):
        if p.suffix.lower() not in TEXT_EXT:
            continue
        text = p.read_text(encoding="utf-8")
        low = text.lower()
        rel = p.relative_to(web).as_posix()
        for term in BANNED_TERMS:
            if term in low:
                out.append(f"{rel}: {term}")
        for url in re.findall(r"https?://[^\s\"'<>)]+", text, re.I):
            if url not in ALLOWED_URLS:
                out.append(f"{rel}: url {url}")
        # Protocol-relative addresses: url(//host...), src="//host", and any quoted
        # '//host.tld/...' string (fetch, Worker, import). A JS comment is never quoted.
        # Known limit: a `//` comment with no space after it, right after a quote or `=`
        # ("x" //TODO, x = //note), reads as a fetch. The shipped files put a space after //.
        for url in re.findall(r"""(?:url\(\s*["']?|=\s*["']?|["'`]\s*)(//[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?=[/:?#"'`)\s]|$)[^\s"'`<>)]*)""", text, re.I):
            out.append(f"{rel}: protocol-relative {url}")
    return out


def index_reference_violations(pkg):
    html = (Path(pkg) / "web" / "index.html").read_text(encoding="utf-8")
    out = []
    for tag in re.findall(r"<(?:link|script)\b[^>]*>", html, re.I):
        for attr in re.findall(r"\b(?:href|src)\s*=\s*[\"']([^\"']*)[\"']", tag, re.I):
            if not attr.startswith("./"):
                out.append(attr)
    return out


def font_family_violations(css):
    out = []
    families = re.findall(r"font-family\s*:\s*([^;}]*)", css, re.I)
    # The font shorthand ends with its family list; only a keyword like `font: inherit` has none.
    for decl in re.findall(r"(?<![-\w])font\s*:\s*([^;}]*)", css, re.I):
        decl = decl.strip()
        if decl.lower() in {"inherit", "initial", "unset", "revert"}:
            continue
        match = re.search(r"\d(?:[\d.]*)(?:px|pt|em|rem|%|vw|vh)?(?:\s*/\s*[\d.]+\w*)?\s+(.+)$", decl)
        families.append(match.group(1) if match else decl)
    for decl in families:
        for fam in decl.split(","):
            name = fam.strip().strip("\"'").lower()
            if name and name not in GENERIC_FAMILIES:
                out.append(name)
    return out


def colours_in(text):
    found = set()
    # #rgb, #rgba, #rrggbb and #rrggbbaa: the alpha digits never hide the colour.
    for hexa in re.findall(r"#([0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})\b", text, re.I):
        hexa = hexa[:6] if len(hexa) >= 6 else "".join(c * 2 for c in hexa[:3])
        found.add(tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4)))
    for r, g, b in re.findall(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})", text, re.I):
        found.add((int(r), int(g), int(b)))
    return found


def brand_colour_violations(pkg):
    out = []
    for rel, text in web_texts(pkg):
        out += [f"{rel}: #{r:02x}{g:02x}{b:02x}" for r, g, b in colours_in(text) & set(BRAND_FIXED_COLOURS)]
    return out


def css_blocks(css):
    """(selector, declarations) for every rule at any depth, so rules inside @media count.

    A small structural reader: comments are dropped, braces are matched by depth, and each
    block's own declarations (its nested blocks removed) are split into (property, value),
    whitespace-collapsed and lowercased, `!important` kept. A nested block's selector is its
    full ancestor path ("outer >> inner"), so it is judged by every rule it sits in. A
    comment counts as whitespace, as it does in CSS, so it cannot glue tokens together.
    """
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    blocks, stack, start = [], [], 0
    for index, char in enumerate(css):
        if char == "{":
            stack.append((css[start:index].strip(), index + 1))
            start = index + 1
        elif char == "}" and stack:
            path = " >> ".join(sel for sel, _ in stack)
            selector, body_start = stack.pop()
            body = css[body_start:index]
            while True:  # drop nested blocks, innermost first, keeping this block's own text
                stripped = re.sub(r"[^{};]*\{[^{}]*\}", "", body)
                if stripped == body:
                    break
                body = stripped
            decls = []
            for part in body.split(";"):
                if ":" in part:
                    prop, value = part.split(":", 1)
                    decls.append((prop.strip().lower(), " ".join(value.lower().split())))
            blocks.append((" ".join(path.split()), decls))
            start = index + 1
        elif char == ";" and not stack:
            start = index + 1
    return blocks


# The approved focus ring and saved fill, locked exactly. A unit test cannot decide whether
# CSS paints a visible ring (cascade, initial values, colour spaces); a browser can, and
# the plan's real-browser accessibility check does. This lock makes any change to the
# approved declarations a deliberate edit of these sets, reviewed with that check.
FOCUS_SELECTOR = "button:focus-visible, input:focus-visible, textarea:focus-visible, a:focus-visible"
APPROVED_OUTLINES = {(FOCUS_SELECTOR, "outline", "2px solid #efb85e"),
                     (FOCUS_SELECTOR, "outline-offset", "3px")}
APPROVED_SAVED_FILLS = {('.status-glyph[data-status="saved"]', "background", "#6bce97")}


def escape_violations(css):
    """The sheet uses no CSS escapes; one could spell a locked property the filter misses."""
    return ["backslash escape"] if "\\" in css else []


def focus_violations(css):
    """Every outline declaration anywhere in the sheet, against the approved set."""
    found = {(sel, prop, value) for sel, decls in css_blocks(css)
             for prop, value in decls if prop == "outline" or prop.startswith("outline-")}
    return sorted(f"unapproved {d}" for d in found - APPROVED_OUTLINES) + \
        sorted(f"missing {d}" for d in APPROVED_OUTLINES - found)


def saved_fill_violations(css):
    """Every background declaration on a rule that mentions the saved state, against the approved set."""
    found = {(sel, prop, value) for sel, decls in css_blocks(css) if "saved" in sel.lower()
             for prop, value in decls if prop == "background" or prop.startswith("background-")}
    return sorted(f"unapproved {d}" for d in found - APPROVED_SAVED_FILLS) + \
        sorted(f"missing {d}" for d in APPROVED_SAVED_FILLS - found)


PACKAGE_TEXT_EXT = TEXT_EXT | {".py", ".txt", ".yaml", ".yml", ".json"}


def seat_name_violations(pkg):
    """Every shipped text file in the package, not only web/ (owner ruling extended to
    the Python scripts, 2 Oct 2026: "Extend to Python (Recommended)")."""
    out = []
    for path in all_files(pkg):
        if path.suffix.lower() in PACKAGE_TEXT_EXT:
            rel = path.relative_to(pkg).as_posix()
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                out += [f"{rel}:{n}: {m.group(0)}" for m in SEAT_NAMES.finditer(line)]
    return out


def private_term_violations(path):
    low = Path(path).read_text(encoding="utf-8").lower()
    return [t for t in PRIVATE_TERMS if re.search(r"\b" + re.escape(t) if t[0] != "/" else re.escape(t), low)]


class WebAssetGuard(unittest.TestCase):
    def test_a_notice_lists_exactly_the_web_files(self):
        self.assertTrue((PACKAGE / "web" / "assets" / "NOTICE.md").is_file())
        self.assertEqual(notice_files(PACKAGE), web_files(PACKAGE))

    def test_b_no_fonts_or_images(self):
        self.assertEqual(font_image_violations(PACKAGE), [])

    def test_c_no_prototype_or_design_system_files(self):
        self.assertEqual(banned_name_violations(PACKAGE), [])

    def test_d_text_has_no_brand_fetch_or_urls(self):
        self.assertEqual(text_violations(PACKAGE), [])
        self.assertEqual(index_reference_violations(PACKAGE), [])

    def test_e_css_fonts_focus_and_saved_state(self):
        css = (PACKAGE / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertEqual(font_family_violations(css), [])
        # The approved focus ring and saved fill, exactly; see APPROVED_OUTLINES.
        self.assertEqual(escape_violations(css), [])
        self.assertEqual(focus_violations(css), [])
        self.assertEqual(saved_fill_violations(css), [])

    def test_f_no_brand_fixed_colours(self):
        self.assertEqual(brand_colour_violations(PACKAGE), [])

    def test_h_package_carries_no_seat_names(self):
        self.assertEqual(seat_name_violations(PACKAGE), [])

    def test_g_public_docs_carry_no_private_names(self):
        for path in (PACKAGE / "web" / "assets" / "NOTICE.md", PROVENANCE):
            self.assertEqual(private_term_violations(path), [], str(path))



class GuardDetects(unittest.TestCase):
    """Each guard fires on a realistic violation, so a green suite is not a vacuous pass."""
    SHEET = (PACKAGE / "web" / "styles.css").read_text(encoding="utf-8") + "\n"

    def test_focus_lock(self):
        self.assertEqual(focus_violations(self.SHEET), [])
        for change in ('button:focus-visible { outline: none; }',
                       '@media (max-width: 720px) { button:focus-visible { outline-color: transparent; } }',
                       ':focus-visible { outline-style: initial; }',
                       '* { outline: 0 !important; }',
                       'a:focus-visible { outline: 2px solid #efb85e; @media (x) { outline: none; } }'):
            with self.subTest(change=change):
                self.assertNotEqual(focus_violations(self.SHEET + change), [])
        self.assertNotEqual(focus_violations(self.SHEET.replace("2px solid #efb85e", "1px solid #efb85e")), [])

    def test_saved_lock(self):
        self.assertEqual(saved_fill_violations(self.SHEET), [])
        for change in ('[data-status="saved"] { background: none; }',
                       '@media (max-width: 720px) { .status-glyph[data-status="saved"] { background-color: oklch(0.7 0.1 150 / 0); } }'):
            with self.subTest(change=change):
                self.assertNotEqual(saved_fill_violations(self.SHEET + change), [])
        self.assertNotEqual(saved_fill_violations(self.SHEET.replace("background: #6bce97", "color: #6bce97")), [])
        for change in ('.status-glyph[data-status="saved"] { @media (max-width: 720px) { background: #ffffff; } }',
                       '[data-status="saved"] { &.status-glyph { background: #ffffff; } }'):
            with self.subTest(change=change):
                self.assertNotEqual(saved_fill_violations(self.SHEET + change), [])
        glued = self.SHEET.replace("background: #6bce97", "background: #6bce/**/97")
        self.assertNotEqual(saved_fill_violations(glued), [])

    def test_seat_names_are_found_in_any_web_file(self):
        import tempfile, shutil
        for target_rel, line in (("web/steps/assess.js", "// ExampleBWAI. Original UI."),
                                 ("web/styles.css", "/* layout by SampleBWAI */"),
                                 ("scripts/idea_store.py", "# CP3 upload seam"),
                                 ("scripts/idea_steps/shape.py", '"""Authored for FORGE."""')):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "glitch-idea"
                shutil.copytree(PACKAGE, copy)
                target = copy / target_rel
                target.write_text(line + "\n" + target.read_text())
                self.assertNotEqual(seat_name_violations(copy), [])

    def test_comment_and_escape_cannot_disguise_the_lock(self):
        self.assertNotEqual(focus_violations(self.SHEET.replace("outline: 2px solid", "outline: 2/**/px solid")), [])
        self.assertNotEqual(escape_violations(self.SHEET + "* { \\6futline: none; }"), [])
        self.assertEqual(escape_violations(self.SHEET), [])

    def test_brand_colour_forms(self):
        for text in ("#ffab01", "#FFAB01ff", "#23232380", "rgb(255, 171, 1)", "rgba(35 35 35 / 50%)"):
            with self.subTest(text=text):
                self.assertTrue(colours_in(text) & set(BRAND_FIXED_COLOURS))
        self.assertFalse(colours_in("#efb85e #6bce97") & set(BRAND_FIXED_COLOURS))

    def test_protocol_relative_forms(self):
        import tempfile, shutil
        for line in ('fetch("//cdn.example/a.js");', "new Worker('//cdn.example/w.js');",
                     "import x from '//cdn.example/m.js';", ".x{background:url(//fonts.example/a.woff2)}",
                     '<img src="//img.example/a.png">', 'fetch("//localhost/a.js");', 'fetch("//cdn/a.js");',
                     ".x{background:URL(//cdn.example/a.css)}", "<img src=//cdn.example/a.png>",
                     'fetch( "//cdn.example/a.js");'):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "glitch-idea"
                shutil.copytree(PACKAGE, copy)
                (copy / "web" / "app.js").write_text((copy / "web" / "app.js").read_text() + "\n" + line + "\n")
                self.assertTrue(any("protocol-relative" in v for v in text_violations(copy)))
        self.assertEqual([v for v in text_violations(PACKAGE) if "protocol-relative" in v], [])


if __name__ == "__main__":
    unittest.main()
