#!/usr/bin/env python3
"""Build a static site from each book's 读书笔记.md.

The essay files are read and rendered. They are never rewritten.
New books appear when a folder containing 读书笔记.md is added at the
repository root. Wiki index and log supply the date and summary when
those files mention the book. The author, when shown, is taken from
the essay's own Sources line.
"""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote

import markdown

NOTE_NAME = "读书笔记.md"
EXCERPT_NAME = "读书笔记-摘抄原文.md"
PUBLIC_ORIGIN = "https://ghsshou.github.io"
AUTHOR_RE = re.compile(r"^>\s*Sources:\s*(.+?)，《", re.M)
INDEX_ROW_RE = re.compile(
    r"^\|\s*\[(?P<label>[^\]]+)\]\((?P<href>[^)]+)\)\s*\|\s*(?P<summary>.*?)\s*\|\s*(?P<updated>\d{4}-\d{2}-\d{2})\s*\|"
)
LOG_ENTRY_RE = re.compile(
    r"^## \[(?P<date>\d{4}-\d{2}-\d{2})\][^\n]*\|\s*(?P<label>.+?)\s*$",
    re.M,
)
HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)


@dataclass
class Book:
    name: str
    note_text: str
    excerpt_text: str | None
    author: str | None
    date: str | None
    summary: str | None
    order: int

    @property
    def heading(self) -> str:
        match = HEADING_RE.search(self.note_text)
        if match:
            return match.group(1).strip()
        return f"{self.name} 读书笔记"

    @property
    def excerpt_heading(self) -> str | None:
        if not self.excerpt_text:
            return None
        match = HEADING_RE.search(self.excerpt_text)
        if match:
            return match.group(1).strip()
        return f"{self.name} 读书笔记-摘抄原文"


def normalize_base(base: str) -> str:
    base = (base or "").strip()
    if base in {"", "/"}:
        return ""
    if not base.startswith("/"):
        base = "/" + base
    return base.rstrip("/")


def prepare_markdown(text: str) -> str:
    """Keep consecutive blockquote lines on separate visual lines.

    Standard Markdown would join them with a space. A hard break preserves
    the line structure already in the note without changing any words.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    prepared: list[str] = []
    for index, line in enumerate(lines):
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if line.startswith(">") and nxt.startswith(">") and not line.endswith("  "):
            line += "  "
        prepared.append(line)
    return "\n".join(prepared)


def render_markdown(text: str) -> str:
    converter = markdown.Markdown(
        extensions=["sane_lists", "fenced_code", "tables"],
        output_format="html5",
    )
    return converter.convert(prepare_markdown(text))


def rewrite_excerpt_link(html: str, excerpt_href: str | None) -> str:
    if not excerpt_href:
        return html
    return re.sub(
        r'href="(?:\./)?读书笔记-摘抄原文\.md"',
        f'href="{excerpt_href}"',
        html,
    )


def parse_author(note_text: str) -> str | None:
    match = AUTHOR_RE.search(note_text)
    if not match:
        return None
    author = match.group(1).strip()
    return author or None


def parse_index(text: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    order = 0
    for line in text.splitlines():
        match = INDEX_ROW_RE.match(line.strip())
        if not match:
            continue
        parts = [part for part in match.group("href").split("/") if part not in {"", "."}]
        while parts and parts[0] == "..":
            parts.pop(0)
        if len(parts) < 2 or parts[-1] != NOTE_NAME:
            continue
        folder = parts[-2]
        found[folder] = {
            "summary": match.group("summary").strip(),
            "date": match.group("updated"),
            "order": order,
        }
        order += 1
    return found


def parse_log(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in LOG_ENTRY_RE.finditer(text):
        label = match.group("label").strip()
        label = re.sub(r"\s*(读书笔记|读后感)\s*$", "", label)
        label = label.strip("《》 ").strip()
        if label and label not in found:
            found[label] = match.group("date")
    return found


def discover(root: Path) -> list[Book]:
    index_path = root / "wiki" / "index.md"
    log_path = root / "wiki" / "log.md"
    index = parse_index(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {}
    log = parse_log(log_path.read_text(encoding="utf-8")) if log_path.is_file() else {}
    books: list[Book] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        note_path = child / NOTE_NAME
        if not note_path.is_file():
            continue
        if not note_path.resolve().is_relative_to(root.resolve()):
            continue
        note_text = note_path.read_text(encoding="utf-8")
        excerpt_path = child / EXCERPT_NAME
        meta = index.get(child.name, {})
        books.append(
            Book(
                name=child.name,
                note_text=note_text,
                excerpt_text=excerpt_path.read_text(encoding="utf-8") if excerpt_path.is_file() else None,
                author=parse_author(note_text),
                date=meta.get("date") or log.get(child.name),
                summary=meta.get("summary") or None,
                order=meta.get("order", 10_000),
            )
        )
    books.sort(key=lambda book: (book.date is None, _date_key(book.date), book.order, book.name))
    return books


def _date_key(date: str | None) -> int:
    if not date:
        return 0
    return -int(date.replace("-", ""))


def book_href(base: str, name: str) -> str:
    return f"{base}/books/{quote(name, safe='')}/"


def excerpt_href(base: str, name: str) -> str:
    return f"{base}/books/{quote(name, safe='')}/excerpts/"


def page_document(*, title: str, description: str | None, canonical_path: str, base: str, body_class: str, body: str) -> str:
    description_tag = ""
    if description:
        description_tag = f'\n  <meta name="description" content="{escape(description, quote=True)}">'
    canonical = PUBLIC_ORIGIN + canonical_path
    return f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>{description_tag}
  <link rel="canonical" href="{escape(canonical, quote=True)}">
  <link rel="icon" href="{base}/assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{base}/assets/style.css">
</head>
<body class="{body_class}">
  <a class="skip" href="#content">跳到正文</a>
  <div class="wrap">
{body}
  </div>
</body>
</html>
"""


