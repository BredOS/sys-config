#!/usr/bin/env python

import re
import os
import sys
import time
import shlex
import curses
import hashlib
import argparse
import textwrap
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

APP_NAME = "BredOS Configurator"
LOG_FILE = None
DRYRUN = False
ROOT_MODE = False

DTB_PATH = Path("/boot/dtbs")
PROC_DT = Path("/proc/device-tree")

dts_cache = {}

# --------------- RUNNER ----------------


def elevated_file_write(filepath: str, content: str):
    proc = subprocess.run(
        ['pkexec', 'tee', filepath],
        input=content.encode(),
        check=True
    )

def wrap_lines(lines: list, width: int) -> list:
    return [wrapped for line in lines for wrapped in textwrap.wrap(line, width)]


def cmdr(cmd: list, stdscr=None, label: str = None) -> str:
    output = []
    if DRYRUN:
        if stdscr is not None:
            stdscr.clear()
            stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
            draw_border(stdscr)
        output = "DRYRUN: " + " ".join(cmd)
        if stdscr is not None:
            stdscr.addstr(3, 2, output)
            stdscr.refresh()
        else:
            print(output)
        return output

    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    ) as proc:
        try:
            y = 3
            ym = 0
            limit = 0
            if stdscr is not None:
                stdscr.clear()
                stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
                draw_border(stdscr)
                ym, _ = stdscr.getmaxyx()
                limit = int(ym) - 2
                stdscr.refresh()
            for line in proc.stdout:
                if stdscr is not None:
                    if y < limit:
                        stdscr.addstr(y if y <= limit else limit, 2, line)
                    else:
                        for i in range(3, limit):
                            clear_line(stdscr, i)
                            stdscr.addstr(i, 2, output[y - limit - 3 + i][:-1])
                    y += 1
                    draw_border(stdscr)
                    stdscr.refresh()
                else:
                    print(line, end="")
                output.append(line)
            proc.wait()
        except KeyboardInterrupt:
            try:
                proc.kill()
            except:
                pass
            return -1
    returncode = proc.returncode
    return "".join(output)


def cli_runner(cmd: str, elevate: bool = False) -> None:
    global LOG_FILE, ROOT_MODE

    if elevate and not ROOT_MODE:
        cmd = ["pkexec"] + cmd

    result = cmdr(cmd)

    if LOG_FILE is not None and result != -1:
        with open(LOG_FILE, "a") as f:
            f.write(f"$ {' '.join(cmd)}\n")
            f.write(result.stdout + "\n")

    if result == -1:
        print("\nABORTED")
    else:
        print("\nOK")


def tui_runner(
    stdscr, label: str, cmd: list, elevate: bool = False, prompt: bool = True
) -> None:
    global LOG_FILE, ROOT_MODE

    stdscr.clear()
    draw_border(stdscr)
    stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
    if elevate and not ROOT_MODE:
        cmd = ["pkexec"] + cmd
    stdscr.addstr(
        4,
        2,
        ("#" if elevate else "$") + " " + " ".join(cmd if not elevate else cmd[1:]),
    )
    stdscr.refresh()

    output = cmdr(cmd, stdscr, label)

    if LOG_FILE is not None and output != -1:
        with open(LOG_FILE, "a") as f:
            f.write(f"$ {' '.join(cmd)}\n{output}\n")

    maxy, _ = stdscr.getmaxyx()

    if not prompt:
        return

    stdscr.attron(curses.A_REVERSE)
    stdscr.addstr(
        maxy - 2, 2, ("ABORTED" if output == -1 else "OK") + " - Press Enter to return"
    )
    stdscr.attroff(curses.A_REVERSE)
    stdscr.refresh()
    while stdscr.getch() != ord("\n"):
        pass
    wait_clear(stdscr)


def message(
    text: list, stdscr=None, label: str = APP_NAME, prompt: bool = True
) -> None:
    if stdscr is None:
        for line in text:
            print(line)
        return

    text = [subline for line in text for subline in line.split('\n')]

    maxy, _ = stdscr.getmaxyx()
    stdscr.clear()
    draw_border(stdscr)
    stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
    y = 3
    for line in text:
        stdscr.addstr(y, 2, line)
        y += 1

    if not prompt:
        stdscr.refresh()
        return

    stdscr.attron(curses.A_REVERSE)
    stdscr.addstr(maxy - 2, 2, " Press Enter to continue ")
    stdscr.attroff(curses.A_REVERSE)
    stdscr.refresh()
    while stdscr.getch() != ord("\n"):
        pass
    wait_clear(stdscr)


