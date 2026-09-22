""" md_to_pdf.py — Markdown → PDF через Chrome headless (print-to-pdf).

Использование:
  python md_to_pdf.py <in.md> <out.pdf> [--chrome <путь к chrome.exe>]

Схема: md → HTML (библиотека markdown + встроенный CSS) → Chrome в режиме
headless печатает PDF. Кириллица — системными шрифтами fromы Windows, ничего
доустанавливать не нужно. Зависимость: `markdown` (в venv скилла).
"""

import argparse
import html
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown
import markdown.extensions.tables  # noqa: F401  (регистрирует "tables")

# консоль Windows cp1251 — без этого падаем на кириллице/стрелках в выводе
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_chrome(chrome_arg=None):
    candidates = []
    if chrome_arg:
        candidates.append(Path(chrome_arg))
    candidates += [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe",
    ]
    for p in candidates:
        if p.is_file():
            return str(p)
    raise RuntimeError(
        "chrome.exe не найден — укажите путь: --chrome \"C:\\...\\chrome.exe\""
    )


_PAGE_CSS = """
  @page { size: A4; margin: 20mm 16mm 20mm 16mm; }
  body { font-family: Georgia, 'Times New Roman', serif; font-size: 11.5pt;
         line-height: 1.45; color: #1a1a1a; max-width: 100%; }
  h1 { font-size: 19pt; margin: 0 0 6mm; page-break-after: avoid; }
  h2 { font-size: 15pt; margin: 7mm 0 3mm; page-break-after: avoid;
       border-bottom: 1px solid #bbb; padding-bottom: 1.5mm; }
  h3 { font-size: 13pt; margin: 5mm 0 2mm; page-break-after: avoid; }
  h4, h5, h6 { font-size: 12pt; margin: 4mm 0 2mm; page-break-after: avoid; }
  p { margin: 0 0 2.5mm; text-align: justify; }
  ul, ol { margin: 0 0 3mm; padding-left: 7mm; }
  li { margin-bottom: 1mm; }
  blockquote { margin: 3mm 0; padding: 1.5mm 4mm; border-left: 3px solid #ccc;
               color: #444; background: #f7f7f7; }
  code { font-family: Consolas, monospace; font-size: 10pt; }
  pre { background: #f7f7f7; padding: 3mm; whitespace: pre-wrap; }
  table { border-collapse: collapse; width: 100%; margin: 3mm 0; }
  th, td { border: 1px solid #999; padding: 1.5mm 2.5mm; font-size: 10.5pt;
           vertical-align: top; }
  th { background: #efefef; }
  hr { border: none; border-top: 1px solid #999; margin: 5mm 0; }
  a { color: #1a1a1a; }
"""


def md_to_html(md_text, title):
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def render_to_pdf(md_path, out_path, chrome):
    md_text = md_path.read_text(encoding="utf-8")
    title = md_path.stem.replace("_", " ")
    with tempfile.TemporaryDirectory(prefix="md2pdf-") as tmp:
        html_path = Path(tmp) / "doc.html"
        profile_dir = Path(tmp) / "profile"
        html_path.write_text(md_to_html(md_text, title), encoding="utf-8")
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            f"--user-data-dir={profile_dir}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={out_path.resolve()}",
            html_path.as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(
                f"chrome --print-to-pdf завершился с кодом {proc.returncode}: "
                f"{(proc.stderr or b'')[:300]!r}"
            )
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise RuntimeError(f"PDF не создан: {out_path}")
    head = out_path.read_bytes()[:5]
    if head != b"%PDF-":
        raise RuntimeError(f"Файл не похож на PDF: {out_path}")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Markdown → PDF через Chrome (print-to-pdf)")
    ap.add_argument("md", type=Path, help="входной .md")
    ap.add_argument("out", type=Path, help="выходной .pdf")
    ap.add_argument("--chrome", help="путь к chrome.exe (по умолчанию — поиск по стандартным местам)")
    args = ap.parse_args()

    if not args.md.is_file():
        print(f"Нет файла: {args.md}", file=sys.stderr)
        return 1
    if args.out.suffix.lower() != ".pdf":
        args.out = args.out.with_suffix(".pdf")
    try:
        out = render_to_pdf(args.md, args.out, find_chrome(args.chrome))
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 1
    print(f"OK: {out} ({out.stat().st_size} байт)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