def render_index(books: list[Book], base: str) -> str:
    if books:
        count = f"{len(books)} 篇"
        items = []
        for book in books:
            meta_bits = []
            if book.date:
                meta_bits.append(
                    f"<time datetime=\"{escape(book.date, quote=True)}\">{escape(book.date)}</time>"
                )
            if book.author:
                meta_bits.append(f"<span class=\"author\">{escape(book.author)}</span>")
            separator = '<span class="dot" aria-hidden="true">·</span>'
            meta = f"<p class=\"meta\">{separator.join(meta_bits)}</p>" if meta_bits else ""
            summary = f"<p class=\"summary\">{escape(book.summary)}</p>" if book.summary else ""
            items.append(
                "        <li>\n"
                f"          <a href=\"{book_href(base, book.name)}\">\n"
                f"            {meta}\n"
                f"            <h2>{escape(book.name)}</h2>\n"
                f"            {summary}\n"
                "          </a>\n"
                "        </li>"
            )
        listing = "      <ol class=\"books\">\n" + "\n".join(items) + "\n      </ol>"
    else:
        count = "还没有笔记"
        listing = "      <p class=\"empty\">还没有读书笔记。</p>"
    body = f"""    <header class="mast">
      <h1>读书笔记</h1>
      <p class="count">{count}</p>
    </header>
    <main id="content">
      <nav aria-label="笔记">
{listing}
      </nav>
    </main>"""
    return page_document(
        title="读书笔记",
        description=None,
        canonical_path=f"{base}/",
        base=base,
        body_class="home",
        body=body,
    )


def render_note_page(book: Book, base: str, *, excerpt: bool) -> str:
    source = book.excerpt_text if excerpt else book.note_text
    if source is None:
        raise ValueError(f"{book.name} has no excerpt")
    href = excerpt_href(base, book.name) if excerpt else book_href(base, book.name)
    target = excerpt_href(base, book.name) if book.excerpt_text else None
    article_html = rewrite_excerpt_link(render_markdown(source), target)
    if excerpt:
        nav_right = f"<a href=\"{book_href(base, book.name)}\">返回笔记</a>"
        title = book.excerpt_heading or book.name
        description = None
    else:
        nav_right = f"<a href=\"{excerpt_href(base, book.name)}\">摘抄原文</a>" if book.excerpt_text else ""
        title = book.heading
        description = book.summary
    body = f"""    <header class="top">
      <a href="{base}/">读书笔记</a>
      {nav_right}
    </header>
    <main id="content">
      <article>
{article_html}
      </article>
      <p class="back"><a href="{base}/">全部笔记</a></p>
    </main>"""
    return page_document(
        title=title,
        description=description,
        canonical_path=href,
        base=base,
        body_class="note" if not excerpt else "excerpt",
        body=body,
    )


def build_site(root: Path, out: Path, base: str) -> list[Book]:
    root = root.resolve()
    out = out.resolve()
    base = normalize_base(base)
    if out == root or out in root.parents:
        raise SystemExit(f"refusing to write site output to {out}")
    books = discover(root)
    if out.exists():
        shutil.rmtree(out)
    assets = out / "assets"
    assets.mkdir(parents=True)
    static = Path(__file__).resolve().parent / "static"
    shutil.copy(static / "style.css", assets / "style.css")
    shutil.copy(static / "favicon.svg", assets / "favicon.svg")
    (out / "index.html").write_text(render_index(books, base), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "404.html").write_text(render_missing(base), encoding="utf-8")
    for book in books:
        essay_dir = out / "books" / book.name
        essay_dir.mkdir(parents=True)
        (essay_dir / "index.html").write_text(render_note_page(book, base, excerpt=False), encoding="utf-8")
        if book.excerpt_text is not None:
            excerpt_dir = essay_dir / "excerpts"
            excerpt_dir.mkdir()
            (excerpt_dir / "index.html").write_text(
                render_note_page(book, base, excerpt=True),
                encoding="utf-8",
            )
    return books


def render_missing(base: str) -> str:
    body = f"""    <header class="mast">
      <h1>没有这一页</h1>
    </header>
    <main id="content">
      <p class="empty"><a href="{base}/">回到读书笔记</a></p>
    </main>"""
    return page_document(
        title="没有这一页",
        description=None,
        canonical_path=f"{base}/404.html",
        base=base,
        body_class="home",
        body=body,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the reading-notes site.")
    parser.add_argument("--base", default="/book_reading")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = args.out if args.out is not None else root / "_site"
    books = build_site(root, out, args.base)
    print(f"wrote {len(books)} books to {out}")


if __name__ == "__main__":
    main()
