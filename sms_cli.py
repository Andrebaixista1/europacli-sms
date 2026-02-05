#!/usr/bin/env python3
import curses
import json
import locale
import os
import re
import subprocess
import time
from datetime import datetime
from glob import glob
import csv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
GAMMU_RC_PATH = os.path.join(BASE_DIR, "gammurc")
LOG_PATH = os.path.join(BASE_DIR, "sms_cli.log")

DEFAULT_CONFIG = {
    "country_prefix": "55",
    "flash": False,
    "connection": "at",
    "selected_devices": [],
    "send_delay_sec": 1.0,
    "validate_modems": True,
    "last_csv_path": "",
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
        return cfg
    except Exception:
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def scan_devices():
    patterns = [
        "/dev/ttyUSB*",
    ]
    devices = []
    for p in patterns:
        devices.extend(glob(p))
    return sorted(set(devices))


def section_name_for_device(dev):
    base = os.path.basename(dev)
    name = re.sub(r"[^A-Za-z0-9_]+", "_", base)
    if not name:
        name = "device"
    return name


def write_gammu_config(devices, connection):
    lines = []
    for dev in devices:
        section = section_name_for_device(dev)
        lines.append(f"[{section}]")
        lines.append(f"device = {dev}")
        lines.append(f"connection = {connection}")
        lines.append("")
    with open(GAMMU_RC_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_temp_gammu_config(dev, connection):
    section = section_name_for_device(dev)
    lines = [
        f"[{section}]",
        f"device = {dev}",
        f"connection = {connection}",
        "",
    ]
    path = os.path.join(BASE_DIR, "gammurc_check")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path, section


def is_valid_modem(dev, connection):
    try:
        cfg, section = write_temp_gammu_config(dev, connection)
        cmd = ["gammu", "-c", cfg, "-s", section, "identify"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return proc.returncode == 0
    except Exception:
        return False


def get_own_number(dev, connection):
    try:
        cfg, section = write_temp_gammu_config(dev, connection)
        cmd = ["gammu", "-c", cfg, "-s", section, "getmemory", "ON", "1", "20", "-nonempty"]
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


def scan_devices_with_status(connection, validate=True):
    devices = scan_devices()
    status = {}
    numbers = {}
    for dev in devices:
        status[dev] = "?" if not validate else ("OK" if is_valid_modem(dev, connection) else "FAIL")
        numbers[dev] = get_own_number(dev, connection) or "-"
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
        numbers.append(num)
    return numbers


def parse_csv_numbers(path, prefix):
    numbers = []
    seen = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            sample = f.read(2048)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            except Exception:
                dialect = csv.excel
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                if not row:
                    continue
                num = (
                    row.get("numero")
                    or row.get("Número")
                    or row.get("Numero")
                    or row.get("NUMERO")
                )
                if not num:
                    continue
                formatted = format_number(str(num), prefix)
                if formatted and formatted not in seen:
                    seen.add(formatted)
                    numbers.append(formatted)
    except Exception:
        return []
    return numbers


def send_sms(section, number, message, flash):
    cmd = [
        "gammu",
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


def send_numbers(numbers, sections, message, flash, delay_sec):
    ok_count = 0
    failed = []
    for i, num in enumerate(numbers):
        section = sections[i % len(sections)]
        ok, out, err = send_sms(section, num, message, flash)
        if ok:
            ok_count += 1
            log_event(f"OK {num} via {section}")
        else:
            failed.append(num)
            log_event(f"FAIL {num} via {section} | {err or out}")
        if delay_sec > 0 and i < len(numbers) - 1:
            time.sleep(delay_sec)
    return ok_count, failed


def draw_header(stdscr, title):
    stdscr.clear()
    h, w = stdscr.getmaxyx()
    stdscr.addstr(0, 2, title, curses.A_BOLD)
    stdscr.hline(1, 0, "-", w)


def menu(stdscr, title, options, index=0):
    curses.curs_set(0)
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
        if key in (ord("q"), ord("Q")):
            return None
        if key in (curses.KEY_UP, ord("k")):
            index = (index - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            index = (index + 1) % len(options)
        elif key in (10, 13, curses.KEY_ENTER):
            return index


def checkbox_list(
    stdscr,
    title,
    items,
    checked,
    status=None,
    numbers=None,
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
            st = ""
            if status is not None:
                st = status.get(it, "?")
                st = f" [{st}]"
            num = ""
            if numbers is not None:
                num = numbers.get(it, "-")
                num = f" num:{num}"
            line = f"{mark} {it}{st}{num}"
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
                    items, status, numbers = rescan_fn()
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
                items, status, numbers = rescan_fn()
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
        key = stdscr.getch()
        if key in (10, 13, curses.KEY_ENTER):
            return current.strip()
        if key == 27:
            return ""
        if key == 21:
            current = ""
            touched = True
            continue
        if key in (curses.KEY_BACKSPACE, 127, 8):
            current = current[:-1]
            touched = True
            continue
        if 32 <= key <= 126:
            if replace_on_type and not touched and initial:
                current = ""
            current += chr(key)
            touched = True


def choose_file_dialog():
    for cmd in (
        ["zenity", "--file-selection", "--title=Selecione o CSV"],
        ["kdialog", "--getopenfilename", ".", "*.csv"],
    ):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                path = (proc.stdout or "").strip()
                if path:
                    return path
        except FileNotFoundError:
            continue
        except Exception:
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
        stdscr.addstr(h - 2, 2, "F2: ok  ESC: cancelar  Enter: nova linha")
        stdscr.refresh()
        key = stdscr.getch()
        if key == 27:
            return None
        if key == curses.KEY_F2:
            if current:
                lines.append(current)
            return "\n".join(lines).strip()
        if key in (10, 13, curses.KEY_ENTER):
            lines.append(current)
            current = ""
            continue
        if key in (curses.KEY_BACKSPACE, 127, 8):
            current = current[:-1]
            continue
        if 32 <= key <= 126:
            current += chr(key)


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

def release_ports(selected_devices):
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
    selected = [d for d in devices if d in cfg.get("selected_devices", [])]
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

def compose_and_send(stdscr, cfg, devices):
    selected = [d for d in devices if d in cfg.get("selected_devices", [])]
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
        numbers = parse_numbers(numbers_text, cfg.get("country_prefix", ""))
    else:
        path = choose_file_dialog()
        if not path:
            path = prompt_input(
                stdscr,
                "CSV",
                "Caminho do CSV (colunas: nome, numero):",
                cfg.get("last_csv_path", ""),
            )
        if not path:
            return
        numbers = parse_csv_numbers(path, cfg.get("country_prefix", ""))
        if numbers:
            cfg["last_csv_path"] = path
            save_config(cfg)
        message_screen(stdscr, "CSV", [f"Numeros carregados: {len(numbers)}"])
    message = multiline_input(
        stdscr,
        "Mensagem",
        "Digite a mensagem (F2 para finalizar).",
    )
    if message is None:
        return
    if not numbers:
        message_screen(stdscr, "Enviar", ["Nenhum numero valido informado."])
        return

    write_gammu_config(selected, cfg.get("connection", "at"))
    sections = [section_name_for_device(d) for d in selected]

    lines = [
        f"Modems selecionados: {len(selected)}",
        f"Numeros unicos: {len(numbers)}",
        f"Flash: {'sim' if cfg.get('flash') else 'nao'}",
        "",
        "Confirma o envio?",
    ]
    if not confirm_screen(stdscr, "Confirmar", lines):
        return

    delay = float(cfg.get("send_delay_sec", 0))
    attempt = 1
    pending = numbers
    total_ok = 0
    while True:
        ok_count, failed = send_numbers(
            pending, sections, message, cfg.get("flash", False), delay
        )
        total_ok += ok_count
        fail_count = len(failed)
        title = "Resultado" if attempt == 1 else f"Resultado (tentativa {attempt})"
        if fail_count == 0:
            message_screen(
                stdscr,
                title,
                [f"Enviados: {total_ok}", "Falhas: 0"],
            )
            break
        retry = retry_screen(
            stdscr,
            title,
            [f"Enviados: {total_ok}", f"Falhas: {fail_count}"],
        )
        if not retry:
            break
        pending = failed
        attempt += 1


def settings_menu(stdscr, cfg):
    while True:
        options = [
            f"Toggle flash (atual: {'sim' if cfg.get('flash') else 'nao'})",
            f"Pais prefixo (atual: {cfg.get('country_prefix') or 'vazio'})",
            f"Connection (atual: {cfg.get('connection')})",
            f"Delay entre envios (seg, atual: {cfg.get('send_delay_sec')})",
            f"Validar modems (atual: {'sim' if cfg.get('validate_modems') else 'nao'})",
            "Voltar",
        ]
        choice = menu(stdscr, "Config", options)
        if choice is None or choice == 5:
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
            cfg["validate_modems"] = not cfg.get("validate_modems", True)
            save_config(cfg)


def main(stdscr):
    locale.setlocale(locale.LC_ALL, "")
    curses.use_default_colors()
    cfg = load_config()

    while True:
        devices, status_map, number_map = scan_devices_with_status(
            cfg.get("connection", "at"), cfg.get("validate_modems", True)
        )
        options = [
            "Selecionar modems",
            "Compor e enviar",
            "Liberar portas (kill)",
            "Configuracoes",
            "Ver log",
            "Sair",
        ]
        choice = menu(stdscr, "Europa CLI - Sender SMS", options)
        if choice is None or choice == 5:
            break
        if choice == 0:
            checked = set(cfg.get("selected_devices", []))
            while True:
                def do_rescan():
                    return scan_devices_with_status(
                        cfg.get("connection", "at"), cfg.get("validate_modems", True)
                    )

                result = checkbox_list(
                    stdscr,
                    "Modems",
                    devices,
                    checked,
                    status_map,
                    number_map,
                    rescan_fn=do_rescan,
                    rescan_interval_sec=30,
                )
                if result == "__RESCAN__":
                    devices, status_map, number_map = do_rescan()
                    continue
                checked = result
                break
            cfg["selected_devices"] = sorted(checked)
            save_config(cfg)
        elif choice == 1:
            compose_and_send(stdscr, cfg, devices)
        elif choice == 2:
            release_ports_screen(stdscr, cfg, devices)
        elif choice == 3:
            settings_menu(stdscr, cfg)
        elif choice == 4:
            view_log(stdscr)


if __name__ == "__main__":
    curses.wrapper(main)
