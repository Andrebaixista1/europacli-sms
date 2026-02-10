#!/usr/bin/env python3
try:
    import curses
except Exception:
    print("Erro: curses nao disponivel. No Windows, instale: pip install windows-curses")
    raise SystemExit(1)
import json
import locale
import os
import re
import subprocess
import time
import random
from datetime import datetime
from datetime import timedelta
from glob import glob
import csv
import shutil
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
GAMMU_RC_PATH = os.path.join(BASE_DIR, "gammurc")
LOG_PATH = os.path.join(BASE_DIR, "sms_cli.log")
HISTORY_PATH = os.path.join(BASE_DIR, "sms_history.jsonl")
HISTORY_RETENTION_DAYS = 7
VERSION = "linux-mint-artemis1"
IS_WINDOWS = os.name == "nt"

DEFAULT_CONFIG = {
    "country_prefix": "55",
    "flash": False,
    "connection": "at",
    "selected_devices": [],
    "send_delay_sec": 1.0,
    "random_delay_enabled": True,
    "random_delay_min_sec": 10.0,
    "random_delay_max_sec": 30.0,
    "validate_modems": True,
    "read_numbers": False,
    "last_csv_path": "",
    "init_at_commands": "AT+ZCDRUN=8",
    "init_at_baud": 115200,
    "auto_activate_on_start": True,
    "keepalive_enabled": True,
    "keepalive_interval_sec": 60,
    "keepalive_commands": "AT",
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data or {})
        changed = False
        if not str(cfg.get("init_at_commands", "")).strip():
            cfg["init_at_commands"] = DEFAULT_CONFIG["init_at_commands"]
            changed = True
        if "auto_activate_on_start" not in cfg:
            cfg["auto_activate_on_start"] = DEFAULT_CONFIG["auto_activate_on_start"]
            changed = True
        if not cfg.get("init_at_baud"):
            cfg["init_at_baud"] = DEFAULT_CONFIG["init_at_baud"]
            changed = True
        if "keepalive_enabled" not in cfg:
            cfg["keepalive_enabled"] = DEFAULT_CONFIG["keepalive_enabled"]
            changed = True
        if "keepalive_interval_sec" not in cfg:
            cfg["keepalive_interval_sec"] = DEFAULT_CONFIG["keepalive_interval_sec"]
            changed = True
        if not str(cfg.get("keepalive_commands", "")).strip():
            cfg["keepalive_commands"] = DEFAULT_CONFIG["keepalive_commands"]
            changed = True
        if "random_delay_enabled" not in cfg:
            cfg["random_delay_enabled"] = DEFAULT_CONFIG["random_delay_enabled"]
            changed = True
        if "random_delay_min_sec" not in cfg:
            cfg["random_delay_min_sec"] = DEFAULT_CONFIG["random_delay_min_sec"]
            changed = True
        if "random_delay_max_sec" not in cfg:
            cfg["random_delay_max_sec"] = DEFAULT_CONFIG["random_delay_max_sec"]
            changed = True
        try:
            mn = float(cfg.get("random_delay_min_sec", 10.0))
            mx = float(cfg.get("random_delay_max_sec", 30.0))
            if mn < 0:
                mn = 0.0
            if mx < mn:
                mx = mn
            cfg["random_delay_min_sec"] = mn
            cfg["random_delay_max_sec"] = mx
        except Exception:
            cfg["random_delay_min_sec"] = DEFAULT_CONFIG["random_delay_min_sec"]
            cfg["random_delay_max_sec"] = DEFAULT_CONFIG["random_delay_max_sec"]
            changed = True
        if changed:
            save_config(cfg)
        return cfg
    except Exception:
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def parse_at_commands(value):
    if not value:
        return []
    parts = re.split(r"[;\n]+", str(value))
    return [p.strip() for p in parts if p and p.strip()]

def summarize_at_commands(value, max_len=40):
    cmds = parse_at_commands(value)
    if not cmds:
        return "vazio"
    text = "; ".join(cmds)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."

