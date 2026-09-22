"""health: проверка состояния воркспейса по запросу пользователя.

Команды:
    problems [--all] -- незакрытые записки каталога monitoring/
    report <id>      -- new -> reported (агент сообщил пользователю)
    close <id> --note N -- закрыть записку (resolved, by=agent)
    check            -- полная диагностика: Планировщик, прогоны, индексы,
                        Bright Data, система, git

Только stdlib; корень воркспейса -- env WORKSPACE (дефолт -- корень этого
репозитория), остальные пути и настройки -- константы ниже.
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- настройки ---
# Корень воркспейса: env WORKSPACE, дефолт -- корень этого репозитория
# (health.py лежит в monitoring/). Остальные пути -- от корня.
WS = Path(os.environ.get("WORKSPACE", "").strip()
         or Path(__file__).resolve().parents[1])
PROBLEMS_DIR = WS / "monitoring"
COLLECTOR_LOG = WS / "logs" / "collector.log"
COLLECTOR_STATUS = WS / "logs" / "collector.status.json"
CHECKER_LOG = WS / "logs" / "checker.log"
PERIODIC_DIR = WS / "daemon"
SKILLS_SEARCH = WS / "memory" / "skills-search.py"
SKILLS_ROOT = WS / "memory" / "records"
MCP_CALL = WS / "bridges" / "brightdata" / "mcp_call.py"

# имена задач Планировщика Windows для первой секции check
# (примеры -- подставьте имена своих задач)
SCHED_TASKS = ("periodic-demo", "demo-collector", "demo-bridge")


def run(cmd, cwd=None, timeout=120, extra_env=None):
    """subprocess с забором вывода; ошибку ловит вызывающая секция."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(cmd, cwd=cwd, env=env,
                          capture_output=True, timeout=timeout)


def decode_windows(raw):
    """Буфер утилит Windows (cp866) -> str; с нулями -- utf-16-le (powershell)."""
    if not raw:
        return ""
    if b"\x00" in raw[:200]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("cp866", errors="replace")


