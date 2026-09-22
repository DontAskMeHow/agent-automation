""" md_to_docx.py — конвертация Markdown в .docx (python-docx).

Использование:
  python md_to_docx.py <in.md> <out.docx>

Поддерживается (достаточно для итоговых резюме и отчётов):
  заголовки #–######, цитаты «>», горизонтальные линии «---», маркированные
  списки («-», «*», «+», тире-эм «—»), нумерованные «1.», таблицы (GFM),
  жирный **…**, курсив *…*, код `…`, ссылки [текст](url).
Подход намеренно «текстовый»: python-docx строит документ из параграфов
со стилями Word (Heading/List Bullet/List Number), поэтому результат
редактируется в Word как обычный документ.
"""

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

# консоль Windows cp1251 — без этого падаем на кириллице/стрелках в выводе
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*([-+*])\s+(.*)$")
_EMDASH_BULLET_RE = re.compile(r"^\s*([—–])\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")
_INLINE_RE = re.compile(r"(\*\*.+?\*\*|\*[^*\s][^*]*?\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")
_GFM_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_GFM_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def split_inline(text):
    """Текст строки → список (kind, value): kind ∈ text/bold/italic/code/link."""
    parts = []
    for token in _INLINE_RE.split(text):
        if not token:
            continue
        if token.startswith("**") and token.endswith("**") and len(token) > 4:
            parts.append(("bold", token[2:-2]))
        elif token.startswith("`") and token.endswith("`") and len(token) > 2:
            parts.append(("code", token[1:-1]))
        elif token.startswith("[") and "](" in token and token.endswith(")"):
            label, url = token[1:].rsplit("](", 1)
            parts.append(("link", label, url[:-1]))
        elif token.startswith("*") and token.endswith("*") and len(token) > 2:
            parts.append(("italic", token[1:-1]))
        else:
            parts.append(("text", token))
    return parts


def add_runs(paragraph, inline):
    for part in inline:
        kind = part[0]
        if kind == "bold":
            run = paragraph.add_run(part[1])
            run.bold = True
        elif kind == "italic":
            run = paragraph.add_run(part[1])
            run.italic = True
        elif kind == "code":
            run = paragraph.add_run(part[1])
            run.font.name = "Consolas"
            run.font.size = Pt(10)
        elif kind == "link":
            run = paragraph.add_run(part[1])
            run.underline = True
            if part[2] and part[2] != part[1]:
                paragraph.add_run(f" ({part[2]})").font.size = Pt(9)
        else:
            paragraph.add_run(part[1])
    return paragraph


def add_horizontal_rule(doc):
    """Горизонтальная линия — параграф с нижней границей."""
    p = doc.add_paragraph()
    p_pr = p._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "999999")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)
    return p


def add_table(doc, rows):
    """Строки GFM-таблицы → таблица Word (первая строка — шапка)."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    n_cols = max(len(r) for r in cells)
    table = doc.add_table(rows=len(cells), cols=n_cols)
    table.style = "Table Grid"
    for i, row in enumerate(cells):
        for j in range(n_cols):
            text = row[j] if j < len(row) else ""
            cell = table.cell(i, j)
            cell.paragraphs[0].text = ""
            add_runs(cell.paragraphs[0], split_inline(text))
    if cells:
        for j in range(n_cols):
            for run in table.cell(0, j).paragraphs[0].runs:
                run.bold = True
    return table


def convert(md_path, out_path):
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    # кириллица: для всех стилей заголовков шрифт с восточно-европейским набором
    for style_name in ("Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        try:
            style = doc.styles[style_name]
            style.font.name = "Calibri Light"
        except KeyError:
            pass

    pending_table = []
    lines = md_path.read_text(encoding="utf-8").splitlines()

    def flush_table():
        if pending_table:
            add_table(doc, pending_table)
            pending_table.clear()
            doc.add_paragraph()

    for line in lines:
        if _GFM_ROW_RE.match(line):
            if pending_table and _GFM_SEP_RE.match(line):
                continue  # строка-разделитель заголовка/тела
            pending_table.append(line)
            continue
        if pending_table:
            flush_table()

        stripped = line.strip()
        if not stripped:
            doc.add_paragraph()
            continue
        if _HR_RE.match(stripped):
            add_horizontal_rule(doc)
            continue

        m = _HEADING_RE.match(line)
        if m:
            level = min(len(m.group(1)), 6)
            p = doc.add_paragraph(style=f"Heading {level}")
            add_runs(p, split_inline(m.group(2).strip()))
            continue
        if line.startswith(">"):
            p = doc.add_paragraph(style="Intense Quote")
            add_runs(p, split_inline(line.lstrip("> ").strip()))
            continue
        m = _BULLET_RE.match(line) or _EMDASH_BULLET_RE.match(line)
        if m:
            p = doc.add_paragraph(style="List Bullet")
            add_runs(p, split_inline(m.group(2).strip()))
            continue
        m = _ORDERED_RE.match(line)
        if m:
            p = doc.add_paragraph(style="List Number")
            add_runs(p, split_inline(m.group(2).strip()))
            continue

        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        add_runs(p, split_inline(stripped))

    flush_table()
    doc.save(str(out_path))


def main():
    ap = argparse.ArgumentParser(description="Markdown → .docx (python-docx)")
    ap.add_argument("md", type=Path, help="входной .md")
    ap.add_argument("out", type=Path, help="выходной .docx")
    args = ap.parse_args()

    if not args.md.is_file():
        print(f"Нет файла: {args.md}", file=sys.stderr)
        return 1
    if args.out.suffix.lower() != ".docx":
        args.out = args.out.with_suffix(".docx")
    convert(args.md, args.out)
    print(f"OK: {args.out} ({args.out.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