def run_at_commands(dev, commands, baud=115200, timeout=1.0):
    cmds = []
    for cmd in commands or []:
        if not cmd:
            continue
        line = str(cmd).strip()
        if not line:
            continue
        if not line.endswith("\r") and not line.endswith("\n"):
            line += "\r"
        cmds.append(line)
    if not cmds:
        return True, "sem comandos"

    serial = None
    try:
        import serial as _serial  # type: ignore
        serial = _serial
    except Exception:
        serial = None

    if serial is not None:
        try:
            with serial.Serial(
                dev,
                baudrate=int(baud),
                timeout=timeout,
                write_timeout=timeout,
            ) as ser:
                for line in cmds:
                    ser.write(line.encode("ascii", "ignore"))
                    ser.flush()
                    time.sleep(0.2)
                time.sleep(0.2)
                resp = b""
                try:
                    if ser.in_waiting:
                        resp = ser.read(ser.in_waiting)
                except Exception:
                    resp = b""
            return True, (resp.decode("ascii", "ignore") or "").strip()
        except Exception as e:
            return False, str(e)

    if IS_WINDOWS:
        return False, "pyserial nao instalado"

    try:
        subprocess.run(
            ["stty", "-F", dev, str(int(baud)), "raw", "-echo"],
            capture_output=True,
            text=True,
        )
        fd = os.open(dev, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        with os.fdopen(fd, "wb", buffering=0) as f:
            for line in cmds:
                f.write(line.encode("ascii", "ignore"))
                f.flush()
                time.sleep(0.2)
        return True, ""
    except Exception as e:
        return False, str(e)

def device_real(dev):
    if IS_WINDOWS:
        # COM ports não precisam de canonicalização; manter simples evita caminhos estranhos
        return str(dev).upper()
    try:
        return os.path.realpath(dev)
    except Exception:
        return dev


def resolve_selected_devices(devices, selected_cfg):
    real_map = {device_real(d): d for d in devices}
    selected = []
    for cfg_dev in selected_cfg:
        real = device_real(cfg_dev)
        if real in real_map:
            selected.append(real_map[real])
    return selected

def list_windows_ports():
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    def _com_number(dev):
        m = re.search(r"COM(\\d+)", str(dev).upper())
        return int(m.group(1)) if m else 10_000

    def _pref_score(desc):
        d = (desc or "").lower()
        if "at interface" in d or "pc ui" in d:
            return 0
        if "modem" in d:
            return 1
        if "diag" in d:
            return 3
        return 2

    ports_by_key = {}
    for p in list_ports.comports():
        if not p.device:
            continue
        loc = getattr(p, "location", None) or ""
        loc_base = loc.rsplit(".", 1)[0] if "." in loc else loc
        key = (
            getattr(p, "serial_number", None)
            or loc_base
            or f"{getattr(p, 'vid', None)}:{getattr(p, 'pid', None)}"
        )
        score = _pref_score(getattr(p, "description", ""))
        existing = ports_by_key.get(key)
        if existing is None or score < existing[0] or (
            score == existing[0] and _com_number(p.device) < _com_number(existing[1])
        ):
            ports_by_key[key] = (score, p.device)
    ports = [dev for _, dev in sorted(ports_by_key.values(), key=lambda t: _com_number(t[1]))]
    return ports


def _iface_rank(dev):
    m = re.search(r"-if(\d+)-port", dev)
    if not m:
        return (1, dev)
    try:
        return (0, int(m.group(1)))
    except Exception:
        return (0, dev)


def scan_devices(prefer_devices=None):
    if IS_WINDOWS:
        return list_windows_ports()
    prefer_set = set(prefer_devices or [])
    by_path = sorted(glob("/dev/serial/by-path/*"))
    if by_path:
        # Prefer non-usbv2 entries to avoid duplicates.
        filtered = [d for d in by_path if "usbv2-" not in d]
        if not filtered:
            filtered = by_path
        groups = {}
        for dev in filtered:
            m = re.match(r"^(.*):1\.\d+-port\d+$", dev)
            key = m.group(1) if m else dev
            groups.setdefault(key, []).append(dev)
        devices = []
        seen_real = set()
        for key, items in sorted(groups.items()):
            preferred = [d for d in items if d in prefer_set]
            if preferred:
                chosen = sorted(preferred, key=_iface_rank)[0]
            else:
                chosen = sorted(items, key=_iface_rank)[0]
            real = device_real(chosen)
            if real in seen_real:
                continue
            seen_real.add(real)
            devices.append(chosen)
        return devices

    patterns = [
        "/dev/serial/by-id/*",
        "/dev/ttyUSB*",
        "/dev/ttyACM*",
    ]
    candidates = []
    for p in patterns:
        candidates.extend(sorted(glob(p)))
    by_id = [d for d in candidates if d.startswith("/dev/serial/by-id/")]
    others = [d for d in candidates if d not in by_id]
    groups = {}
    for dev in by_id:
        m = re.match(r"^(.*)-if\d+-port\d+$", dev)
        key = m.group(1) if m else dev
        groups.setdefault(key, []).append(dev)
    devices = []
    seen_real = set()
    for key, items in sorted(groups.items()):
        preferred = [d for d in items if d in prefer_set]
        if preferred:
            chosen = sorted(preferred, key=_iface_rank)[0]
        else:
            chosen = sorted(items, key=_iface_rank)[0]
        real = device_real(chosen)
        if real in seen_real:
            continue
        seen_real.add(real)
        devices.append(chosen)
    for dev in sorted(others):
        real = device_real(dev)
        if real in seen_real:
            continue
        seen_real.add(real)
        devices.append(dev)
    return devices


def write_gammu_config(devices, connection):
    lines = []
    for i, dev in enumerate(devices, start=1):
        section = f"gammu{i}"
        lines.append(f"[{section}]")
        lines.append(f"port = {dev}")
        lines.append(f"connection = {connection}")
        lines.append("")
    with open(GAMMU_RC_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_temp_gammu_config(dev, connection):
    section = "gammu1"
    lines = [
        f"[{section}]",
        f"port = {dev}",
        f"connection = {connection}",
        "",
    ]
    path = os.path.join(BASE_DIR, "gammurc_check")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, "1"


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def load_history():
    records = []
    if not os.path.exists(HISTORY_PATH):
        return records
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                records.append(rec)
    except Exception:
        return []
    return records


def prune_history(records=None, max_days=HISTORY_RETENTION_DAYS):
    if records is None:
        records = load_history()
    cutoff = datetime.now() - timedelta(days=max_days)
    kept = []
    for rec in records:
        ts = parse_ts(rec.get("ts"))
        if ts and ts >= cutoff:
            kept.append(rec)
    tmp = HISTORY_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            for rec in kept:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp, HISTORY_PATH)
    except Exception:
        return kept
    return kept


def append_history(record):
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        return


def is_valid_modem(dev, connection):
    gbin = gammu_bin()
    if not gbin:
        return False
    try:
        cfg, section = write_temp_gammu_config(dev, connection)
        cmd = [gbin, "-c", cfg, "-s", section, "identify"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return proc.returncode == 0
    except Exception:
        return False


def get_own_number(dev, connection):
    gbin = gammu_bin()
    if not gbin:
        return None
    try:
        cfg, section = write_temp_gammu_config(dev, connection)
        cmd = [gbin, "-c", cfg, "-s", section, "getmemory", "ON", "1", "20", "-nonempty"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if proc.returncode != 0:
            return None
        for line in (proc.stdout or "").splitlines():
            if line.lower().startswith("number"):
                parts = line.split(":", 1)
                if len(parts) != 2:
                    continue
                raw = parts[1].strip()
                val = re.sub(r"[^\d+]", "", raw)
                if val.strip("+"):
                    return val
        return None
    except Exception:
        return None


def scan_devices_with_status(connection, validate=True, read_numbers=False, prefer_devices=None):
    devices = scan_devices(prefer_devices=prefer_devices)
    status = {}
    numbers = {}
    for dev in devices:
        if not validate:
            status[dev] = "?"
            numbers[dev] = "-"
            continue
        ok = is_valid_modem(dev, connection)
        status[dev] = "OK" if ok else "FAIL"
        if read_numbers and ok:
            numbers[dev] = get_own_number(dev, connection) or "-"
        else:
            numbers[dev] = "-"
    return devices, status, numbers


def log_event(message):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def format_number(raw, prefix):
    digits = re.sub(r"\D+", "", raw or "")
    if not digits:
        return ""
    if prefix:
        if digits.startswith(prefix) and len(digits) >= len(prefix) + 8:
            return "+" + digits
        return "+" + prefix + digits
    return digits


def parse_numbers(text, prefix):
    tokens = re.split(r"[^0-9]+", text or "")
    seen = set()
    numbers = []
    for t in tokens:
        if not t:
            continue
        if len(t) < 8:
            continue
        num = format_number(t, prefix)
        if not num or num in seen:
            continue
        seen.add(num)
        numbers.append({"number": num, "name": ""})
    return numbers


def _normalize_text(value):
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value))


_GAMMU_BIN = None


def gammu_bin():
    """Resolve o caminho do executavel do Gammu (cacheado)."""
    global _GAMMU_BIN
    if _GAMMU_BIN is not None:
        return _GAMMU_BIN or None
    candidates = [shutil.which("gammu")]
    if IS_WINDOWS:
        pf = os.environ.get("ProgramFiles", r"C:\\Program Files")
        pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")
        patterns = [
            os.path.join(pf, "Gammu*", "bin", "gammu.exe"),
            os.path.join(pfx86, "Gammu*", "bin", "gammu.exe"),
        ]
        for pattern in patterns:
            candidates.extend(glob(pattern))
    for path in candidates:
        if path and os.path.exists(path):
            _GAMMU_BIN = path
            return path
    _GAMMU_BIN = ""
    return None


def parse_csv_numbers(path, prefix):
    encodings = ["utf-8-sig", "latin1", "cp1252", "utf-16", "utf-16le", "utf-16be"]
    delimiters = ";,"
    for enc in encodings:
        numbers = []
        seen = set()
        try:
            with open(path, "r", encoding=enc) as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=delimiters)
                except Exception:
                    dialect = csv.excel
                reader = csv.reader(f, dialect=dialect)
                for row in reader:
                    if not row:
                        continue
                    num = row[0] if len(row) > 0 else ""
                    name = _normalize_text(row[1] if len(row) > 1 else "")
                    if not num:
                        continue
                    formatted = format_number(str(num), prefix)
                    if formatted and formatted not in seen:
                        seen.add(formatted)
                        numbers.append(
                            {
                                "number": formatted,
                                "name": str(name).strip() if name is not None else "",
                            }
                        )
            if numbers:
                return numbers
        except Exception:
            continue
    return []


def send_sms(section, number, message, flash):
    gbin = gammu_bin()
    if not gbin:
        return False, "", "Gammu nao encontrado. Instale e deixe 'gammu' no PATH (ex.: winget install Gammu.Gammu)."
    cmd = [
        gbin,
        "-c",
        GAMMU_RC_PATH,
        "-s",
        section,
        "sendsms",
        "TEXT",
        number,
        "-textutf8",
        message,
    ]
    if flash:
        cmd.append("-flash")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ok = proc.returncode == 0
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return ok, out, err


