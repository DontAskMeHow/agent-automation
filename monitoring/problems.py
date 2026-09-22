"""Каталог проблем автоматов — helper для механизмов и агента.

Записка = JSON-файл `<ISO-ts>_<source>.json` в этом же каталоге.
Конвенция — в README.md рядом. Статусы: new -> reported -> resolved.

Импорт из скрипта механизма (каталог лежит в monitoring/ рядом с problems.py,
пути каталога подставьте под структуру своего проекта):

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path("<корень проекта>") / "monitoring"))
    import problems as p
    p.add("demo", "error", "проход 1: упал", "TimeoutError: ...")
    p.close("<файл или id>", by="mechanism", note="само-вылечилось")

CLI (ручной разбор и отладка):

    python problems.py add --source demo --severity error --title "..." --detail "..."
    python problems.py list [--all]
    python problems.py report <файл|id>
    python problems.py close <файл|id> --by agent --note "починено"
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATUS_NEW = "new"
STATUS_REPORTED = "reported"
STATUS_RESOLVED = "resolved"
SEVERITIES = ("error", "warning", "info")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _slug(text):
    s = re.sub(r"[^0-9A-Za-zА-Яа-яЁё-]+", "-", (text or "source")).strip("-")
    return s[:40] or "source"


def _records():
    return sorted(ROOT.glob("*.json"))


def _read(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write(path, record):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)
    return path


def add(source, severity="error", title="", detail=""):
    """Создать записку или увеличить счётчик уже открытой с тем же source+title."""
    if severity not in SEVERITIES:
        severity = "error"
    for path in _records():
        try:
            rec = _read(path)
        except Exception:
            continue
        if rec.get("status") == STATUS_RESOLVED:
            continue
        if rec.get("source") == source and rec.get("title") == title:
            rec["count"] = int(rec.get("count", 1)) + 1
            rec["updated_ts"] = _now()
            if detail and rec.get("detail") != detail:
                rec["detail"] = (rec.get("detail", "") + "\n--\n" + detail).strip("\n")
            _write(path, rec)
            return str(path)
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    path = ROOT / f"{ts}_{_slug(source)}.json"
    if path.exists():
        path = ROOT / f"{ts}_{_slug(source)}-2.json"
    record = {
        "id": path.stem,
        "ts": _now(),
        "source": source,
        "severity": severity,
        "title": title or f"{source}: проблема",
        "detail": detail or "",
        "status": STATUS_NEW,
        "count": 1,
        "updated_ts": _now(),
    }
    _write(path, record)
    return str(path)


def list_open():
    """Нерезолвленные записки (new + reported), старые первыми."""
    out = []
    for path in _records():
        try:
            rec = _read(path)
        except Exception:
            continue
        if rec.get("status") != STATUS_RESOLVED:
            out.append({"file": str(path), **rec})
    return sorted(out, key=lambda r: r["ts"])


def list_all():
    out = []
    for path in _records():
        try:
            rec = _read(path)
        except Exception:
            continue
        out.append({"file": str(path), **rec})
    return sorted(out, key=lambda r: r["ts"])


def _resolve_ref(ref):
    for path in _records():
        if ref in (str(path), path.stem, path.name):
            return path
    return None


def mark_reported(ref):
    """new -> reported (агент сообщил пользователю). reported/resolve — без изменений."""
    path = _resolve_ref(ref)
    if not path:
        return False
    rec = _read(path)
    if rec.get("status") != STATUS_NEW:
        return False
    rec["status"] = STATUS_REPORTED
    rec["reported_ts"] = _now()
    _write(path, rec)
    return True


def close(ref, by, note=""):
    """Любой нерезолвленный статус -> resolved."""
    path = _resolve_ref(ref)
    if not path:
        return False
    rec = _read(path)
    if rec.get("status") == STATUS_RESOLVED:
        return False
    rec["status"] = STATUS_RESOLVED
    rec["resolved_ts"] = _now()
    rec["resolved_by"] = by or "unknown"
    rec["note"] = note or ""
    _write(path, rec)
    return True


def _cli():
    ap = argparse.ArgumentParser(description="Каталог проблем автоматов")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add")
    a.add_argument("--source", required=True)
    a.add_argument("--severity", default="error", choices=list(SEVERITIES))
    a.add_argument("--title", default="")
    a.add_argument("--detail", default="")

    l = sub.add_parser("list")
    l.add_argument("--all", action="store_true", dest="all_")

    r = sub.add_parser("report")
    r.add_argument("ref")

    c = sub.add_parser("close")
    c.add_argument("ref")
    c.add_argument("--by", default="unknown")
    c.add_argument("--note", default="")

    args = ap.parse_args()
    if args.cmd == "add":
        print(add(args.source, args.severity, args.title, args.detail))
    elif args.cmd == "list":
        rows = list_all() if args.all_ else list_open()
        for rec in rows:
            print(json.dumps(rec, ensure_ascii=False))
    elif args.cmd == "report":
        print("reported" if mark_reported(args.ref) else "нет записки / уже reported")
    elif args.cmd == "close":
        print("closed" if close(args.ref, args.by, args.note) else "нет записки / уже resolved")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
