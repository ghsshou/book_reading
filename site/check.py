#!/usr/bin/env python3
"""Build the site and check that essays survive unchanged.

The check compares the words of each 读书笔记.md and 读书笔记-摘抄原文.md
with the generated article, and checks that every internal link stays
under the /book_reading/ base path.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlparse

import build

ROOT = Path(__file__).resolve().parents[1]
BASE = "/book_reading"
NOTE_NAME = build.NOTE_NAME
EXCERPT_NAME = build.EXCERPT_NAME
SKIP_PARTS = {".git", "_site", "__pycache__"}


def snapshot(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.parts[0] in SKIP_PARTS or any(part == "__pycache__" for part in rel.parts):
            continue
        if path.suffix == ".pyc":
            continue
        hashes[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def visible_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"[-*_]{3,}", stripped):
            continue
        stripped = re.sub(r"^>\s?", "", stripped)
        stripped = re.sub(r"^#{1,6}\s+", "", stripped)
        stripped = re.sub(r"^[-*+]\s+", "", stripped)
        stripped = re.sub(r"^\d+\.\s+", "", stripped)
        lines.append(stripped)
    return re.sub(r"\s+", "", "".join(lines))


def visible_html(article: str) -> str:
    text = re.sub(r"<[^>]+>", "", article)
    return re.sub(r"\s+", "", unescape(text))


def article_html(page: str) -> str:
    match = re.search(r"<article>\s*(.*?)\s*</article>", page, re.S)
    if not match:
        raise AssertionError("page has no article")
    return match.group(1)


def assert_same_text(label: str, source: str, page: str) -> None:
    expected = visible_markdown(source)
    actual = visible_html(article_html(page))
    if expected == actual:
        return
    limit = min(len(expected), len(actual))
    index = next((i for i in range(limit) if expected[i] != actual[i]), limit)
    start = max(0, index - 24)
    raise AssertionError(
        f"{label} text diverged at {index}\n"
        f"  source: {expected[start:index + 24]!r}\n"
        f"  page:   {actual[start:index + 24]!r}\n"
        f"  lengths: source {len(expected)} page {len(actual)}"
    )


def resolve_href(out: Path, href: str) -> Path:
    path = unquote(urlparse(href).path)
    if not path.startswith(BASE + "/") and path != BASE:
        raise AssertionError(f"link escapes base path: {href}")
    rel = path[len(BASE):].lstrip("/")
    if rel == "":
        return out / "index.html"
    target = out / rel
    if rel.endswith("/") or "." not in Path(rel).name:
        return out / rel.strip("/") / "index.html"
    return target


def check_links(out: Path) -> None:
    pages = list(out.rglob("*.html"))
    if not pages:
        raise AssertionError("site has no pages")
    for page in pages:
        html = page.read_text(encoding="utf-8")
        if 'href="/assets/' in html or 'href="/books/' in html:
            raise AssertionError(f"{page} has a root-absolute link outside {BASE}")
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if href.startswith(PUBLIC_PREFIX):
                continue
            target = resolve_href(out, href)
            if not target.is_file():
                raise AssertionError(f"{page.name} links to missing {href} ({target})")
        if "读书笔记-摘抄原文.md" in html:
            raise AssertionError(f"{page} still links to the raw excerpt file")


PUBLIC_PREFIX = "https://ghsshou.github.io/book_reading/"


def check_site(root: Path, out: Path) -> None:
    books = build.discover(root)
    note_dirs = {
        path.parent.name
        for path in root.glob(f"*/{NOTE_NAME}")
        if path.is_file() and not path.parent.name.startswith(".")
    }
    if {book.name for book in books} != note_dirs:
        raise AssertionError(f"discovered {sorted(book.name for book in books)} != {sorted(note_dirs)}")
    index = (out / "index.html").read_text(encoding="utf-8")
    titles = re.findall(r"<h2>(.*?)</h2>", index)
    if titles != [book.name for book in books]:
        raise AssertionError(f"homepage order {titles} != {[book.name for book in books]}")
    if f"{len(books)} 篇" not in index:
        raise AssertionError("homepage count is wrong")
    for book in books:
        if book.name not in index:
            raise AssertionError(f"{book.name} missing from homepage")
        if book.summary and book.summary not in index:
            raise AssertionError(f"summary missing for {book.name}")
        if book.date and f'datetime="{book.date}"' not in index:
            raise AssertionError(f"date missing for {book.name}")
        if book.author and book.author not in index:
            raise AssertionError(f"author missing for {book.name}")
        essay = out / "books" / book.name / "index.html"
        if not essay.is_file():
            raise AssertionError(f"missing essay page for {book.name}")
        essay_html = essay.read_text(encoding="utf-8")
        assert_same_text(book.name, book.note_text, essay_html)
        if f"{BASE}/assets/style.css" not in essay_html:
            raise AssertionError(f"{book.name} is missing the site stylesheet")
        excerpt_page = out / "books" / book.name / "excerpts" / "index.html"
        if book.excerpt_text is None:
            if excerpt_page.exists():
                raise AssertionError(f"invented excerpt page for {book.name}")
        else:
            if not excerpt_page.is_file():
                raise AssertionError(f"missing excerpt page for {book.name}")
            assert_same_text(f"{book.name} excerpt", book.excerpt_text, excerpt_page.read_text(encoding="utf-8"))
            if excerpt_href_missing(essay_html, book.name):
                raise AssertionError(f"{book.name} essay does not link to its excerpt page")
    for path in out.rglob("*"):
        if path.name in {NOTE_NAME, EXCERPT_NAME, "读书笔记-无格式版.txt", "笔记原文.txt", "想补充的总体感受.txt"}:
            raise AssertionError(f"published a source note: {path}")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in out.rglob("*.html"))
    if "Knowledge Base Index" in combined:
        raise AssertionError("wiki index was published as a page")
    check_links(out)


def excerpt_href_missing(essay_html: str, name: str) -> bool:
    return build.excerpt_href(BASE, name) not in essay_html


def check_new_book_is_discovered() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        kept = root / "只有原文"
        kept.mkdir()
        (kept / "笔记原文.txt").write_text("不要收录这段。", encoding="utf-8")
        folder = root / "新的一本书"
        folder.mkdir()
        (folder / NOTE_NAME).write_text(
            "# 《新的一本书》读书笔记\n\n"
            "> Sources: 某人，《新的一本书》，2020\n"
            "> Raw: [摘抄原文](读书笔记-摘抄原文.md)\n\n"
            "正文里有一句不能改的话。\n",
            encoding="utf-8",
        )
        (folder / EXCERPT_NAME).write_text(
            "# 新的一本书 读书笔记-摘抄原文\n\n摘抄一句。\n",
            encoding="utf-8",
        )
        wiki = root / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "| Article | Summary | Updated |\n|---|---|---|\n"
            "| [新的一本书 读书笔记](../新的一本书/读书笔记.md) | 一句摘要。 | 2026-01-02 |\n",
            encoding="utf-8",
        )
        (wiki / "log.md").write_text(
            "## [2026-01-02] ingest | 新的一本书 读书笔记\n",
            encoding="utf-8",
        )
        out = root / "_site"
        books = build.build_site(root, out, BASE)
        if [book.name for book in books] != ["新的一本书"]:
            raise AssertionError(f"unexpected books: {[book.name for book in books]}")
        if books[0].author != "某人" or books[0].date != "2026-01-02" or books[0].summary != "一句摘要。":
            raise AssertionError(f"metadata parsed wrong: {books[0]}")
        index = (out / "index.html").read_text(encoding="utf-8")
        if "不要收录这段" in index or "只有原文" in index:
            raise AssertionError("a folder without 读书笔记.md was published")
        essay = (out / "books" / "新的一本书" / "index.html").read_text(encoding="utf-8")
        assert_same_text("新的一本书", (folder / NOTE_NAME).read_text(encoding="utf-8"), essay)
        if "读书笔记-摘抄原文.md" in essay:
            raise AssertionError("excerpt link was not rewritten")
        check_links(out)


def main() -> None:
    before = snapshot(ROOT)
    out = ROOT / "_site"
    books = build.build_site(ROOT, out, BASE)
    after = snapshot(ROOT)
    if before != after:
        changed = sorted(set(before) ^ set(after) | {key for key in before if before.get(key) != after.get(key)})
        raise SystemExit(f"build modified source files: {changed}")
    check_site(ROOT, out)
    check_new_book_is_discovered()
    print(f"ok: {len(books)} books, base {BASE}")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