def send_numbers(
    recipients,
    sections,
    devices,
    message,
    flash,
    delay_sec,
    random_delay_enabled=False,
    random_delay_min_sec=0.0,
    random_delay_max_sec=0.0,
    progress_cb=None,
    status_list=None,
    recipient_modems=None,
    modem_status=None,
    modem_labels=None,
    report_records=None,
):
    FAIL_LIMIT = 10
    ok_count = 0
    failed = []
    total_modems = len(sections)
    stop_index = len(recipients)
    for i, rec in enumerate(recipients):
        num = rec.get("number", "")
        name = _normalize_text(rec.get("name", ""))
        if status_list is not None:
            status_list[i] = "ENVIANDO"
        ok = False
        start_idx = i % total_modems if total_modems else 0
        current_modem_label = None
        for attempt in range(max(total_modems, 1)):
            idx = (start_idx + attempt) % total_modems if total_modems else 0
            section = sections[idx]
            device = devices[idx]
            current_modem_label = modem_labels.get(device, device) if modem_labels else device
            if recipient_modems is not None:
                recipient_modems[i] = current_modem_label
            if modem_status is not None:
                modem_status[device] = "ENVIANDO"
            if progress_cb:
                progress_cb(
                    i,
                    len(recipients),
                    ok_count,
                    len(failed),
                    num,
                    status_list,
                    i,
                    current_modem_label,
                )
            msg = message
            if "<" in msg and ">" in msg:
                msg = re.sub(r"<\s*name\s*>", name or "", msg, flags=re.IGNORECASE)
            ok_try, out, err = send_sms(section, num, msg, flash)
            if ok_try:
                ok_count += 1
                log_event(f"OK {num} via {section}")
            else:
                log_event(f"FAIL {num} via {section} | {err or out}")
            if modem_status is not None:
                modem_status[device] = "OK" if ok_try else "FAIL"
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "name": name,
                "number": num,
                "message": msg,
                "flash": bool(flash),
                "status": "OK" if ok_try else "FAIL",
                "device": device,
                "section": section,
                "response": out or err,
            }
            append_history(record)
            if report_records is not None:
                report_records.append(record)
            if ok_try:
                ok = True
                break
        if not ok:
            failed.append(num)
        if status_list is not None:
            status_list[i] = "OK" if ok else "FAIL"
        if progress_cb:
            progress_cb(
                i + 1,
                len(recipients),
                ok_count,
                len(failed),
                num,
                status_list,
                i,
                current_modem_label,
            )
        if i < len(recipients) - 1:
            if random_delay_enabled:
                try:
                    mn = float(random_delay_min_sec)
                    mx = float(random_delay_max_sec)
                except Exception:
                    mn, mx = 0.0, 0.0
                if mn < 0:
                    mn = 0.0
                if mx < mn:
                    mx = mn
                if mx > 0:
                    time.sleep(random.uniform(mn, mx))
            elif delay_sec > 0:
                time.sleep(delay_sec)
        if len(failed) >= FAIL_LIMIT:
            stop_index = i + 1
            break

    # Se parou por limite de falhas, registra o restante como FAIL (skipped)
    if stop_index < len(recipients):
        for j in range(stop_index, len(recipients)):
            rec = recipients[j]
            num = rec.get("number", "")
            name = _normalize_text(rec.get("name", ""))
            if status_list is not None:
                status_list[j] = "FAIL"
            if recipient_modems is not None:
                recipient_modems[j] = "SKIPPED"
            record = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "name": name,
                "number": num,
                "message": message,
                "flash": bool(flash),
                "status": "FAIL",
                "device": "SKIPPED_FAIL_LIMIT",
                "section": "-",
                "response": "Skipped: fail limit reached",
            }
            append_history(record)
            if report_records is not None:
                report_records.append(record)
            if progress_cb:
                progress_cb(
                    j + 1,
                    len(recipients),
                    ok_count,
                    len(failed),
                    num,
                    status_list,
                    j,
                    "SKIPPED",
                )
    prune_history()
    return ok_count, failed


def draw_header(stdscr, title):
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(0, 2, title, curses.A_BOLD)
    stdscr.hline(1, 0, "-", w)


def format_recipient_label(rec):
    num = rec.get("number", "") if isinstance(rec, dict) else str(rec or "")
    name = rec.get("name", "") if isinstance(rec, dict) else ""
    name = str(name).strip()
    if name:
        return f"{num} ({name})"
    return num


def draw_progress(
    stdscr,
    title,
    sent,
    total,
    ok_count,
    fail_count,
    current_num,
    recipients=None,
    status_list=None,
    current_idx=None,
    current_modem=None,
    recipient_modems=None,
    modem_status=None,
    modem_order=None,
    modem_labels=None,
    delay_info=None,
):
    draw_header(stdscr, title)
    h, w = stdscr.getmaxyx()
    if total <= 0:
        total = 1
    stdscr.addstr(3, 2, f"Progresso: {sent}/{total}")
    bar_width = max(10, w - 10)
    filled = int(bar_width * sent / total)
    if filled > bar_width:
        filled = bar_width
    bar = "[" + ("#" * filled) + ("-" * (bar_width - filled)) + "]"
    stdscr.addstr(4, 2, bar[: max(w - 4, 0)])
    stdscr.addstr(6, 2, f"OK: {ok_count}  FAIL: {fail_count}")
    if delay_info:
        stdscr.addstr(7, 2, f"Delay: {delay_info}"[: max(w - 4, 0)])
    if modem_order and modem_status and modem_labels:
        parts = []
        for dev in modem_order:
            label = modem_labels.get(dev, dev)
            status = modem_status.get(dev, "?")
            parts.append(f"{label}:{status}")
        line = "Modems: " + "  ".join(parts)
        stdscr.addstr(8, 2, line[: max(w - 4, 0)])
    if current_num:
        if current_modem:
            stdscr.addstr(9, 2, f"Atual: {current_num} via {current_modem}")
        else:
            stdscr.addstr(9, 2, f"Atual: {current_num}")
    list_start = 12
    if recipients and status_list:
        stdscr.addstr(list_start - 1, 2, "Fila:")
        available = max(0, h - (list_start + 1))
        if available > 0:
            total_items = len(recipients)
            cur = current_idx if current_idx is not None else 0
            start = max(0, min(cur - available + 1, total_items - available))
            end = min(total_items, start + available)
            y = list_start
            for idx in range(start, end):
                rec_label = format_recipient_label(recipients[idx])
                status = status_list[idx]
                marker = ">" if idx == cur else " "
                modem_label = ""
                if recipient_modems:
                    modem_label = f" {recipient_modems[idx]}"
                line = f"{marker} {rec_label} [{status}]{modem_label}"
                stdscr.addstr(y, 2, line[: max(w - 4, 0)])
                y += 1
    stdscr.refresh()


def menu(stdscr, title, options, index=0, tick_fn=None, tick_interval_sec=0):
    curses.curs_set(0)
    last_tick = time.time()
    if tick_fn and tick_interval_sec > 0:
        stdscr.timeout(500)
    while True:
        draw_header(stdscr, title)
        h, w = stdscr.getmaxyx()
        for i, opt in enumerate(options):
            y = 3 + i
            if y >= h - 2:
                break
            if i == index:
                stdscr.addstr(y, 4, opt, curses.A_REVERSE)
            else:
                stdscr.addstr(y, 4, opt)
        stdscr.addstr(h - 2, 2, "Setas: navegar  Enter: selecionar  Q: voltar")
        stdscr.refresh()
        key = stdscr.getch()
        if key == -1:
            if tick_fn and tick_interval_sec > 0:
                now = time.time()
                if now - last_tick >= tick_interval_sec:
                    try:
                        tick_fn()
                    except Exception:
                        pass
                    last_tick = now
            continue
        if key in (ord("q"), ord("Q")):
            if tick_fn and tick_interval_sec > 0:
                stdscr.timeout(-1)
            return None
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(options)
        elif key in (10, 13, curses.KEY_ENTER):
            if tick_fn and tick_interval_sec > 0:
                stdscr.timeout(-1)
            return index


