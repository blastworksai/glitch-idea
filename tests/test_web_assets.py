"""Guard: the public glitch-idea web package ships exactly the declared brand
files (pinned by hash), no other font or image, no prototype runtime and no
third-party fetch, and declares every file it ships.

Stdlib only, no network. Helpers take a package root so they can be red-tested
against a temporary copy.
"""
import hashlib
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
# Owner ruling, 6 Oct 2026: the public package ships the Blastworks brand. Each brand file is
# declared here by its path under web/ and its pinned sha256; a font, image or design-system
# file ships only if it is in this map AND its bytes match.
BRAND_FILES = {
    "assets/fonts/BlastworksSans-Regular.woff2": "72e9a6fddd7ac56f272db959afbb2c103bffc5918c80804c6d5c0faea61ce951",
    "assets/fonts/BlastworksSans-SemiBold.woff2": "1cc71aad35242164f93f218298abbb3fa1e4f1f9bf59500a8fb75449be003346",
    "assets/fonts/BlastworksSans-ExtraBold.woff2": "88ff99cca1216fa879ce553f23d6d9457e40905da21f45de8976ae487299017e",
    "assets/fonts/BlastworksSans-UNLICENSE.txt": "6b0382b16279f26ff69014300541967a356a666eb0b91b422f6862f6b7dad17e",
    "assets/logo.svg": "7196a059444d46f6203f6a50278f6668f1fd35b67dda556d9ba4fd45ca68e4e8",
    "assets/bwpm/bundle.css": "0ae74948984054948e796ed03e25675ea9d03ae95ff349a27a40001df680ad61",
}
BRAND_FILES_LOWER = {k.lower() for k in BRAND_FILES}
BUNDLE_PATH = "assets/bwpm/bundle.css"
STYLES_PATH = "styles.css"
BANNED_TERMS = ["@import"]  # banned in every shipped text file
BRAND_WORDS = ["blastworks", "bwpm"]
# Where the brand name may appear in web text: the stylesheet, the page, the notice, and the
# pinned design-system sheet. Every .js file stays free of it. (The font licence and the
# provenance note are not scanned as web text.)
BRAND_WORD_OK = {STYLES_PATH, "index.html", "assets/NOTICE.md", BUNDLE_PATH}
BRAND_COLOUR_OK = {STYLES_PATH, BUNDLE_PATH}
# Exact URL strings a shipped file legitimately needs. One today.
# Owner-mandated install pointer for an optional external skill (Prototype Here); never fetched.
# 'http://www.w3.org/2000/svg' is the XML namespace identifier for createElementNS, never fetched.
ALLOWED_URLS: set = {'https://github.com/mattpocock/skills', 'http://www.w3.org/2000/svg'}
# CSS generic families, the platform UI-font aliases and the one declared brand typeface.
GENERIC_FAMILIES = {
    "blastworks sans",
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


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def font_image_violations(pkg):
    """A font or image ships only as a declared brand file with its pinned hash; the declared
    files must also all be present with that hash."""
    out = []
    web = Path(pkg) / "web"
    for p in all_files(pkg):
        if p.suffix.lower() in FONT_EXT | IMAGE_EXT:
            rel = p.relative_to(web).as_posix() if web in p.parents else None
            if rel not in BRAND_FILES:
                out.append(f"undeclared {p.name}")
            elif _sha(p) != BRAND_FILES[rel]:
                out.append(f"hash {p.name}")
    for rel, digest in BRAND_FILES.items():
        path = web / rel
        if not path.is_file():
            out.append(f"missing {rel}")
        elif _sha(path) != digest:
            out.append(f"hash {rel}")
    return out


def banned_name_violations(pkg):
    """bundle.css is allowed only at its declared path (its bytes are pinned above)."""
    out = []
    web = Path(pkg) / "web"
    for p in all_files(pkg):
        if BANNED_NAME.search(p.name):
            rel = p.relative_to(web).as_posix() if web in p.parents else None
            if rel != BUNDLE_PATH:
                out.append(p.name)
    return out


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
        if "@font-face" in low and rel != STYLES_PATH:
            out.append(f"{rel}: @font-face")
        if rel not in BRAND_WORD_OK:
            for term in BRAND_WORDS:
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


def _declared_ref(ref):
    ref = ref.strip()
    return ref[2:] if ref.startswith("./") else ref


def url_violations(pkg):
    """Every url(...) in shipped web text points at a declared brand file; every @font-face
    src is a relative assets/fonts/<declared file>. No data:, absolute or undeclared target."""
    out = []
    for rel, text in web_texts(pkg):
        flags = 0 if rel.endswith(".js") else re.I  # in .js, `new URL(` is not a CSS url()
        for _quote, ref in re.findall(r"""url\(\s*(["']?)(.*?)\1\s*\)""", text, flags):
            if _declared_ref(ref).lower() not in BRAND_FILES_LOWER:
                out.append(f"{rel}: url {ref}")
        if rel.endswith(".css"):
            for sel, decls in css_blocks(text):
                if sel.split(">>")[-1].strip().startswith("@font-face"):
                    for prop, value in decls:
                        if prop == "src":
                            refs = re.findall(r"""url\(\s*["']?(.*?)["']?\s*\)""", value)
                            if not refs or any(not _declared_ref(r).lower().startswith("assets/fonts/")
                                               or _declared_ref(r).lower() not in BRAND_FILES_LOWER for r in refs):
                                out.append(f"{rel}: @font-face src {value}")
    return out


def index_reference_violations(pkg):
    html = (Path(pkg) / "web" / "index.html").read_text(encoding="utf-8")
    out = []
    for tag in re.findall(r"<(?:link|script)\b[^>]*>", html, re.I):
        for attr in re.findall(r"\b(?:href|src)\s*=\s*[\"']([^\"']*)[\"']", tag, re.I):
            if not attr.startswith("./"):
                out.append(attr)
    return out


FONT_SHORTHAND_KEYWORDS = {"inherit", "initial", "unset", "revert"}


def _is_font_token(name):
    return bool(re.search(r"font|family", name, re.I)) and not re.search(
        r"size|weight|height|style|stretch|variant|feature|spacing", name, re.I)


def font_family_violations(css):
    """Every family a sheet names must be generic or the one packaged face.

    Pass the sheets together (one string): a var(--name) is accepted only when --name is a
    declared font token whose own value passes, and a fallback inside var() is checked as a family list.
    """
    out = []
    # Every declaration of a token counts (a later media-query or theme override too), never just the last.
    tokens = {}
    for n, v in re.findall(r"(--[\w-]+)\s*:\s*([^;}]*)", css):
        tokens.setdefault(n, []).append(v.strip())
    lists = []
    lists += re.findall(r"font-family\s*:\s*([^;}]*)", css, re.I)
    # The font shorthand ends with its family list; only a keyword like `font: inherit` has none.
    for decl in re.findall(r"(?<![-\w])font\s*:\s*([^;}]*)", css, re.I):
        decl = decl.strip()
        if decl.lower() in FONT_SHORTHAND_KEYWORDS:
            continue
        match = re.search(r"(?:^|\s)[\d.]+(?:px|pt|em|rem|%|vw|vh)(?:\s*/\s*[\d.]+\w*)?\s+(.+)$", decl)
        lists.append(match.group(1) if match else decl)
    # A font token (`--font-sans: ...`) is a family list too; size/weight/style tokens are not.
    lists += [v for n, vs in tokens.items() if _is_font_token(n) for v in vs]

    def check(decl, seen=()):
        # Split on top-level commas only: a var() fallback keeps its own commas.
        parts, depth, cur = [], 0, ""
        for ch in decl:
            depth += (ch == "(") - (ch == ")")
            if ch == "," and depth == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        parts.append(cur)
        for part in parts:
            part = part.strip()
            ref = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,(.*))?\)", part, re.S | re.I)
            if ref:
                name, fallback = ref.group(1), ref.group(2)
                if not _is_font_token(name) or name not in tokens:
                    out.append("var(" + name + ") is not a declared font token")
                elif name not in seen:
                    for value in tokens[name]:
                        check(value, seen + (name,))
                if fallback is not None:
                    check(fallback, seen)
                continue
            name = part.strip("\"'").lower()
            if name and name not in GENERIC_FAMILIES:
                out.append(name)

    for decl in lists:
        check(decl)
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
        if rel in BRAND_COLOUR_OK:
            continue
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