def file_tail(path, n):
    """Последние n строк файла (читаем хвост байтами, utf-8 с replace)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 8192))
        text = f.read().decode("utf-8", errors="replace")
    return text.splitlines()[-n:]


def mem_kb(cell):
    """'51 512 КБ' / '51,512 K' -> int КБ (0 при мусоре)."""
    return int(re.sub(r"\D", "", cell or "") or "0")


def last_result_label(code):
    """Код последнего результата задачи -> человеческая пометка."""
    return {
        "0": "успех",
        "267009": "выполняется сейчас",
        "267011": "ещё ни разу не выполнялась",
    }.get(str(code).strip(), str(code))


def clip(text, limit):
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


# --- диагноз: секции check ---


def find_col(header, names):
    """Индекс колонки CSV по набору вариантов имени (рус/англ) или None."""
    for i, h in enumerate(header):
        if h.strip().lower() in names:
            return i
    return None


def section_scheduler():
    print("== 1. Задачи Планировщика ==")
    try:
        r = run(["schtasks", "/query", "/fo", "csv", "/v"], timeout=120)
    except Exception as e:
        print(f"  секция недоступна: schtasks: {e}")
        return
    text = decode_windows(r.stdout)
    if not text.strip():
        print(f"  секция недоступна: schtasks вернул пустой вывод (rc={r.returncode})")
        return
    rows = list(csv.reader(text.splitlines()))
    if not rows or not rows[0]:
        print("  секция недоступна: schtasks не выдал CSV-строк")
        return
    header = [h.strip().lower() for h in rows[0]]
    i_name = find_col(header, ("имя задачи", "taskname"))
    i_state = find_col(header, ("состояние", "status"))
    i_result = find_col(header, ("прошлый результат", "последний результат",
                                 "last result"))
    shown = 0
    seen = set()
    for row in rows[1:]:
        cells = [c.strip() for c in row]
        # путь задачи вроде \employer-check: ищем ячейку с именем из фильтра
        task = next((c for c in cells
                     if c.startswith("\\") and any(n in c for n in SCHED_TASKS)),
                    None)
        if not task:
            continue
        name = task.strip("\\").split("\\")[-1] or task
        if name in seen:
            continue
        seen.add(name)
        state = cells[i_state] if i_state is not None and i_state < len(cells) else "?"
        result = cells[i_result] if i_result is not None and i_result < len(cells) else "?"
        print(f"  {name:<22} стейт: {state:<14} результат: "
              f"{result} ({last_result_label(result)})")
        shown += 1
    if not shown:
        print("  задачи не найдены (имена не совпали с фильтром)")


def section_runs():
    print("== 2. Последние проходы ==")
    print("  -- collector --")
    if not Path(COLLECTOR_LOG).is_file():
        print("  collector.log не найден")
    else:
        try:
            for ln in file_tail(COLLECTOR_LOG, 8):
                print("  ", ln)
        except Exception as e:
            print(f"  collector.log недоступен: {e}")
    if not Path(COLLECTOR_STATUS).is_file():
        print("  collector.status.json не найден")
    else:
        try:
            with open(COLLECTOR_STATUS, encoding="utf-8") as f:
                st = json.load(f)
            live = {k: v for k, v in st.get("sources", {}).items() if v}
            print(f"  статус: last_run={st.get('last_run')} dry={st.get('dry')} "
                  f"источники-ок: {live or '—'}")
        except Exception as e:
            print(f"  статус недоступен: status.json не прочитан: {e}")
    print("  -- checker --")
    if not Path(CHECKER_LOG).is_file():
        print("  checker.log не найден")
    else:
        try:
            for ln in file_tail(CHECKER_LOG, 6):
                print("  ", ln)
        except Exception as e:
            print(f"  checker.log недоступен: {e}")
    print("  -- periodic --")
    logs = sorted(Path(PERIODIC_DIR).glob("periodic-*.log"),
                  key=lambda p: p.stat().st_mtime)
    if not logs:
        print("  periodic-*.log не найден")
    else:
        newest = logs[-1]
        mtime = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  свежий лог: {newest.name} ({mtime})")
        try:
            tail = file_tail(newest, 60)
            rises = [ln for ln in tail if "поднят" in ln]
            errs = [ln for ln in tail
                    if re.search(r"ошибк|fail|error|exception", ln, re.I)]
            for ln in rises[-3:]:
                print("  [поднято] ", ln)
            for ln in errs[-3:]:
                print("  [ошибка]  ", ln)
            if not rises and not errs:
                for ln in tail[-6:]:
                    print("  ", ln)
        except Exception as e:
            print(f"  periodic-лог недоступен: {e}")


def section_indexes():
    print("== 3. Индексы памяти ==")
    if not Path(SKILLS_SEARCH).is_file():
        print("  секция недоступна: skills-search.py не найден")
        return
    try:
        r = run([sys.executable, SKILLS_SEARCH, "status"],
                cwd=str(Path(SKILLS_SEARCH).parent),
                extra_env={"SKILLS_ROOT": SKILLS_ROOT}, timeout=180)
    except Exception as e:
        print(f"  секция недоступна: skills-search status: {e}")
        return
    if r.returncode != 0:
        print(f"  секция недоступна: skills-search status rc={r.returncode}: "
              f"{clip(decode_windows(r.stderr), 300)}")
        return
    lines = r.stdout.decode("utf-8", errors="replace").splitlines()
    for ln in lines[-40:]:
        print("  ", ln)


def section_brightdata():
    print("== 4. Bright Data ==")
    mcp = Path(MCP_CALL)
    if not mcp.is_file():
        print("  недоступен (нет вызова: mcp_call.py не найден)")
        return
    # сначала смотрим --help: принимает ли session-stats
    try:
        h = run([sys.executable, mcp.name, "--help"], cwd=str(mcp.parent),
                timeout=60)
    except Exception as e:
        print(f"  секция недоступна: mcp_call --help: {e}")
        return
    help_text = (h.stdout + h.stderr).decode("utf-8", errors="replace")
    if "session-stats" not in help_text:
        print("  недоступен (нет вызова: mcp_call.py не принимает session-stats)")
        return
    try:
        r = run([sys.executable, mcp.name, "session-stats"], cwd=str(mcp.parent),
                timeout=180)
    except Exception as e:
        print(f"  секция недоступна: mcp_call session-stats: {e}")
        return
    out_text = r.stdout.decode("utf-8", errors="replace")
    if r.returncode != 0:
        print(f"  секция недоступна: mcp_call rc={r.returncode}: "
              f"{clip(out_text or decode_windows(r.stderr), 300)}")
        return
    print(clip(out_text, 2600) or "(пустой ответ)")


def section_system():
    print("== 5. Система ==")
    # процессы: Windows tasklist, вывод в cp866
    try:
        r = run(["tasklist", "/fo", "csv"], timeout=60)
    except Exception as e:
        print(f"  секция недоступна: tasklist: {e}")
    else:
        rows = list(csv.reader(decode_windows(r.stdout).splitlines()))
        if not rows:
            print("  секция недоступна: tasklist не выдал строк")
        else:
            header = [h.strip().lower() for h in rows[0]]
            i_mem = find_col(header, ("память", "mem usage"))
            if i_mem is None:
                i_mem = -1
            procs = []
            py_count = chrome_count = 0
            for row in rows[1:]:
                if not row or not row[0]:
                    continue
                base = Path(row[0].strip()).name.lower()
                mem = mem_kb(row[i_mem]) if abs(i_mem) < len(row) else 0
                if base in ("python.exe", "pythonw.exe"):
                    py_count += 1
                    procs.append((row[0].strip(), mem))
                elif base == "chrome.exe":
                    chrome_count += 1
                    procs.append((row[0].strip(), mem))
            procs.sort(key=lambda x: -x[1])
            print(f"  процессов python/pythonw: {py_count} (всего, включая демоны); "
                  f"chrome: {chrome_count}")
            print("  top по памяти среди python/pythonw/chrome:")
            for name, kb in procs[:8]:
                print(f"    {name:<26} {kb / 1024:7.0f} МБ")
    # RAM: powershell; неудача -- честная строка
    try:
        r = run(["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_OperatingSystem | Select-Object "
                 "FreePhysicalMemory,TotalVisibleMemorySize | Format-List"],
                timeout=120)
    except Exception as e:
        print(f"  RAM недоступна: {e}")
    else:
        text = decode_windows(r.stdout)
        if not text.strip():
            print(f"  RAM недоступна: powershell вернул пусто (rc={r.returncode})")
        else:
            free = re.search(r"FreePhysicalMemory\s*:\s*(\d+)", text)
            total = re.search(r"TotalVisibleMemorySize\s*:\s*(\d+)", text)
            if free and total:
                print(f"  RAM: свободно {int(free.group(1))/1024/1024:.1f} ГБ "
                      f"из {int(total.group(1))/1024/1024:.1f} ГБ")
            else:
                print(f"  RAM: распознать не удалось: {clip(text, 300)}")


def section_git():
    print("== 6. git ==")
    try:
        r = run(["git", "-C", WS, "status", "--short"], timeout=60)
    except Exception as e:
        print(f"  секция недоступна: git: {e}")
        return
    if r.returncode != 0:
        print(f"  секция недоступна: git rc={r.returncode}: "
              f"{clip(decode_windows(r.stderr), 300)}")
        return
    lines = r.stdout.decode("utf-8", errors="replace").splitlines()
    if not lines:
        print("  чисто")
    for ln in lines[:60]:
        print("  ", ln)
    if len(lines) > 60:
        print(f"  … и ещё {len(lines) - 60}")


# --- команды каталога проблем ---


def load_problems():
    """Импорт problems.py из каталога записок."""
    sys.path.insert(0, str(PROBLEMS_DIR))
    try:
        import problems
        return problems
    except Exception as e:
        print(f"каталог проблем недоступен: {e}")
        sys.exit(1)


def print_note(rec):
    status = rec.get("status", "?")
    mark = (" [нерешённое ранее]" if status == "reported"
            else " [resolved]" if status == "resolved" else "")
    print(f"- {rec.get('id')} | {rec.get('ts')} | "
          f"{rec.get('source')}[{rec.get('severity')}] x{rec.get('count', 1)}{mark}")
    print(f"    {clip(rec.get('title', ''), 120)}")
    detail = rec.get("detail", "")
    if detail:
        for ln in clip(detail, 400).splitlines()[:4]:
            print(f"      {ln}")


def cmd_problems(args):
    p = load_problems()
    rows = p.list_all() if args.all_ else p.list_open()
    if not rows:
        print("проблем нет")
        return
    total = len(rows)
    print(f"записок в истории: {total}" if args.all_
          else f"открытых (new+reported): {total}")
    for rec in rows:
        print_note(rec)


def cmd_report(args):
    p = load_problems()
    ok = p.mark_reported(args.ref)
    print("reported" if ok else "нет записки / уже reported")


def cmd_close(args):
    p = load_problems()
    ok = p.close(args.ref, by="agent", note=args.note or "")
    print("closed" if ok else "нет записки / уже resolved")


def cmd_check(_args):
    section_scheduler()
    section_runs()
    section_indexes()
    section_brightdata()
    section_system()
    section_git()


def main():
    ap = argparse.ArgumentParser(
        description="health: проблемы автоматов и диагностика воркспейса")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("problems", help="незакрытые записки; --all — история")
    pr.add_argument("--all", action="store_true", dest="all_")

    rp = sub.add_parser("report", help="new -> reported")
    rp.add_argument("ref")

    cl = sub.add_parser("close", help="закрыть записку (resolved, by=agent)")
    cl.add_argument("ref")
    cl.add_argument("--note", default="")

    sub.add_parser("check", help="полная диагностика воркспейса")

    args = ap.parse_args()
    if args.cmd == "problems":
        cmd_problems(args)
    elif args.cmd == "report":
        cmd_report(args)
    elif args.cmd == "close":
        cmd_close(args)
    else:
        cmd_check(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