def confirm(text: list, stdscr=None, label: str = APP_NAME) -> None:
    if stdscr is None:
        for line in text:
            print(line)

        while True:
            try:
                dat = input("(Y/N)> ")
                if dat in ["y", "Y"]:
                    return True
                elif dat in ["n", "N"]:
                    return False
            except KeyboardInterrupt:
                pass
            except EOFError:
                pass

        return False  # Magical fallthrough

    maxy, _ = stdscr.getmaxyx()
    stdscr.clear()
    draw_border(stdscr)
    stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
    y = 3
    for line in text:
        stdscr.addstr(y, 2, line)
        y += 1

    sel = None
    stdscr.attron(curses.A_REVERSE)
    stdscr.addstr(
        maxy - 2,
        2,
        " Confirm (Y/N): ",
    )
    stdscr.attroff(curses.A_REVERSE)
    stdscr.refresh()

    while True:  # Way too many ways to do this, cba to do it fancy
        dat = stdscr.getch()
        if dat == ord("\n"):
            if sel is not None:
                break
        elif (sel is not True) and dat in [ord("y"), ord("Y")]:
            sel = True
            stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(
                maxy - 2,
                2,
                " Confirm (Y/N): Y | Press enter to continue ",
            )
            stdscr.attroff(curses.A_REVERSE)
            stdscr.refresh()
        elif (sel is not False) and dat in [ord("n"), ord("N")]:
            sel = False
            stdscr.attron(curses.A_REVERSE)
            stdscr.addstr(
                maxy - 2,
                2,
                " Confirm (Y/N): N | Press enter to continue ",
            )
            stdscr.attroff(curses.A_REVERSE)
            stdscr.refresh()
        elif sel is not None:
            sel = None

            stdscr.attron(curses.A_REVERSE)
            clear_line(stdscr, maxy - 2)
            stdscr.addstr(
                maxy - 2,
                2,
                " Confirm (Y/N): ",
            )
            stdscr.attroff(curses.A_REVERSE)
            stdscr.border()
            stdscr.refresh()

    wait_clear(stdscr)
    return sel


def selector(items: list, stdscr, multi: bool, label: str | None = None) -> list | int:
    curses.curs_set(0)
    selected = [False] * len(items)
    idx = 0
    offset = 0

    def draw() -> None:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        if label:
            stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
        start_y = 3
        view_h = h - start_y - 1
        draw_border(stdscr)
        nonlocal offset
        if idx < offset:
            offset = idx
        elif idx >= offset + view_h:
            offset = idx - view_h + 1
        for view_idx in range(view_h):
            item_idx = offset + view_idx
            y = start_y + view_idx
            if item_idx >= len(items):
                break
            prefix = "- [x]" if multi and selected[item_idx] else "- [ ]" if multi else " <*>" if idx == item_idx else " < >"
            text = f"{prefix} {items[item_idx]}"
            attr = curses.A_REVERSE if item_idx == idx else curses.A_NORMAL
            stdscr.addnstr(y, 2, text, w - 4, attr)
        stdscr.refresh()

    while True:
        draw()
        key = stdscr.getch()
        if key ==curses.KEY_UP:
            idx = (idx - 1) % len(items)
        elif key == curses.KEY_DOWN:
            idx = (idx + 1) % len(items)
        elif key == ord(' ') and multi:
            selected[idx] = not selected[idx]
        elif key == ord("q"):
            return [] if multi else None
        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            if multi:
                return [i for i, sel in enumerate(selected) if sel]
            else:
                return idx
        elif key == 27:  # ESC
            return [] if multi else None


def runner(
    cmd: list, elevate=True, stdscr=None, label: str = APP_NAME, prompt: bool = True
) -> None:
    if stdscr is None:
        cli_runner(cmd, elevate=elevate)
    else:
        tui_runner(stdscr, label, cmd, elevate=elevate, prompt=prompt)


# ---- DEVICE TREE SUPPORT FUNCTIONS ----


def safe_exists(path: str) -> bool:
    try:
        real_path = os.path.realpath(path)

        boot_path = os.path.dirname(path)
        if not os.path.isdir(boot_path):
            return False

        return os.path.isfile(real_path)
    except Exception:
        return False


def grub_exists() -> bool:
    return safe_exists("/boot/grub/grub.cfg")


def extlinux_exists() -> bool:
    return safe_exists("/boot/extlinux/extlinux.conf")


def booted_with_edk() -> bool:
    try:
        output = subprocess.check_output(["journalctl", "-b"], text=True)
        lines = output.splitlines()[:20]
        pattern = re.compile(r"efi: EFI v[\d.]+ by .+", re.IGNORECASE)
        return any(pattern.search(line) for line in lines)
    except subprocess.CalledProcessError:
        return False


def extract_dtb_info(dtb_path: Path) -> dict | None:
    output = dtb_to_dts(dtb_path)

    description = None
    compatible = []

    for line in output.splitlines():
        line = line.strip()
        if line.startswith("description =") and description is not None:
            description = line.split("=", 1)[1].strip().strip('"').strip(";")
        elif line.startswith("compatible =") and not compatible:
            compat_str = line.split("=", 1)[1].strip().strip(";")
            compatible = [compat_str.strip().strip('"')]

    name = str(dtb_path)
    name = name[name.rfind("/") + 1 : name.rfind(".")]

    return {
        "name": name,
        "description": description,
        "compatible": compatible,
    }