# The approved focus ring and saved glyph, locked exactly. A unit test cannot decide whether
# CSS paints a visible ring (cascade, initial values, colour spaces); a browser can, and
# the plan's real-browser accessibility check does. This lock makes any change to the
# approved declarations a deliberate edit of these sets, reviewed with that check.
# Design look (6 Oct 2026): focus is one :focus-visible rule drawn in the theme's --focus
# token (amber on dark, ink on light); saved is the closed status glyph, a filled dot in --ink-muted.
FOCUS_SELECTOR = ":focus-visible"
APPROVED_OUTLINES = {(FOCUS_SELECTOR, "outline", "2px solid var(--focus)"),
                     (FOCUS_SELECTOR, "outline-offset", "2px"),
                     (':root, [data-theme="dark"]', "--focus", "#ffab01"),
                     ('[data-theme="light"]', "--focus", "#232323")}
SAVED_SELECTOR = ".bw-status--closed .dot"
APPROVED_SAVED_FILLS = {(SAVED_SELECTOR, "fill", "var(--ink-muted)")}


def escape_violations(css):
    """The sheet uses no CSS escapes; one could spell a locked property the filter misses."""
    return ["backslash escape"] if "\\" in css else []


def focus_violations(css):
    """Every outline declaration and every --focus token anywhere in the sheet, against the approved set."""
    found = {(sel, prop, value) for sel, decls in css_blocks(css)
             for prop, value in decls if prop == "outline" or prop.startswith("outline-") or prop == "--focus"}
    return sorted(f"unapproved {d}" for d in found - APPROVED_OUTLINES) + \
        sorted(f"missing {d}" for d in APPROVED_OUTLINES - found)