def checkbox_list(
    stdscr,
    title,
    items,
    checked,
    status=None,
    numbers=None,
    numbers_label="num",
    labels=None,
    rescan_fn=None,
    rescan_interval_sec=0,
):
    curses.curs_set(0)
    index = 0
    notice = ""
    last_scan = time.time()
    if rescan_fn and rescan_interval_sec > 0:
        stdscr.timeout(500)
    while True:
        draw_header(stdscr, title)
        h, w = stdscr.getmaxyx()
        if not items:
            stdscr.addstr(3, 4, "Nenhum modem detectado. Pressione R para rescan.")
        for i, it in enumerate(items):
            y = 3 + i
            if y >= h - 3:
                break
            mark = "[x]" if it in checked else "[ ]"
            label = labels.get(it, it) if labels else it
            st = ""
            if status is not None:
                st = status.get(it, "?")
                st = f" [{st}]"
            num = ""
            if numbers is not None:
                num = numbers.get(it, "-")
                num = f" {numbers_label}:{num}"
            line = f"{mark} {label}{st}{num}"
            if i == index:
                stdscr.addstr(y, 4, line, curses.A_REVERSE)
            else:
                stdscr.addstr(y, 4, line)
        if notice:
            stdscr.addstr(h - 3, 2, notice[: w - 4])
        if rescan_fn and rescan_interval_sec > 0:
            now = time.time()
            remaining = int(max(0, rescan_interval_sec - (now - last_scan)))
            stdscr.addstr(
                h - 2,
                2,
                f"Espaco: marcar  R: rescan  Auto: {rescan_interval_sec}s (em {remaining}s)  S: salvar  Q: voltar",
            )
        else:
            stdscr.addstr(h - 2, 2, "Espaco: marcar  R: rescan  S: salvar  Q: voltar")
        stdscr.refresh()
        key = stdscr.getch()
        if key == -1:
            if rescan_fn and rescan_interval_sec > 0:
                now = time.time()
                if now - last_scan >= rescan_interval_sec:
                    result = rescan_fn()
                    if isinstance(result, tuple) and len(result) == 4:
                        items, status, numbers, labels = result
                    else:
                        items, status, numbers = result
                        labels = None
                    checked.intersection_update(set(items))
                    last_scan = now
            continue
        if key in (ord("q"), ord("Q")):
            if rescan_fn and rescan_interval_sec > 0:
                stdscr.timeout(-1)
            return checked
        if key in (ord("s"), ord("S")):
            if rescan_fn and rescan_interval_sec > 0:
                stdscr.timeout(-1)
            return checked
        if key in (ord("r"), ord("R")):
            if rescan_fn:
                result = rescan_fn()
                if isinstance(result, tuple) and len(result) == 4:
                    items, status, numbers, labels = result
                else:
                    items, status, numbers = result
                    labels = None
                checked.intersection_update(set(items))
                last_scan = time.time()
                continue
            return "__RESCAN__"
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % max(len(items), 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % max(len(items), 1)
        elif key == ord(" ") and items:
            dev = items[index]
            if status is not None and status.get(dev) == "FAIL":
                notice = "Modem com FAIL bloqueado para selecao."
                continue
            notice = ""
            if dev in checked:
                checked.remove(dev)
            else:
                checked.add(dev)


def prompt_input(stdscr, title, prompt, initial="", replace_on_type=False):
    curses.curs_set(0)
    current = initial or ""
    touched = False
    while True:
        draw_header(stdscr, title)
        h, w = stdscr.getmaxyx()
        stdscr.addstr(3, 4, prompt)
        stdscr.addstr(5, 4, "> ")
        display = (current + "|")[: max(w - 8, 0)]
        stdscr.addstr(5, 6, display)
        stdscr.clrtoeol()
        stdscr.addstr(h - 2, 2, "Enter: ok  ESC: cancelar  Ctrl+U: limpar")
        stdscr.refresh()
        try:
            key = stdscr.get_wch()
        except Exception:
            key = stdscr.getch()
        if key in (10, 13, curses.KEY_ENTER, "\n", "\r"):
            return _normalize_text(current.strip())
        if key in (27, "\x1b", curses.KEY_EXIT):
            return ""
        if key in (21, "\x15"):
            current = ""
            touched = True
            continue
        if key in (
            curses.KEY_BACKSPACE,
            curses.KEY_DC,
            127,
            8,
            "\x7f",
            "\b",
        ):
            current = current[:-1]
            touched = True
            continue
        if isinstance(key, str) and key:
            if replace_on_type and not touched and initial:
                current = ""
            # aceita qualquer caractere imprimivel (exceto DEL)
            if ord(key) >= 32 and key != "\x7f":
                current += key
                touched = True


def choose_file_dialog():
    if IS_WINDOWS:
        path = None
        try:
            try:
                curses.def_prog_mode()
                curses.endwin()
            except Exception:
                pass
            try:
                import tkinter as tk  # type: ignore
                from tkinter import filedialog  # type: ignore
                root = tk.Tk()
                root.withdraw()
                path = filedialog.askopenfilename(
                    title="Selecione o CSV",
                    filetypes=[("Arquivos CSV", "*.csv"), ("Todos os arquivos", "*.*")],
                )
                root.destroy()
            except Exception:
                path = None
        finally:
            try:
                curses.reset_prog_mode()
                curses.curs_set(0)
                curses.doupdate()
            except Exception:
                pass
        if path:
            return path
    for cmd in (
        ["zenity", "--file-selection", "--title=Selecione o CSV"],
        ["kdialog", "--getopenfilename", ".", "*.csv"],
    ):
        try:
            try:
                curses.def_prog_mode()
                curses.endwin()
            except Exception:
                pass
            proc = subprocess.run(cmd, capture_output=True, text=True)
            try:
                curses.reset_prog_mode()
                curses.curs_set(0)
                curses.doupdate()
            except Exception:
                pass
            if proc.returncode == 0:
                path = (proc.stdout or "").strip()
                if path:
                    return path
        except FileNotFoundError:
            try:
                curses.reset_prog_mode()
                curses.curs_set(0)
                curses.doupdate()
            except Exception:
                pass
            continue
        except Exception:
            try:
                curses.reset_prog_mode()
                curses.curs_set(0)
                curses.doupdate()
            except Exception:
                pass
            continue
    return None


def multiline_input(stdscr, title, hint):
    curses.curs_set(1)
    lines = []
    current = ""
    while True:
        draw_header(stdscr, title)
        h, w = stdscr.getmaxyx()
        stdscr.addstr(3, 2, hint)
        y = 5
        for line in lines[-(h - 8):]:
            stdscr.addstr(y, 4, line[: w - 8])
            y += 1
        stdscr.addstr(y, 4, (current + "|")[: w - 8])
        stdscr.addstr(h - 2, 2, "F2: ok  ESC: cancelar  Enter: nova linha  Ctrl+V: colar")
        stdscr.refresh()
        key = stdscr.get_wch()
        if key in (27, "\x1b", curses.KEY_EXIT):
            return None
        if key == curses.KEY_F2:
            if current:
                lines.append(current)
            return "\n".join(lines).strip()
        if key in (10, 13, curses.KEY_ENTER, "\n", "\r"):
            lines.append(current)
            current = ""
            continue
        if key in (
            curses.KEY_BACKSPACE,
            curses.KEY_DC,
            127,
            8,
            "\x7f",
            "\b",
        ):
            current = current[:-1]
            continue
        if key in (21, "\x15"):
            current = ""
            continue
        if key in (22, "\x16"):
            paste = read_clipboard()
            if paste:
                parts = paste.splitlines()
                if parts:
                    current += parts[0]
                    for mid in parts[1:-1]:
                        lines.append(current)
                        current = mid
                    if len(parts) > 1:
                        lines.append(current)
                        current = parts[-1]
            continue
        if isinstance(key, str) and key:
            if ord(key) >= 32 and key != "\x7f":
                current += key


def message_screen(stdscr, title, lines, wait=True):
    curses.curs_set(0)
    draw_header(stdscr, title)
    h, w = stdscr.getmaxyx()
    y = 3
    for line in lines:
        if y >= h - 3:
            break
        stdscr.addstr(y, 2, line[: w - 4])
        y += 1
    if wait:
        stdscr.addstr(h - 2, 2, "Pressione qualquer tecla para voltar")
        stdscr.refresh()
        stdscr.getch()


def confirm_screen(stdscr, title, lines):
    curses.curs_set(0)
    while True:
        draw_header(stdscr, title)
        h, w = stdscr.getmaxyx()
        y = 3
        for line in lines:
            if y >= h - 3:
                break
            stdscr.addstr(y, 2, line[: w - 4])
            y += 1
        stdscr.addstr(h - 2, 2, "Enter: enviar  Q: cancelar")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return False
        if key in (10, 13, curses.KEY_ENTER):
            return True


def retry_screen(stdscr, title, lines):
    curses.curs_set(0)
    while True:
        draw_header(stdscr, title)
        h, w = stdscr.getmaxyx()
        y = 3
        for line in lines:
            if y >= h - 3:
                break
            stdscr.addstr(y, 2, line[: w - 4])
            y += 1
        stdscr.addstr(h - 2, 2, "R: reenviar falhas  Q: sair")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return False
        if key in (ord("r"), ord("R")):
            return True


def default_export_path(prefix):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(BASE_DIR, f"{prefix}_{ts}.csv")


def export_csv(path, headers, rows):
    try:
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)
        return True, ""
    except Exception as e:
        return False, str(e)