def dtb_to_dts(dtb_path) -> str | None:
    global dts_cache
    if dtb_path in dts_cache:
        return dts_cache[dtb_path]
    try:
        res = subprocess.check_output(
            ["dtc", "-I", "dtb", "-O", "dts", "-q", str(dtb_path)],
            stderr=subprocess.DEVNULL,
        ).decode()

        dts_cache[dtb_path] = res
        return res
    except subprocess.CalledProcessError:
        return None


def fdt_hash_from_proc() -> str | None:
    try:
        return subprocess.check_output(
            ["dtc", "-I", "fs", "-O", "dts", "/proc/device-tree"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def hash_str(data):
    return hashlib.sha256(data.encode()).hexdigest()


def identify_base_dtb() -> tuple | None:
    live_dts = fdt_hash_from_proc()
    if live_dts is None:
        return None, "Failed to read live FDT"

    live_hash = hash_str(live_dts.decode())

    candidates = list(DTB_PATH.rglob("*.dtb"))
    matches = []
    for dtb in candidates:
        dts = dtb_to_dts(dtb)
        if dts is None:
            continue
        if hash_str(dts) == live_hash:
            matches.append(dtb.relative_to(DTB_PATH))

    if matches:
        return matches[0], None
    else:
        return None, "No exact match found (overlays likely applied)"


def diff_dts(base_dts, live_dts) -> list:
    base_lines = set(base_dts.splitlines())
    live_lines = set(live_dts.splitlines())
    return list(live_lines - base_lines)


def dt_process_candidate(dtb_path, live_hash) -> tuple | None:
    dts = dtb_to_dts(dtb_path)
    if dts is None:
        return None
    h = hash_str(dts)
    return (dtb_path.relative_to(DTB_PATH), h, dts)


def dt_detect_live() -> tuple:
    live_dts = fdt_hash_from_proc()
    if live_dts is None:
        return None, "Could not read live FDT"

    live_dts_str = live_dts.decode()
    live_hash = hash_str(live_dts_str)
    candidates = list(DTB_PATH.rglob("*.dtb"))

    best_match = None
    best_dts = None
    overlay_diff = []
    min_diff = float("inf")

    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(dt_process_candidate, dtb, live_hash): dtb
            for dtb in candidates
        }

        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            dtb_relpath, candidate_hash, candidate_dts = result
            if candidate_hash == live_hash:
                return dtb_relpath, []
            else:
                diff_len = len(diff_dts(candidate_dts, live_dts_str))
                if diff_len < min_diff:
                    min_diff = diff_len
                    best_match = dtb_relpath
                    best_dts = candidate_dts

    if best_dts:
        overlay_diff = diff_dts(best_dts, live_dts_str)
        return best_match, overlay_diff

    return None, "No match found"


def dt_gencache() -> dict:
    res = {"base": {}, "overlays": {}}
    try:
        dtb_root = Path("/boot/dtbs")

        base_files = list(dtb_root.rglob("*.dtb"))
        overlay_files = list(dtb_root.rglob("*.dtbo"))

        with ThreadPoolExecutor() as executor:
            future_to_path_base = {
                executor.submit(extract_dtb_info, path): path for path in base_files
            }
            future_to_path_overlay = {
                executor.submit(extract_dtb_info, path): path for path in overlay_files
            }

            for future in as_completed(future_to_path_base):
                path = future_to_path_base[future]
                result = future.result()
                if result:
                    res["base"][str(path)] = result

            for future in as_completed(future_to_path_overlay):
                path = future_to_path_overlay[future]
                result = future.result()
                if result:
                    res["overlays"][str(path)] = result
    except KeyboardInterrupt:
        pass
    except:
        pass
    return res


def debug_info(stdscr = None) -> None:
    grub = grub_exists()
    ext = extlinux_exists()
    efi = booted_with_edk()
    message(
        [f"GRUB: {grub}", f"EXTLINUX: {ext}", f"EFI: {efi}"],
        stdscr,
        "Debug Information",
    )


def parse_extlinux_conf(source) -> dict:
    if hasattr(source, 'read'):
        lines = source.read().splitlines()
    else:
        lines = source.splitlines()

    config = {
        'global': {},
        'labels': {}
    }

    current_label = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        if line.lower().startswith('label '):
            current_label = line[6:].strip()
            config['labels'][current_label] = {}
            continue

        key_value = line.split(None, 1)
        if len(key_value) == 2:
            key, value = key_value
            key = key.lower()
            value = value.strip()

            if key == 'fdtoverlays':
                value = value.split()

            if current_label:
                config['labels'][current_label][key] = value
            else:
                config['global'][key] = value
        else:
            key = key_value[0].lower()
            if current_label:
                config['labels'][current_label][key] = None
            else:
                config['global'][key] = None

    return config


def serialize_extlinux_conf(config: dict) -> str:
    lines = []

    for key, value in config.get('global', {}).items():
        if value is None:
            lines.append(key.upper())
        else:
            lines.append(f"{key.upper()} {value}")

    if lines:
        lines.append("")

    for label, directives in config.get('labels', {}).items():
        lines.append(f"LABEL {label}")
        for key, value in directives.items():
            if value is None:
                lines.append(f"    {key.upper()}")
            elif key == 'fdtoverlays' and isinstance(value, list):
                joined = ' '.join(value)
                lines.append(f"    {key.upper()} {joined}")
            else:
                lines.append(f"    {key.upper()} {value}")
        lines.append("")

    return "\n".join(lines).rstrip()


def parse_grub() -> dict:
    config = {}
    with open("/etc/default/grub") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, val = line.split('=', 1)
                key = key.strip()
                try:
                    val = shlex.split(val, posix=True)
                    val = val[0] if len(val) == 1 else val
                    if isinstance(val, list):
                        for i in range(len(val)):
                            if val[i].isdigit():
                                try:
                                    val[i] = int(val[i])
                                except:
                                    pass
                    elif isinstance(val, str):
                        if val.isdigit():
                            try:
                                val = int(val)
                            except:
                                pass
                    config[key] = val
                except ValueError:
                    config[key] = val.strip()
    return config


def force_quote(val):
    if isinstance(val, int):
        return str(val)
    return "'" + str(val).replace("'", "'\"'\"'") + "'"


def encode_grub(config: dict) -> str:
    lines = []
    for key, val in config.items():
        if isinstance(val, list):
            # Join multi-word values if they were stored as list
            val_str = ' '.join(val)
        else:
            val_str = val

        quoted_val = force_quote(val_str)
        lines.append(f'{key}={quoted_val}')
    return '\n'.join(lines)


def set_base_dtb(stdscr = None, dtb: str = None) -> None:
    grub = grub_exists()
    ext = extlinux_exists()

    if grub:
        grubcfg = parse_grub()

        if dtb is not None:
            grubcfg["GRUB_DTB"] = dtb
        else:
            if "GRUB_DTB" in grubcfg.keys():
                del grubcfg["GRUB_DTB"]

        grubcfg = encode_grub(grubcfg)

        if not DRYRUN:
            elevated_file_write("/etc/default/grub", grubcfg)
        else:
            message(["The GRUB config would have been updated with the following:", "", grubcfg], stdscr, "DRYRUN Simulated Output")

        runner(["grub-mkconfig", "-o" ,"/boot/grub/grub.cfg"], True, stdscr, "Update GRUB Configuration")

    if ext:
        pass


def set_overlays(stdscr = None, dtbos: list = None) -> None:
    grub = grub_exists()
    ext = extlinux_exists()

    if grub:
        grubcfg = parse_grub()

        # do lomgicc

        grubcfg = encode_grub(grubcfg)

        if not DRYRUN:
            elevated_file_write("/etc/default/grub", grubcfg)
        else:
            message(["The GRUB config would have been updated with the following:", "", grubcfg], stdscr, "DRYRUN Simulated Output")

        runner(["grub-mkconfig", "-o" ,"/boot/grub/grub.cfg"], True, stdscr, "Update GRUB Configuration")

    if ext:
        pass

# -------- ACTIVATABLE COMMANDS ---------


def filesystem_maint(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        'findmnt -n -o FSTYPE / | grep -q btrfs && echo "Detected BTRFS root, performing balance operation." && btrfs balance start -dusage=20 -musage=20 /',
    ]
    runner(cmd, True, stdscr, "Filesystem Maintenance")


def filesystem_check(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        'findmnt -n -o FSTYPE / | grep -q btrfs && echo "Detected BTRFS root, performing scrub operation." && btrfs scrub start -Bd /',
    ]
    elevate = True
    runner(cmd, True, stdscr, "Filesystem Check")


def filesystem_resize(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        'systemctl enable resizefs && echo "The filesystem will be resized on next reboot!"',
    ]
    runner(cmd, True, stdscr, "Filesystem Resize")


def dt_manager(stdscr=None, cmd: list = []) -> None:
    message(["Please wait.."], stdscr, "Generating Device Tree Caches", False)
    dts = dt_gencache()
    if not dts["base"]:
        message(["No Device Trees were detected!"], stdscr, "Device Tree Manager", True)
        return

    if stdscr is None:
        if not cmd:
            print("No operations specified.\n\nUsage: list/base/overlay\n")
        else:
            if cmd[0] == "list":
                print("Base Device Trees:")
                maxnl = max(len(v["name"]) for v in dts["base"].values())
                maxde = max(
                    max(
                        len(v["description"] if v["description"] is not None else [])
                        for v in dts["base"].values()
                    ),
                    11,
                )
                maxco = max(
                    len(",".join(v["compatible"])) for v in dts["base"].values()
                )
                print(
                    f'{"NAME".ljust(maxnl)} | {"DESCRIPTION".ljust(maxde)} | COMPATIBLE'
                )
                for tree in dts["base"].keys():
                    base = dts["base"][tree]
                    name = base["name"]
                    desc = base["description"] or ""

                    compat = base["compatible"]
                    if compat:
                        compat_str = '"' + '","'.join(compat) + '"'
                    else:
                        compat_str = ""

                    print(f"{name.ljust(maxnl)} | {desc.ljust(maxde)} | {compat_str}")
                print("\nOverlays:")
                maxnl = max(len(v["name"]) for v in dts["overlays"].values())
                maxde = max(
                    max(
                        len(v["description"] if v["description"] is not None else [])
                        for v in dts["overlays"].values()
                    ),
                    11,
                )
                maxco = max(
                    len(",".join(v["compatible"])) for v in dts["overlays"].values()
                )
                print(
                    f'{"NAME".ljust(maxnl)} | {"DESCRIPTION".ljust(maxde)} | COMPATIBLE'
                )
                for tree in dts["overlays"].keys():
                    overlay = dts["overlays"][tree]
                    name = overlay["name"]
                    desc = overlay["description"] or ""

                    compat = overlay["compatible"]
                    if compat:
                        compat_str = '"' + '","'.join(compat) + '"'
                    else:
                        compat_str = ""

                    print(f"{name.ljust(maxnl)} | {desc.ljust(maxde)} | {compat_str}")
                print("\nLive System Tree:")
                base, overlays = dt_detect_live()
                print(f"Base: {base} (detected)\n\nOverlay-like entries (diffs):")
                for line in overlays:
                    print("  +", line)
            elif cmd[0] == "base":
                if len(cmd) - 1:
                    pass
                else:
                    print("\nLive System Tree:")
                    base, overlays = dt_detect_live()
                    print(f"Base: {base} (detected)\n\nOverlay-like entries (diffs):")
            elif cmd[0] == "overlay":
                if len(cmd) > 1:
                    if cmd[1] == "enable":
                        pass
                    elif cmd[1] == "disable":
                        pass
                    else:
                        print(
                            "Invalid operation specified.\n\nUsage: enable/disable overlay.dtbo\n"
                        )
                else:
                    print(
                        "No operations specified.\n\nUsage: enable/disable overlay.dtbo\n"
                    )
            else:
                print("Invalid operation specified.\n\nUsage: list/base/overlay\n")
        return

    res = confirm(
        [
            "This command is only for advanced users!",
            "",
            "Only continue if you know EXACTLY what you're doing.",
        ],
        stdscr,
        "Device Tree Management",
    )

    if not res:
        return

    options = [
        "Set the Base System Device Tree",
        "Enable / Disable Overlays",
        "View Currently Enabled Trees",
        "Main Menu",
    ]

    while True:
        selection = draw_menu(stdscr, "Device Tree Manager", options)
        if selection is None or options[selection] == "Main Menu":
            return

        stdscr.clear()
        stdscr.refresh()
        if options[selection] == "Set the Base System Device Tree":
            maxnl = max(len(v["name"]) for v in dts["base"].values())
            maxde = max(
                len(v["description"] if v["description"] is not None else [])
                for v in dts["base"].values()
            )
            maxco = max(
                len(",".join(v["compatible"])) for v in dts["base"].values()
            )

            basedt = []
            matchdt = []

            for tree in dts["base"].keys():
                base = dts["base"][tree]
                name = base["name"]

                compat = base["compatible"]
                if compat:
                    compat_str = '"' + '","'.join(compat) + '"'
                else:
                    compat_str = ""

                basedt.append(f"{name.ljust(maxnl)} | {compat_str}")
                matchdt.append(tree)

            res = selector(basedt, stdscr, False, "Select a device Tree")
            if res is None:
                return

        if options[selection] == "Enable / Disable Overlays":
            maxnl = max(len(v["name"]) for v in dts["overlays"].values())
            maxde = max(
                len(v["description"] if v["description"] is not None else [])
                for v in dts["overlays"].values()
            )
            maxco = max(
                len(",".join(v["compatible"])) for v in dts["overlays"].values()
            )

            basedt = []
            matchdt = []

            for tree in dts["overlays"].keys():
                base = dts["overlays"][tree]
                name = base["name"]
                desc = base["description"] or ""

                compat = base["compatible"]
                if compat:
                    compat_str = '"' + '","'.join(compat) + '"'
                else:
                    compat_str = ""

                basedt.append(f"{name.ljust(maxnl)} | {compat_str}")
                matchdt.append(tree)

            res = selector(basedt, stdscr, True, "Select overlays")
            if not res:
                return

            dtbos = []
            for i in res:
                dtbos.append(matchdt[i])

        if options[selection] == "View Currently Enabled Trees":
            pass


def hack_pipewire(stdscr=None) -> None:
    res = False
    service_path = Path.home() / ".config/systemd/user/pipewire.service"
    service_content = """[Unit]
Description=PipeWire Multimedia Service

# We require pipewire.socket to be active before starting the daemon, because
# while it is possible to use the service without the socket, it is not clear
# why it would be desirable.
#
# A user installing pipewire and doing `systemctl --user start pipewire`
# will not get the socket started, which might be confusing and problematic if
# the server is to be restarted later on, as the client autospawn feature
# might kick in. Also, a start of the socket unit will fail, adding to the
# confusion.
#
# After=pipewire.socket is not needed, as it is already implicit in the
# socket-service relationship, see systemd.socket(5).
Requires=pipewire.socket

[Service]
CPUAccounting=true
CPUQuota=10%
LockPersonality=yes
MemoryDenyWriteExecute=yes
NoNewPrivileges=yes
RestrictNamespaces=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
Type=simple
ExecStart=/usr/bin/pipewire
Restart=on-failure
Slice=session.slice

[Install]
Also=pipewire.socket
WantedBy=default.target
"""

    if service_path.exists():
        if not DRYRUN:
            service_path.unlink()
    else:
        if not DRYRUN:
            service_path.parent.mkdir(parents=True, exist_ok=True)
            service_path.write_text(service_content)
        res = True

    message(
        [
            "Pipewire CPU fix " + ("applied" if res else "removed") + ".",
            "Relog or Reboot to apply.",
        ],
        stdscr,
        "Pipewire CPU fix",
    )


def hack_wol(stdscr=None) -> None:
    cmd = [
        "bash",
        "-c",
        'pacman -Qi bredos-wol &>/dev/null && echo "Removing.." && pacman -R --noconfirm bredos-wol || { echo "Installing.."; pacman -Sy; pacman -S --noconfirm bredos-wol; }',
    ]
    runner(cmd, True, stdscr, "Wake On Lan")


def pacman_integrity(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        r"""echo "Running.." && pacman -Qkk 2>/dev/null | awk '
/:.*(missing|Size mismatch|MODIFIED)/ &&
$0 !~ /\.json|\.conf|\.pac(new|save|orig)/ &&
$0 !~ /\/\.?(bashrc|bash_profile|zshrc|profile)$/ &&
$0 !~ /^.*\/etc\/(shells|subgid|subuid|environment|sudoers|passwd|shadow|group|gshadow|fstab|mtab|issue|default\/|skel\/|locale\.gen|ssh\/|libvirt\/)/ &&
$0 !~ /\/usr\/share\/(doc|man)|\.cache/ {
    pkg = gensub(/:.*$/, "", 1, $0);
    issues[pkg]++;
    print;
    found = 1;
}
END {
    if (found) {
        print "\n==== Summary ====";
        for (p in issues) print p ": " issues[p] " issue(s)";
    } else {
        print "+++ No integrity issues found. +++";
    }
}
'""",
    ]
    runner(cmd, False, stdscr, "Check Packages Integrity")


def install_recommends(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed"
        + " webcord-bin"
        + " ayugram-desktop"
        + " thunderbird"
        + " gnome-disk-utility"
        + " mpv"
        + " libreoffice-fresh"
        + " timeshift"
        + " proton-run"
        + " evince"
        + " loupe",
    ]
    elevate = True
    runner(cmd, True, stdscr, "Install Recommended Packages")


def install_docker(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed"
        + " docker"
        + " docker-buildx"
        + " docker-compose"
        + " docker-compose"
        + " pigz"
        + " && systemctl disable --now systemd-networkd-wait-online"
        + " && systemctl mask systemd-networkd-wait-online"
        + " && systemctl enable --now docker",
    ]
    elevate = True
    runner(cmd, True, stdscr, "Install Docker")


def install_steam_any(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed steam steam-libs-any",
    ]
    elevate = True
    runner(cmd, True, stdscr, "Install Steam (Any)")


def install_steam_panfork(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed steam steam-libs-rk3588",
    ]
    elevate = True
    runner(cmd, True, stdscr, "Install Steam (RK3588, Panfork graphics)")


def install_development(stdscr=None) -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed"
        + " python-prettytable"
        + " grub"
        + " parted"
        + " gptfdisk"
        + " edk2-rk3588-devel"
        + " dtc"
        + " xmlto"
        + " docbook-xsl"
        + " kmod"
        + " bc"
        + " uboot-tools"
        + " vboot-utils"
        + " bredos-tools",
    ]
    elevate = True
    runner(cmd, True, stdscr, "Install BredOS Development Packages")


