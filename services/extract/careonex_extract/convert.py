"""Document bytes -> Markdown. Pure functions, no AWS.

The goal is text whose hash changes only when the *content* changes. Raw HTML
from nj.gov and va.gov differs on every request (nonces, timestamps, menus), so
we strip boilerplate with trafilatura and normalise whitespace. PDFs go through
pymupdf4llm, which keeps headings and renders tables as Markdown tables."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from bs4 import Comment

EXTRACTOR_VERSION = "3"  # 1: trafilatura (dropped nj.gov eligibility); 2: markdownify (nav-first strip emptied nj.gov); 3: main-first, wrapper-safe


def normalise(md: str) -> str:
    md = md.replace("\r\n", "\n").replace(" ", " ")
    md = re.sub(r"[ \t]+\n", "\n", md)            # trailing spaces
    md = re.sub(r"\n{3,}", "\n\n", md)            # collapse blank runs
    md = re.sub(r"[ \t]{2,}", " ", md)            # internal runs of spaces
    return md.strip() + "\n"


# Class/id fragments that mark boilerplate inside the main region.
_STRIP_HINTS = ("breadcrumb", "skip", "share", "social", "cookie", "banner", "menu", "sidebar", "toolbar", "pagination", "translate", "search")
_MAIN_SELECTORS = ("main", "[role=main]", "#main-content", "#main", "#content", ".main-content", ".content", "article", "body")


def _select_main(soup):
    from bs4 import Tag

    for sel in _MAIN_SELECTORS:
        node = soup.select_one(sel)
        if isinstance(node, Tag) and len(node.get_text(" ", strip=True)) > 200:
            return node
    return soup.body or soup


def html_to_markdown(data: bytes, url: str | None = None) -> str:
    """Deterministic, inclusive HTML -> Markdown: keep everything a reader sees in the main region,
    drop everything they do not (scripts, nav, footer, forms). Page title becomes the H1 when the
    region has none. Content-detection heuristics (trafilatura, readability) were tried first and
    silently dropped the eligibility paragraphs on nj.gov pages, so we do not guess.

    Order matters: select the main region *before* stripping. nj.gov wraps <main> inside a
    <nav class="navbar">, so stripping nav first deletes the whole page."""
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup.find_all(("script", "style", "noscript", "template", "svg", "iframe")):
        tag.decompose()
    main = _select_main(soup)
    for tag in main.find_all(("nav", "header", "footer", "aside", "form", "button", "input", "select", "option")):
        tag.decompose()
    main_chars = max(1, len(main.get_text(" ", strip=True)))
    for el in list(main.find_all(True)):
        # Children of an element decomposed earlier in this loop have attrs=None; skip them.
        if el.attrs is None or getattr(el, "decomposed", False):
            continue
        marker = " ".join([*(el.get("class") or []), el.get("id") or "", el.get("role") or ""]).lower()
        if not (marker and any(h in marker for h in _STRIP_HINTS)):
            continue
        # A hint on a *wrapper* (va.gov puts sidebar + article in one "sidebarnav-wrapper" row) must
        # not delete the content. Only strip elements that are clearly a widget: small, no article/H1.
        if el.find(("article", "h1")) is not None or len(el.get_text(" ", strip=True)) > 0.4 * main_chars:
            continue
        el.decompose()
    for c in main.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    # strip=["a"] keeps link text and drops the URL: retrieval text should not carry hrefs.
    md = markdownify(str(main), heading_style="ATX", bullets="-", strip=["img", "a"], escape_underscores=False, escape_asterisks=False)
    md = re.sub(r"^\s*[-*]\s*$", "", md, flags=re.M)  # empty bullets left by stripped elements
    if title and not re.search(r"^#\s", md, flags=re.M):
        md = f"# {title}\n\n{md}"
    return normalise(md)


def pdf_to_markdown(data: bytes) -> str:
    import pymupdf4llm

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as fh:
        fh.write(data)
        fh.flush()
        md = pymupdf4llm.to_markdown(fh.name, show_progress=False)
    return normalise(md)


def to_markdown(data: bytes, kind: str, file_name: str = "", url: str | None = None) -> str:
    k = (kind or Path(file_name).suffix.lstrip(".")).lower()
    if k == "pdf":
        return pdf_to_markdown(data)
    if k in ("html", "htm"):
        return html_to_markdown(data, url=url)
    raise ValueError(f"unsupported document kind {kind!r} for {file_name}")