def read_clipboard():
    if IS_WINDOWS:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0:
                return (proc.stdout or "").replace("\r\n", "\n")
        except Exception:
            return ""
        return ""
    for cmd in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if proc.returncode == 0:
                return (proc.stdout or "").replace("\r\n", "\n")
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return ""


def view_log(stdscr):
    if not os.path.exists(LOG_PATH):
        message_screen(stdscr, "Log", ["Log vazio."])
        return
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    index = max(len(lines) - 200, 0)
    while True:
        draw_header(stdscr, "Log (ultimas linhas)")
        h, w = stdscr.getmaxyx()
        visible = lines[index : index + (h - 5)]
        y = 3
        for line in visible:
            stdscr.addstr(y, 2, line[: w - 4])
            y += 1
        stdscr.addstr(h - 2, 2, "Setas: rolar  Q: voltar")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key in (curses.KEY_UP, ord("k")):
            index = max(index - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = min(index + 1, max(len(lines) - 1, 0))

def help_screen(stdscr):
    lines = [
        f"Europa CLI - Sender SMS (versao {VERSION})",
        "",
        "Fluxo basico:",
        "1) Selecionar modems (use os que ficam OK)",
        "2) Compor e enviar (digitar/colar ou CSV)",
        "3) (Opcional) Ativar modems com comandos AT",
        "4) Reenviar do historico (selecionar numeros)",
        "5) Historico e Relatorio (F6 exporta CSV)",
        "",
        "Configuracoes:",
        "- Flash SMS: envia como flash se habilitado",
        "- Connection: use 'at' (recomendado)",
        "- Validar modems: usa gammu identify (mais lento)",
        "- Delay aleatorio: intervalo 10-30s entre envios",
        "- Mostrar numero do chip: consulta numero (pode travar em portas ruins)",
        "- Comandos AT: ex. AT+ZCDRUN=8 (seu modem pode exigir)",
        "- Auto ativar AT: envia os comandos ao iniciar o programa",
        "- Keepalive AT: envia AT periodicamente para evitar inatividade",
        "",
        "Dicas:",
        "- Se travar, desative Validar modems e Mostrar numero do chip",
        "- Prefira /dev/serial/by-id quando disponivel",
        "- No Windows, use portas COM (ex: COM3)",
        "- Mensagem pode usar <NAME> para nome do CSV",
        "",
        "Historico (7 dias):",
        f"- Arquivo: {HISTORY_PATH}",
        "- Campos: nome, telefone, mensagem, flash, status, device, ts",
        "",
        "API (historico):",
        "- Iniciar: python3 sms_api.py --host 0.0.0.0 --port 8081",
        "- GET /history",
        "- GET /history?since=YYYY-MM-DDTHH:MM:SS&limit=100",
        "- GET /health",
    ]
    message_screen(stdscr, "Ajuda", lines, wait=True)

def release_ports(selected_devices):
    if IS_WINDOWS:
        return []
    if not selected_devices:
        return []
    results = []
    for dev in selected_devices:
        cmd = ["fuser", "-k", dev]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        results.append((dev, proc.returncode, out, err))
    return results

def release_ports_screen(stdscr, cfg, devices):
    if IS_WINDOWS:
        message_screen(stdscr, "Liberar portas", ["Disponivel apenas no Linux."])
        return
    selected_cfg = cfg.get("selected_devices", [])
    selected = resolve_selected_devices(devices, selected_cfg)
    if not selected:
        message_screen(stdscr, "Liberar portas", ["Nenhum modem selecionado."])
        return
    lines = [
        "Isso vai encerrar processos usando as portas selecionadas.",
        f"Portas: {', '.join(selected)}",
        "",
        "Deseja continuar?",
    ]
    if not confirm_screen(stdscr, "Liberar portas", lines):
        return
    results = release_ports(selected)
    msg = []
    for dev, code, out, err in results:
        if code == 0:
            msg.append(f"OK {dev}")
            log_event(f"KILL OK {dev} | {out or err}")
        else:
            msg.append(f"FAIL {dev}")
            log_event(f"KILL FAIL {dev} | {err or out}")
    message_screen(stdscr, "Liberar portas", msg)

def history_candidates():
    records = load_history()
    if not records:
        return [], {}
    latest = {}
    order = []
    for rec in reversed(records):
        num = rec.get("number") or ""
        if not num or num in latest:
            continue
        latest[num] = rec
        order.append(num)
    return order, latest

def resend_from_history(stdscr, cfg, devices):
    numbers, meta = history_candidates()
    if not numbers:
        message_screen(stdscr, "Historico", ["Historico vazio."])
        return
    status_map = {}
    name_map = {}
    for num in numbers:
        rec = meta.get(num, {})
        status_map[num] = rec.get("status", "?")
        name = rec.get("name") or "-"
        name_map[num] = name

    checked = set()
    result = checkbox_list(
        stdscr,
        "Historico (selecione numeros)",
        numbers,
        checked,
        status=status_map,
        numbers=name_map,
        numbers_label="nome",
    )
    if not result:
        return
    selected = sorted(result, key=lambda n: numbers.index(n))
    recipients = [{"number": n, "name": meta.get(n, {}).get("name", "")} for n in selected]
    message = multiline_input(
        stdscr,
        "Mensagem",
        "Digite a mensagem (F2 para finalizar). Use <NAME> ou <Name> para nome.",
    )
    if message is None:
        return
    if not recipients:
        message_screen(stdscr, "Enviar", ["Nenhum numero selecionado."])
        return
    flash_choice = menu(stdscr, "Tipo de envio", ["Flash SMS", "SMS normal", "Cancelar"])
    if flash_choice is None or flash_choice == 2:
        return
    flash = flash_choice == 0
    send_flow(stdscr, cfg, devices, recipients, message, flash)

def history_screen(stdscr):
    records = load_history()
    if not records:
        message_screen(stdscr, "Historico", ["Historico vazio."])
        return
    lines = []
    for rec in records:
        ts = rec.get("ts", "")
        status = rec.get("status", "")
        num = rec.get("number", "")
        dev = rec.get("device", "")
        msg = rec.get("message", "")
        msg = msg.replace("\n", " ")[:30]
        line = f"{ts} {status} {num} {dev} {msg}"
        lines.append(line)
    index = max(len(lines) - 200, 0)
    while True:
        draw_header(stdscr, "Historico (ultimos registros)")
        h, w = stdscr.getmaxyx()
        visible = lines[index : index + (h - 5)]
        y = 3
        for line in visible:
            stdscr.addstr(y, 2, line[: w - 4])
            y += 1
        stdscr.addstr(h - 2, 2, "Setas: rolar  F6: exportar CSV  Q: voltar")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key == curses.KEY_F6:
            default_path = default_export_path("sms_history")
            path = prompt_input(
                stdscr,
                "Exportar CSV",
                "Caminho do CSV:",
                default_path,
                replace_on_type=True,
            )
            if not path:
                continue
            headers = ["ts", "name", "number", "message", "flash", "status", "device", "section", "response"]
            rows = []
            for rec in records:
                rows.append(
                    [
                        rec.get("ts", ""),
                        rec.get("name", ""),
                        rec.get("number", ""),
                        rec.get("message", ""),
                        rec.get("flash", ""),
                        rec.get("status", ""),
                        rec.get("device", ""),
                        rec.get("section", ""),
                        rec.get("response", ""),
                    ]
                )
            ok, err = export_csv(path, headers, rows)
            if ok:
                message_screen(stdscr, "Exportar CSV", [f"Salvo em: {path}"])
            else:
                message_screen(stdscr, "Exportar CSV", [f"Erro: {err}"])
            continue
        if key in (curses.KEY_UP, ord("k")):
            index = max(index - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = min(index + 1, max(len(lines) - 1, 0))


def build_report(records):
    stats = {}
    for rec in records:
        dev = rec.get("device", "") or "-"
        status = (rec.get("status", "") or "").upper()
        entry = stats.setdefault(dev, {"total": 0, "ok": 0, "fail": 0, "last_ts": ""})
        entry["total"] += 1
        if status == "OK":
            entry["ok"] += 1
        elif status == "FAIL":
            entry["fail"] += 1
        ts = rec.get("ts", "")
        if ts and (not entry["last_ts"] or ts > entry["last_ts"]):
            entry["last_ts"] = ts
    return stats


def build_report_from_records(records):
    stats = build_report(records)
    lines = []
    for dev, entry in sorted(stats.items()):
        line = f"{dev} | total:{entry['total']} ok:{entry['ok']} fail:{entry['fail']}"
        lines.append(line)
    return lines, stats


def report_screen(stdscr):
    records = load_history()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not records:
        message_screen(stdscr, "Relatorio", ["Historico vazio."])
        return
    stats = build_report(records)
    lines = [
        f"Relatorio por modem - {now}",
        f"Total registros: {len(records)}",
        "",
    ]
    for dev, entry in sorted(stats.items()):
        line = f"{dev} | total:{entry['total']} ok:{entry['ok']} fail:{entry['fail']} | ultimo:{entry['last_ts']}"
        lines.append(line)
    index = 0
    while True:
        draw_header(stdscr, "Relatorio")
        h, w = stdscr.getmaxyx()
        visible = lines[index : index + (h - 5)]
        y = 3
        for line in visible:
            stdscr.addstr(y, 2, line[: w - 4])
            y += 1
        stdscr.addstr(h - 2, 2, "Setas: rolar  F6: exportar CSV  Q: voltar")
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), ord("Q")):
            return
        if key == curses.KEY_F6:
            default_path = default_export_path("sms_relatorio")
            path = prompt_input(
                stdscr,
                "Exportar CSV",
                "Caminho do CSV:",
                default_path,
                replace_on_type=True,
            )
            if not path:
                continue
            headers = ["gerado_em", "modem", "total", "ok", "fail", "ultimo_ts"]
            rows = []
            for dev, entry in sorted(stats.items()):
                rows.append(
                    [
                        now,
                        dev,
                        entry["total"],
                        entry["ok"],
                        entry["fail"],
                        entry["last_ts"],
                    ]
                )
            ok, err = export_csv(path, headers, rows)
            if ok:
                message_screen(stdscr, "Exportar CSV", [f"Salvo em: {path}"])
            else:
                message_screen(stdscr, "Exportar CSV", [f"Erro: {err}"])
            continue
        if key in (curses.KEY_UP, ord("k")):
            index = max(index - 1, 0)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = min(index + 1, max(len(lines) - 1, 0))