def unlock_pacman(stdscr=None) -> None:
    cmd = [
        "bash",
        "-c",
        '[ -f /var/lib/pacman/db.lck ] && ! pgrep -x pacman >/dev/null && { sudo rm -f /var/lib/pacman/db.lck && echo "Pacman DB lock removed."; } || echo "No action needed."',
    ]
    elevate = True
    runner(cmd, True, stdscr, "Unlock Pacman Database")


def autoremove(stdscr=None) -> None:
    cmd = [
        "bash",
        "-c",
        "while pacman -Qdtq >/dev/null 2>&1; do sudo pacman -Rns --noconfirm $(pacman -Qdtq); done",
    ]
    elevate = True
    runner(cmd, True, stdscr, "Remove Unused Packages")


# -------------- TUI LOGIC --------------


def draw_border(stdscr) -> None:
    stdscr.attron(curses.color_pair(1))
    stdscr.border()
    stdscr.attroff(curses.color_pair(1))


def wait_clear(stdscr, timeout: float = 0.2) -> None:
    stdscr.nodelay(True)
    keys_held = True

    while keys_held:
        try:
            keys_held = False
            start_time = time.time()

            while time.time() - start_time < timeout:
                if stdscr.getch() != -1:
                    keys_held = True
                    break
                time.sleep(0.01)
        except KeyboardInterrupt:
            pass

    stdscr.nodelay(False)


