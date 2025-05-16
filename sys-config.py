#!/usr/bin/env python

import os
import sys
import time
import curses
import argparse
import textwrap
import subprocess
from pathlib import Path
from datetime import datetime

APP_NAME = "BredOS Configurator"
LOG_FILE = None
DRYRUN = False
ROOT_MODE = False

# --------------- RUNNER ----------------


def wrap_lines(lines, width):
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


def cli_runner(cmd: str, elevate: bool = False):
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
):
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

    maxy, _ = stdscr.getmaxyx()
    stdscr.clear()
    draw_border(stdscr)
    stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
    y = 3
    for line in text:
        stdscr.addstr(y, 2, line)
        y += 1

    if not prompt:
        return

    stdscr.attron(curses.A_REVERSE)
    stdscr.addstr(maxy - 2, 2, "Press Enter to return")
    stdscr.attroff(curses.A_REVERSE)
    stdscr.refresh()
    while stdscr.getch() != ord("\n"):
        pass
    wait_clear(stdscr)


def runner(
    cmd: list, elevate=True, stdscr=None, label: str = APP_NAME, prompt: bool = True
):
    if stdscr is None:
        cli_runner(cmd, elevate=elevate)
    else:
        tui_runner(stdscr, label, cmd, elevate=elevate, prompt=prompt)


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

    options = ["System Health", "System Tweaks", "Packages", "Exit"]

    while True:
        selection = draw_menu(stdscr, APP_NAME, options)
        if selection is None or options[selection] == "Exit":
            return

        if options[selection] == "System Tweaks":
            sys_tweaks_menu(stdscr)
        if options[selection] == "System Health":
            sys_health_menu(stdscr)
        if options[selection] == "Packages":
            packages_menu(stdscr)


def tui():
    curses.wrapper(main_menu)


# -------------- CLI LOGIC --------------


def dp(args):
    cmd = args.command

    if cmd == "health":
        if args.action == "maintenance":
            filesystem_maint()
        elif args.action == "check":
            filesystem_check()
        elif args.action == "expand":
            filesystem_resize()
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

    # Filesystem subcommands
    fs_parser = subparsers.add_parser("health")
    fs_sub = fs_parser.add_subparsers(dest="action")
    fs_sub.add_parser("maintenance")
    fs_sub.add_parser("check")
    fs_sub.add_parser("expand")

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