def activate_modems(devices, commands, baud):
    results = []
    for dev in devices:
        ok, resp = run_at_commands(dev, commands, baud=baud)
        results.append((dev, ok, resp))
        status = "OK" if ok else "FAIL"
        log_event(f"AT {status} {dev} | {resp}")
    return results

def activate_modems_screen(stdscr, cfg, devices):
    commands = parse_at_commands(cfg.get("init_at_commands", ""))
    if not commands:
        message_screen(
            stdscr,
            "Ativar modems",
            [
                "Nenhum comando AT configurado.",
                "Configure em Configuracoes -> Comandos AT.",
            ],
        )
        return
    selected_cfg = cfg.get("selected_devices", [])
    selected = resolve_selected_devices(devices, selected_cfg)
    if not selected:
        message_screen(stdscr, "Ativar modems", ["Nenhum modem selecionado."])
        return
    lines = [
        "Isso vai enviar comandos AT para os modems selecionados.",
        f"Comandos: {'; '.join(commands)}",
        f"Baud: {cfg.get('init_at_baud', 115200)}",
        "",
        "Deseja continuar?",
    ]
    if not confirm_screen(stdscr, "Ativar modems", lines):
        return
    results = activate_modems(selected, commands, cfg.get("init_at_baud", 115200))
    msg = []
    for dev, ok, resp in results:
        if ok:
            msg.append(f"OK {dev}")
        else:
            msg.append(f"FAIL {dev} | {resp}")
    message_screen(stdscr, "Ativar modems", msg)

def keepalive_modems(devices, commands, baud):
    results = []
    for dev in devices:
        ok, resp = run_at_commands(dev, commands, baud=baud, timeout=0.5)
        results.append((dev, ok, resp))
        status = "OK" if ok else "FAIL"
        log_event(f"KEEPALIVE {status} {dev} | {resp}")
    return results

def auto_activate_devices(cfg, activated_real):
    if not cfg.get("auto_activate_on_start", False):
        return
    commands = parse_at_commands(cfg.get("init_at_commands", ""))
    if not commands:
        return
    detected = scan_devices()
    if not detected:
        return
    current_real = {device_real(d) for d in detected}
    activated_real.intersection_update(current_real)
    to_activate = [d for d in detected if device_real(d) not in activated_real]
    if not to_activate:
        return
    activate_modems(to_activate, commands, cfg.get("init_at_baud", 115200))
    activated_real.update({device_real(d) for d in to_activate})