def clear_line(win, y) -> None:
    win.move(y, 0)
    win.clrtoeol()


def draw_list(
    stdscr, title: str, options: list, selected: int, special: bool = False
) -> None:
    stdscr.addstr(1, 2, title, curses.A_BOLD | curses.A_UNDERLINE)

    h, w = stdscr.getmaxyx()
    for idx, option in enumerate(options):
        x = 4
        y = 3 + idx
        clear_line(stdscr, y)
        draw_border(stdscr)
        if idx == selected:
            if special:
                stdscr.addstr(y, x, "[< " + option + " >]")
            else:
                stdscr.attron(curses.A_REVERSE)
                stdscr.addstr(y, x, "[> " + option + " <]")
                stdscr.attroff(curses.A_REVERSE)
        else:
            stdscr.addstr(y, x, option)

    stdscr.refresh()


def draw_menu(stdscr, title: str, options: list):
    curses.curs_set(0)
    current_row = 0
    wait_clear(stdscr)
    stdscr.clear()

    while True:
        try:
            draw_list(
                stdscr,
                title + (" (DRYRUN)" if DRYRUN else ""),
                options,
                selected=current_row,
            )
            key = stdscr.getch()

            if key == curses.KEY_UP:
                if current_row > 0:
                    current_row -= 1
                else:
                    current_row = len(options) - 1
            elif key == curses.KEY_DOWN:
                if current_row < len(options) - 1:
                    current_row += 1
                else:
                    current_row = 0
            elif key in (curses.KEY_ENTER, ord("\n")):
                draw_list(stdscr, title, options, selected=current_row)
                time.sleep(0.08)
                draw_list(stdscr, title, options, selected=current_row, special=True)
                time.sleep(0.08)
                draw_list(stdscr, title, options, selected=current_row)
                time.sleep(0.08)
                draw_list(stdscr, title, options, selected=current_row, special=True)
                time.sleep(0.08)
                draw_list(stdscr, title, options, selected=current_row)
                time.sleep(0.08)
                return current_row
            elif key in (ord("q"), 27):  # ESC or 'q'
                return None
            wait_clear(stdscr, 0.065)
        except KeyboardInterrupt:
            wait_clear(stdscr)
            stdscr.clear()