def saved_fill_violations(css):
    """Every fill, stroke or background declaration on a rule that mentions the saved state or
    the closed status glyph, against the approved set."""
    found = {(sel, prop, value) for sel, decls in css_blocks(css)
             if "saved" in sel.lower() or "status--closed" in sel.lower()
             for prop, value in decls if prop in ("fill", "stroke") or prop == "background" or prop.startswith("background-")}
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

    def test_b_only_declared_brand_fonts_and_images(self):
        self.assertEqual(font_image_violations(PACKAGE), [])

    def test_c_no_prototype_or_design_system_files(self):
        self.assertEqual(banned_name_violations(PACKAGE), [])

    def test_d_text_has_no_brand_fetch_or_urls(self):
        self.assertEqual(text_violations(PACKAGE), [])
        self.assertEqual(url_violations(PACKAGE), [])
        self.assertEqual(index_reference_violations(PACKAGE), [])

    def test_e_css_fonts_focus_and_saved_state(self):
        css = (PACKAGE / "web" / "styles.css").read_text(encoding="utf-8")
        bundle = (PACKAGE / "web" / BUNDLE_PATH).read_text(encoding="utf-8")
        # Read together: the bundle uses var(--font-sans), which only styles.css declares.
        self.assertEqual(font_family_violations(css + "\n" + bundle), [])
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
                       'a:focus-visible { outline: 2px solid var(--focus); @media (x) { outline: none; } }',
                       '[data-theme="light"] { --focus: #ffffff; }',
                       ':root { --focus: transparent; }'):
            with self.subTest(change=change):
                self.assertNotEqual(focus_violations(self.SHEET + change), [])
        self.assertNotEqual(focus_violations(self.SHEET.replace("outline: 2px solid var(--focus)", "outline: 1px solid var(--focus)")), [])
        self.assertNotEqual(focus_violations(self.SHEET.replace("--focus: #232323", "--focus: #ffab01")), [])

    def test_saved_lock(self):
        self.assertEqual(saved_fill_violations(self.SHEET), [])
        for change in ('[data-status="saved"] { background: none; }',
                       '.bw-status--closed .dot { fill: none; }',
                       '@media (max-width: 720px) { .bw-status--closed .dot { fill: transparent; } }',
                       '@media (max-width: 720px) { .status-glyph[data-status="saved"] { background-color: oklch(0.7 0.1 150 / 0); } }'):
            with self.subTest(change=change):
                self.assertNotEqual(saved_fill_violations(self.SHEET + change), [])
        self.assertNotEqual(saved_fill_violations(self.SHEET.replace("fill: var(--ink-muted)", "stroke: var(--ink-muted)")), [])
        for change in ('.bw-status--closed .dot { @media (max-width: 720px) { fill: #ffffff; } }',
                       '.bw-status--closed { &.dot { background: #ffffff; } }'):
            with self.subTest(change=change):
                self.assertNotEqual(saved_fill_violations(self.SHEET + change), [])
        glued = self.SHEET.replace("fill: var(--ink-muted)", "fill: var(--ink-/**/muted)")
        self.assertNotEqual(saved_fill_violations(glued), [])

    def test_seat_names_are_found_in_any_web_file(self):
        import tempfile, shutil
        for target_rel, line in (("web/steps/assess.js", "// ExampleBWAI. Original UI."),
                                 ("web/styles.css", "/* layout by SampleBWAI */"),
                                 ("scripts/idea_store.py", "# CP3 upload seam"),
                                 ("scripts/idea_steps/method.py", '"""Authored for FORGE."""')):
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

    def _copy(self, tmp):
        import shutil
        copy = Path(tmp) / "glitch-idea"
        shutil.copytree(PACKAGE, copy)
        return copy

    def test_declared_brand_files_pass_on_the_real_package(self):
        self.assertEqual(font_image_violations(PACKAGE), [])
        self.assertEqual(banned_name_violations(PACKAGE), [])
        self.assertEqual(url_violations(PACKAGE), [])

    def test_undeclared_font_or_image_fails(self):
        import tempfile
        for rel in ("web/assets/fonts/other.woff2", "web/assets/extra.png", "scripts/stray.ttf", "web/assets/fonts/BlastworksSans-Bold.woff2"):
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as tmp:
                copy = self._copy(tmp)
                (copy / rel).write_bytes(b"x")
                self.assertTrue(any("undeclared" in v for v in font_image_violations(copy)))

    def test_declared_path_with_wrong_hash_fails(self):
        import tempfile
        for rel in ("web/assets/fonts/BlastworksSans-Regular.woff2", "web/assets/logo.svg", "web/assets/bwpm/bundle.css"):
            with self.subTest(rel=rel), tempfile.TemporaryDirectory() as tmp:
                copy = self._copy(tmp)
                with open(copy / rel, "ab") as handle:
                    handle.write(b"\n")
                self.assertTrue(any("hash" in v for v in font_image_violations(copy)))
        with tempfile.TemporaryDirectory() as tmp:
            copy = self._copy(tmp)
            (copy / "web/assets/logo.svg").unlink()
            self.assertTrue(any("missing" in v for v in font_image_violations(copy)))

    def test_bundle_css_only_at_its_declared_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            copy = self._copy(tmp)
            (copy / "web" / "bundle.css").write_text("x{}")
            (copy / "web" / "assets" / "tokens.json").write_text("{}")
            self.assertEqual(sorted(banned_name_violations(copy)), ["bundle.css", "tokens.json"])

    def test_font_face_outside_styles_css_fails(self):
        import tempfile
        face = "@font-face { font-family: 'Blastworks Sans'; src: url(./assets/fonts/BlastworksSans-Regular.woff2); }"
        with tempfile.TemporaryDirectory() as tmp:
            copy = self._copy(tmp)
            (copy / "web" / "app.js").write_text((copy / "web" / "app.js").read_text() + "\n/* " + face + " */\n")
            self.assertIn("app.js: @font-face", text_violations(copy))
        with tempfile.TemporaryDirectory() as tmp:  # allowed in styles.css, with a declared src
            copy = self._copy(tmp)
            (copy / "web" / "styles.css").write_text(self.SHEET + face + "\n")
            self.assertEqual(url_violations(copy), [])
            self.assertEqual([v for v in text_violations(copy) if "@font-face" in v], [])

    def test_url_to_a_non_declared_path_fails(self):
        import tempfile
        for rule in (".x{background:url(./assets/other.png)}", ".x{background:url('data:image/png;base64,AA')}",
                     ".x{background:URL(\"assets/fonts/evil.woff2\")}", ".x{background:url(../up.png)}",
                     "@font-face{font-family:x;src:url(./assets/logo.svg)}",
                     "@font-face{font-family:x;src:local(x)}"):
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as tmp:
                copy = self._copy(tmp)
                (copy / "web" / "styles.css").write_text(self.SHEET + rule + "\n")
                self.assertNotEqual(url_violations(copy), [])
        with tempfile.TemporaryDirectory() as tmp:
            copy = self._copy(tmp)
            (copy / "web" / "app.js").write_text((copy / "web" / "app.js").read_text() + "\nel.style.background = 'url(x.png)';\n")
            self.assertNotEqual(url_violations(copy), [])
        self.assertEqual(url_violations(PACKAGE), [])

    def test_second_named_typeface_fails(self):
        self.assertEqual(font_family_violations("a{font-family:'Blastworks Sans',system-ui,sans-serif}"), [])
        self.assertEqual(font_family_violations("a{font:600 14px/20px var(--font-sans)}:root{--font-sans:'Blastworks Sans',system-ui}"), [])
        for css in ("a{font-family:'Proxima Nova',system-ui}", "a{font-family:'Blastworks Sans','Inter',sans-serif}",
                    ":root{--font-sans:'Proxima Nova',sans-serif}", "a{font:600 14px/20px Arial}",
                    'a{--my-var:"Georgia";font-family:var(--my-var)}', 'a{font:13px var(--font-sans,"Georgia")}',
                    "a{font-family:var(--font-sans,'Georgia')}:root{--font-sans:system-ui}", "a{font-family:var(--undeclared-font)}",
                    ':root{--font-sans:system-ui}@media (min-width:600px){:root{--font-sans:"Georgia"}}a{font-family:var(--font-sans)}',
                    ':root{--font-sans:system-ui}[data-theme="light"]{--font-sans:"Georgia"}a{font-family:var(--font-sans)}',
                    ':root{--font-sans:"Georgia"}[data-theme="light"]{--font-sans:system-ui}a{font-family:var(--font-sans)}'):
            with self.subTest(css=css):
                self.assertNotEqual(font_family_violations(css), [])

    def test_brand_word_and_colour_scope(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            copy = self._copy(tmp)
            (copy / "web" / "app.js").write_text((copy / "web" / "app.js").read_text() + "\n// Blastworks\n")
            self.assertIn("app.js: blastworks", text_violations(copy))
            (copy / "web" / "folds.js").write_text((copy / "web" / "folds.js").read_text() + "\nconst c = '#ffab01';\n")
            self.assertIn("folds.js: #ffab01", brand_colour_violations(copy))
            (copy / "web" / "steps" / "review.js").write_text("// bwpm\n")
            self.assertIn("steps/review.js: bwpm", text_violations(copy))
        with tempfile.TemporaryDirectory() as tmp:  # allowed where declared
            copy = self._copy(tmp)
            (copy / "web" / "styles.css").write_text(self.SHEET + "/* Blastworks bwpm */ :root{--a:#ffab01;--b:#232323}\n")
            self.assertEqual(text_violations(copy), [])
            self.assertEqual(brand_colour_violations(copy), [])
        for rel in ("app.js", "api.js", "folds.js", "ideas.js", "setup.js", "steps/assess.js", "steps/discovery.js",
                    "steps/exploration.js", "steps/method.js", "steps/review.js", "steps/visualize.js"):
            low = (PACKAGE / "web" / rel).read_text(encoding="utf-8").lower()
            self.assertFalse(any(w in low for w in BRAND_WORDS), rel)

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