def keepalive_devices(cfg, last_ts):
    if not cfg.get("keepalive_enabled", False):
        return last_ts
    try:
        interval = float(cfg.get("keepalive_interval_sec", 60))
    except Exception:
        interval = 60.0
    if interval <= 0:
        return last_ts
    now = time.monotonic()
    if last_ts and (now - last_ts) < interval:
        return last_ts
    commands = parse_at_commands(cfg.get("keepalive_commands", "AT"))
    if not commands:
        return now
    detected = scan_devices()
    if not detected:
        return now
    keepalive_modems(detected, commands, cfg.get("init_at_baud", 115200))
    return now


def build_modem_labels(devices):
    labels = {}
    for idx, dev in enumerate(devices, start=1):
        labels[dev] = f"Modem {idx}"
    return labels

def compose_and_send(stdscr, cfg, devices):
    selected_cfg = cfg.get("selected_devices", [])
    selected = resolve_selected_devices(devices, selected_cfg)
    if not selected:
        message_screen(stdscr, "Enviar", ["Nenhum modem selecionado."])
        return
    source = menu(stdscr, "Origem dos numeros", ["Digitar/colar", "Importar CSV", "Voltar"])
    if source is None or source == 2:
        return
    if source == 0:
        numbers_text = multiline_input(
            stdscr,
            "Numeros",
            "Cole os numeros (sem +55). Separe por espaco, virgula ou nova linha.",
        )
        if numbers_text is None:
            return
        recipients = parse_numbers(numbers_text, cfg.get("country_prefix", ""))
    else:
        path = None
        try:
            path = choose_file_dialog()
        except Exception:
            path = None
        if not path:
            path = prompt_input(
                stdscr,
                "CSV",
                "Caminho do CSV (coluna 1=telefone, coluna 2=nome):",
                cfg.get("last_csv_path", ""),
            )
        if not path:
            return
        recipients = parse_csv_numbers(path, cfg.get("country_prefix", ""))
        if not recipients:
            message_screen(stdscr, "CSV", ["Nenhum numero valido encontrado no arquivo."])
            return
        if recipients:
            cfg["last_csv_path"] = path
            save_config(cfg)
        message_screen(stdscr, "CSV", [f"Numeros carregados: {len(recipients)}"])
    message = multiline_input(
        stdscr,
        "Mensagem",
        "Digite a mensagem (F2 para finalizar). Use <NAME> ou <Name> para nome.",
    )
    if message is None:
        return
    send_flow(stdscr, cfg, devices, recipients, message, cfg.get("flash", False))


def send_flow(stdscr, cfg, devices, recipients, message, flash):
    selected_cfg = cfg.get("selected_devices", [])
    selected = resolve_selected_devices(devices, selected_cfg)
    if not selected:
        message_screen(stdscr, "Enviar", ["Nenhum modem selecionado."])
        return
    if not recipients:
        message_screen(stdscr, "Enviar", ["Nenhum numero valido informado."])
        return

    message = _normalize_text(message)

    # Garantir AT antes de cada envio, se houver comandos configurados.
    commands = parse_at_commands(cfg.get("init_at_commands", ""))
    if commands:
        activate_modems(selected, commands, cfg.get("init_at_baud", 115200))

    gbin = gammu_bin()
    if not gbin:
        message_screen(
            stdscr,
            "Gammu",
            [
                "Gammu nao encontrado.",
                "Instale e deixe 'gammu' no PATH.",
                "Sugestao: winget install Gammu.Gammu",
            ],
        )
        return

    write_gammu_config(selected, cfg.get("connection", "at"))
    sections = [str(i + 1) for i in range(len(selected))]

    lines = [
        f"Modems selecionados: {len(selected)}",
        f"Numeros unicos: {len(recipients)}",
        f"Flash: {'sim' if flash else 'nao'}",
        "",
        "Confirma o envio?",
    ]
    if not confirm_screen(stdscr, "Confirmar", lines):
        return

    delay = float(cfg.get("send_delay_sec", 0))
    random_delay_enabled = bool(cfg.get("random_delay_enabled", False))
    random_delay_min_sec = cfg.get("random_delay_min_sec", 10.0)
    random_delay_max_sec = cfg.get("random_delay_max_sec", 30.0)
    if random_delay_enabled:
        delay_info = f"{random_delay_min_sec}-{random_delay_max_sec}s (aleatorio)"
    else:
        delay_info = f"{delay}s"
    attempt = 1
    pending = recipients
    total_ok = 0
    status_list = ["PENDENTE" for _ in pending]
    recipient_modems = ["-" for _ in pending]
    modem_order = list(selected)
    modem_labels = build_modem_labels(modem_order)
    modem_status = {dev: "-" for dev in modem_order}
    report_records = []
    while True:
        def progress_cb(
            sent,
            total,
            ok_count,
            fail_count,
            current_num,
            statuses,
            current_idx,
            current_modem,
        ):
            title = "Enviando" if attempt == 1 else f"Enviando (tentativa {attempt})"
            draw_progress(
                stdscr,
                title,
                sent,
                total,
                ok_count,
                fail_count,
                current_num,
                recipients=pending,
                status_list=statuses,
                current_idx=current_idx,
                current_modem=current_modem,
                recipient_modems=recipient_modems,
                modem_status=modem_status,
                modem_order=modem_order,
                modem_labels=modem_labels,
                delay_info=delay_info,
            )

        ok_count, failed = send_numbers(
            pending,
            sections,
            selected,
            message,
            flash,
            delay,
            random_delay_enabled=random_delay_enabled,
            random_delay_min_sec=random_delay_min_sec,
            random_delay_max_sec=random_delay_max_sec,
            progress_cb=progress_cb,
            status_list=status_list,
            recipient_modems=recipient_modems,
            modem_status=modem_status,
            modem_labels=modem_labels,
            report_records=report_records,
        )
        total_ok += ok_count
        fail_count = len(failed)
        title = "Resultado" if attempt == 1 else f"Resultado (tentativa {attempt})"
        report_lines, _ = build_report_from_records(report_records)
        if fail_count == 0:
            message_screen(
                stdscr,
                title,
                [f"Enviados: {total_ok}", "Falhas: 0", ""] + report_lines,
            )
            break
        retry = retry_screen(
            stdscr,
            title,
            [f"Enviados: {total_ok}", f"Falhas: {fail_count}", ""] + report_lines,
        )
        if not retry:
            break
        pending = [{"number": n, "name": ""} for n in failed]
        attempt += 1
        status_list = ["PENDENTE" for _ in pending]
        recipient_modems = ["-" for _ in pending]