def sys_health_menu(stdscr):
    options = [
        "Perform Filesystem Maintenance",
        "Check & Repair Filesystem",
        "Expand Fileystem",
        "Check Packages Integrity",
        "Manage Device Trees",
        "Main Menu",
    ]

    while True:
        selection = draw_menu(stdscr, "Filesystem", options)
        if selection is None or options[selection] == "Main Menu":
            return

        stdscr.clear()
        stdscr.refresh()
        if options[selection] == "Perform Filesystem Maintenance":
            filesystem_maint(stdscr)
        if options[selection] == "Check & Repair Filesystem":
            filesystem_check(stdscr)
        if options[selection] == "Expand Fileystem":
            filesystem_resize(stdscr)
        if options[selection] == "Check Packages Integrity":
            pacman_integrity(stdscr)
        if options[selection] == "Manage Device Trees":
            dt_manager(stdscr)


def sys_tweaks_menu(stdscr) -> None:
    options = ["Pipewire CPU fix", "Wake On Lan", "Main Menu"]

    while True:
        selection = draw_menu(stdscr, "System Tweaks", options)
        if selection is None or options[selection] == "Main Menu":
            return

        stdscr.clear()
        stdscr.refresh()
        if options[selection] == "Pipewire CPU fix":
            hack_pipewire(stdscr)
        if options[selection] == "Wake On Lan":
            hack_wol(stdscr)


def packages_menu(stdscr) -> None:
    options = [
        "Install Recommended Desktop Packages",
        "Install Docker",
        "Install Steam (Any)",
        "Install Steam (Panfork graphics)",
        "Install BredOS Development Packages",
        "Unlock Pacman Database",
        "Autoremove Unused packages",
        "Check Packages Integrity",
        "Main Menu",
    ]

    while True:
        selection = draw_menu(stdscr, "Packages", options)
        if selection is None or options[selection] == "Main Menu":
            return

        stdscr.clear()
        stdscr.refresh()
        if options[selection] == "Install Recommended Desktop Packages":
            install_recommends(stdscr)
        if options[selection] == "Install Docker":
            install_docker(stdscr)
        if options[selection] == "Install Steam (Any)":
            install_steam_any(stdscr)
        if options[selection] == "Install Steam (Panfork graphics)":
            install_steam_panfork(stdscr)
        if options[selection] == "Install BredOS Development Packages":
            install_development(stdscr)
        if options[selection] == "Unlock Pacman Database":
            unlock_pacman(stdscr)
        if options[selection] == "Autoremove Unused packages":
            autoremove(stdscr)
        if options[selection] == "Check Packages Integrity":
            pacman_integrity(stdscr)


def main_menu(stdscr):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, 166, -1)  # Color
    stdscr.bkgd(" ", curses.color_pair(1))
    stdscr.clear()

    options = ["System Upkeep", "System Tweaks", "Packages", "Debug", "Exit"]

    while True:
        selection = draw_menu(stdscr, APP_NAME, options)
        if selection is None or options[selection] == "Exit":
            return

        if options[selection] == "System Upkeep":
            sys_health_menu(stdscr)
        if options[selection] == "System Tweaks":
            sys_tweaks_menu(stdscr)
        if options[selection] == "Packages":
            packages_menu(stdscr)
        if options[selection] == "Debug":
            debug_info(stdscr)