def settings_menu(stdscr, cfg):
    while True:
        options = [
            f"Toggle flash (atual: {'sim' if cfg.get('flash') else 'nao'})",
            f"Pais prefixo (atual: {cfg.get('country_prefix') or 'vazio'})",
            f"Connection (atual: {cfg.get('connection')})",
            f"Delay entre envios (seg, atual: {cfg.get('send_delay_sec')})",
            f"Delay aleatorio (atual: {'sim' if cfg.get('random_delay_enabled') else 'nao'})",
            f"Delay aleatorio min (seg, atual: {cfg.get('random_delay_min_sec', 10)})",
            f"Delay aleatorio max (seg, atual: {cfg.get('random_delay_max_sec', 30)})",
            f"Validar modems (atual: {'sim' if cfg.get('validate_modems') else 'nao'})",
            f"Mostrar numero do chip (atual: {'sim' if cfg.get('read_numbers') else 'nao'})",
            f"Comandos AT (atual: {summarize_at_commands(cfg.get('init_at_commands'))})",
            f"Baud AT (atual: {cfg.get('init_at_baud', 115200)})",
            f"Auto ativar AT (atual: {'sim' if cfg.get('auto_activate_on_start', False) else 'nao'})",
            f"Keepalive AT (atual: {'sim' if cfg.get('keepalive_enabled', False) else 'nao'})",
            f"Keepalive intervalo (seg, atual: {cfg.get('keepalive_interval_sec', 60)})",
            f"Keepalive comandos (atual: {summarize_at_commands(cfg.get('keepalive_commands'))})",
            "Voltar",
        ]
        choice = menu(stdscr, "Config", options)
        if choice is None or choice == 15:
            return
        if choice == 0:
            cfg["flash"] = not cfg.get("flash", False)
            save_config(cfg)
        elif choice == 1:
            val = prompt_input(
                stdscr,
                "Prefixo",
                "Digite o prefixo do pais (ex: 55) ou vazio:",
                cfg.get("country_prefix", ""),
            )
            cfg["country_prefix"] = val
            save_config(cfg)
        elif choice == 2:
            val = prompt_input(
                stdscr,
                "Connection",
                "Digite o connection do Gammu (ex: at, at115200):",
                cfg.get("connection", "at"),
            )
            cfg["connection"] = val or "at"
            save_config(cfg)
        elif choice == 3:
            val = prompt_input(
                stdscr,
                "Delay",
                "Delay entre envios (segundos, ex: 0.5):",
                str(cfg.get("send_delay_sec", 0)),
                replace_on_type=True,
            )
            try:
                cfg["send_delay_sec"] = max(float(val.replace(",", ".")), 0.0)
            except Exception:
                cfg["send_delay_sec"] = cfg.get("send_delay_sec", 0.0)
            save_config(cfg)
        elif choice == 4:
            cfg["random_delay_enabled"] = not cfg.get("random_delay_enabled", False)
            save_config(cfg)
        elif choice == 5:
            val = prompt_input(
                stdscr,
                "Delay aleatorio min",
                "Minimo em segundos (ex: 10):",
                str(cfg.get("random_delay_min_sec", 10)),
                replace_on_type=True,
            )
            try:
                cfg["random_delay_min_sec"] = max(float(val.replace(",", ".")), 0.0)
            except Exception:
                cfg["random_delay_min_sec"] = cfg.get("random_delay_min_sec", 10.0)
            save_config(cfg)
        elif choice == 6:
            val = prompt_input(
                stdscr,
                "Delay aleatorio max",
                "Maximo em segundos (ex: 30):",
                str(cfg.get("random_delay_max_sec", 30)),
                replace_on_type=True,
            )
            try:
                cfg["random_delay_max_sec"] = max(float(val.replace(",", ".")), 0.0)
            except Exception:
                cfg["random_delay_max_sec"] = cfg.get("random_delay_max_sec", 30.0)
            save_config(cfg)
        elif choice == 7:
            cfg["validate_modems"] = not cfg.get("validate_modems", True)
            save_config(cfg)
        elif choice == 8:
            cfg["read_numbers"] = not cfg.get("read_numbers", False)
            save_config(cfg)
        elif choice == 9:
            val = prompt_input(
                stdscr,
                "Comandos AT",
                "Comandos AT separados por ';' (ex: AT+ZCDRUN=8) ou vazio:",
                cfg.get("init_at_commands", ""),
                replace_on_type=True,
            )
            cfg["init_at_commands"] = val or ""
            save_config(cfg)
        elif choice == 10:
            val = prompt_input(
                stdscr,
                "Baud AT",
                "Baud rate para comandos AT (ex: 115200):",
                str(cfg.get("init_at_baud", 115200)),
                replace_on_type=True,
            )
            try:
                cfg["init_at_baud"] = max(int(str(val).strip()), 1200)
            except Exception:
                cfg["init_at_baud"] = cfg.get("init_at_baud", 115200)
            save_config(cfg)
        elif choice == 11:
            cfg["auto_activate_on_start"] = not cfg.get("auto_activate_on_start", False)
            save_config(cfg)
        elif choice == 12:
            cfg["keepalive_enabled"] = not cfg.get("keepalive_enabled", False)
            save_config(cfg)
        elif choice == 13:
            val = prompt_input(
                stdscr,
                "Keepalive intervalo",
                "Intervalo em segundos (ex: 60) ou 0 para desativar:",
                str(cfg.get("keepalive_interval_sec", 60)),
                replace_on_type=True,
            )
            try:
                cfg["keepalive_interval_sec"] = max(float(val.replace(",", ".")), 0.0)
            except Exception:
                cfg["keepalive_interval_sec"] = cfg.get("keepalive_interval_sec", 60)
            save_config(cfg)
        elif choice == 14:
            val = prompt_input(
                stdscr,
                "Keepalive comandos",
                "Comandos AT separados por ';' (ex: AT) ou vazio:",
                cfg.get("keepalive_commands", "AT"),
                replace_on_type=True,
            )
            cfg["keepalive_commands"] = val or ""
            save_config(cfg)


def main(stdscr):
    locale.setlocale(locale.LC_ALL, "")
    curses.use_default_colors()
    cfg = load_config()
    auto_activated_real = set()
    last_keepalive = 0.0

    while True:
        auto_activate_devices(cfg, auto_activated_real)
        last_keepalive = keepalive_devices(cfg, last_keepalive)
        devices, status_map, number_map = scan_devices_with_status(
            cfg.get("connection", "at"),
            cfg.get("validate_modems", True),
            cfg.get("read_numbers", False),
            prefer_devices=cfg.get("selected_devices", []),
        )
        labels_map = build_modem_labels(devices)
        options = [
            "Selecionar modems",
            "Compor e enviar",
            "Reenviar do historico",
            "Historico",
            "Relatorio",
            "Ativar modems (AT)",
            "Liberar portas (kill)",
            "Configuracoes",
            "Ver log",
            "Ajuda",
            "Sair",
        ]
        title = f"Europa CLI - Sender SMS ({VERSION})"
        def tick():
            nonlocal last_keepalive
            auto_activate_devices(cfg, auto_activated_real)
            last_keepalive = keepalive_devices(cfg, last_keepalive)

        choice = menu(stdscr, title, options, tick_fn=tick, tick_interval_sec=1)
        if choice is None or choice == 10:
            break
        if choice == 0:
            selected_cfg = cfg.get("selected_devices", [])
            selected = resolve_selected_devices(devices, selected_cfg)
            checked = set(selected)
            while True:
                def do_rescan():
                    auto_activate_devices(cfg, auto_activated_real)
                    items, status, numbers = scan_devices_with_status(
                        cfg.get("connection", "at"),
                        cfg.get("validate_modems", True),
                        cfg.get("read_numbers", False),
                        prefer_devices=cfg.get("selected_devices", []),
                    )
                    labels = build_modem_labels(items)
                    return items, status, numbers, labels

                result = checkbox_list(
                    stdscr,
                    "Modems",
                    devices,
                    checked,
                    status_map,
                    number_map,
                    labels=labels_map,
                    rescan_fn=do_rescan,
                    rescan_interval_sec=30,
                )
                if result == "__RESCAN__":
                    devices, status_map, number_map, labels_map = do_rescan()
                    continue
                checked = result
                break
            cfg["selected_devices"] = sorted(checked)
            save_config(cfg)
        elif choice == 1:
            compose_and_send(stdscr, cfg, devices)
        elif choice == 2:
            resend_from_history(stdscr, cfg, devices)
        elif choice == 3:
            history_screen(stdscr)
        elif choice == 4:
            report_screen(stdscr)
        elif choice == 5:
            activate_modems_screen(stdscr, cfg, devices)
        elif choice == 6:
            release_ports_screen(stdscr, cfg, devices)
        elif choice == 7:
            settings_menu(stdscr, cfg)
        elif choice == 8:
            view_log(stdscr)
        elif choice == 9:
            help_screen(stdscr)


if __name__ == "__main__":
    curses.wrapper(main)