def tui():
    curses.wrapper(main_menu)


# -------------- CLI LOGIC --------------


def dp(args):
    cmd = args.command

    if cmd == "upkeep":
        if args.action == "maintenance":
            filesystem_maint()
        elif args.action == "check":
            filesystem_check()
        elif args.action == "expand":
            filesystem_resize()
        elif args.action == "dt":
            dt_manager(cmd=args.cmd)
    elif cmd == "tweaks":
        if args.target == "pipewire":
            hack_pipewire()
        if args.target == "wol":
            hack_wol()
    elif cmd == "packages":
        if args.action == "install":
            if args.target == "recommends":
                install_recommends()
            elif args.target == "docker":
                install_docker()
            elif args.target == "steam-any":
                install_steam_any()
            elif args.target == "steam-panfork":
                install_steam_panfork()
            elif args.target == "development":
                install_development()
        elif args.action == "integrity":
            pacman_integrity()
        elif args.action == "unlock":
            unlock_pacman()
        elif args.action == "autoremove":
            autoremove()
    elif cmd == "debug":
        debug_info()
    else:
        print("Unknown command")


# ----------------- MISC ------------------
def check_root() -> bool:
    if os.geteuid():
        return False
    return True


# -------------- ENTRY POINT --------------


def main():
    global LOG_FILE, DRYRUN, ROOT_MODE
    parser = argparse.ArgumentParser(prog="bredos-config", description=APP_NAME)
    parser.add_argument(
        "--log", action="store_true", help="Log output to bredos-config-<date>.txt"
    )
    parser.add_argument(
        "--dryrun", action="store_true", help="Simulate running commands (SAFE)."
    )
    subparsers = parser.add_subparsers(dest="command")

    # Admin subcommands
    fs_parser = subparsers.add_parser("upkeep")
    fs_sub = fs_parser.add_subparsers(dest="action")
    fs_sub.add_parser("maintenance")
    fs_sub.add_parser("check")
    fs_sub.add_parser("expand")

    # Device tree subcommands
    dt_parser = fs_sub.add_parser("dt")
    dt_parser.add_argument("cmd", nargs=argparse.REMAINDER)

    # Hacks
    hack_parser = subparsers.add_parser("tweaks")
    hack_sub = hack_parser.add_subparsers(dest="target")
    pipewire_parser = hack_sub.add_parser("pipewire")
    pipewire_parser = hack_sub.add_parser("wol")

    # Packages
    pac_parser = subparsers.add_parser("packages")
    pac_sub = pac_parser.add_subparsers(dest="action", required=True)

    # install sub-subcommands
    install_parser = pac_sub.add_parser("install")
    install_sub = install_parser.add_subparsers(dest="target", required=True)
    install_sub.add_parser("recommends")
    install_sub.add_parser("docker")
    install_sub.add_parser("steam")
    install_sub.add_parser("development")
    install_sub.add_parser("unlock")

    # Packages other actions
    pac_sub.add_parser("integrity")
    pac_sub.add_parser("unlock")
    pac_sub.add_parser("autoremove")

    # Dry-Run
    pipewire_parser.add_argument("--dry-run", "-d", action="store_true")

    # Info
    subparsers.add_parser("info")

    # Debug
    subparsers.add_parser("debug")

    args = parser.parse_args()

    if args.command == "health" and args.action is None:
        fs_parser.print_help()
        sys.exit(1)

    if args.command == "tweaks" and args.target is None:
        hack_parser.print_help()
        sys.exit(1)

    # Save command log
    if args.log:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        LOG_FILE = f"bredos-config-{timestamp}.txt"

    if args.dryrun:
        DRYRUN = True

    if check_root():
        ROOT_MODE = True

    if args.command is None:
        tui()
    else:
        dp(args)


if __name__ == "__main__":
    main()
