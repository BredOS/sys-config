#!/usr/bin/env python

import re
import os
import sys
import time
import shlex
import curses
import shutil
import argparse
import textwrap
import subprocess
from pathlib import Path
from datetime import datetime

from bredos import dt
from bredos import utilities
from bredos import curseapp as c

c.APP_NAME = "BredOS Configurator"
LOG_FILE = None
DRYRUN = False
ROOT_MODE = False

# --------------- RUNNER ----------------

elevator = utilities.Elevator()


def cmdr(cmd: list, elevate: bool = False, label: str = None) -> str:
    output = []
    if DRYRUN:
        if c.stdscr is not None:
            c.stdscr.clear()
            c.stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
            c.draw_border()
        output = "DRYRUN: " + " ".join(cmd)
        if c.stdscr is not None:
            c.stdscr.addstr(3, 2, output)
            c.stdscr.refresh()
        else:
            print(output)
        return output

    proc_cm = None
    if elevate and not ROOT_MODE:
        auth = False
        if not elevator.spawned:
            auth = True
            if c.stdscr is not None:
                c.suspend()
            print("Authenticating..")
        proc_cm = elevator.run(" ".join(shlex.quote(part) for part in cmd))
        if auth and c.stdscr is not None:
            c.resume()
    else:
        proc_cm = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
        )

    with proc_cm as proc:
        try:
            y = 3
            ym = 0
            limit = 0
            if c.stdscr is not None:
                c.stdscr.clear()
                c.stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
                c.draw_border()
                ym, _ = c.stdscr.getmaxyx()
                limit = int(ym) - 2
                c.stdscr.refresh()
            for line in proc.stdout:
                if "[[EOC]]" in line:
                    break
                if c.stdscr is not None:
                    if y < limit:
                        c.stdscr.addstr(y if y <= limit else limit, 2, line)
                    else:
                        for i in range(3, limit):
                            clear_line(i)
                            c.stdscr.addstr(i, 2, output[y - limit - 3 + i][:-1])
                    y += 1
                    c.draw_border()
                    c.stdscr.refresh()
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

    result = cmdr(cmd, elevate)

    if LOG_FILE is not None and result != -1:
        with open(LOG_FILE, "a") as f:
            f.write(f"$ {' '.join(cmd)}\n")
            f.write(result.stdout + "\n")

    if result == -1:
        print("\nABORTED")


def tui_runner(
    label: str, cmd: list, elevate: bool = False, prompt: bool = True
) -> None:
    global LOG_FILE, ROOT_MODE

    c.stdscr.clear()
    c.draw_border()
    c.stdscr.addstr(1, 2, label, curses.A_BOLD | curses.A_UNDERLINE)
    c.stdscr.addstr(
        4,
        2,
        ("#" if elevate else "$") + " " + " ".join(cmd if not elevate else cmd[1:]),
    )
    c.stdscr.refresh()

    output = cmdr(cmd, elevate, label)

    if LOG_FILE is not None and output != -1:
        with open(LOG_FILE, "a") as f:
            f.write(f"$ {' '.join(cmd)}\n{output}\n")

    maxy, _ = c.stdscr.getmaxyx()

    if not prompt:
        if DRYRUN:
            time.sleep(3)
        return

    c.stdscr.attron(curses.A_REVERSE)
    c.stdscr.addstr(
        maxy - 2,
        2,
        " " + ("ABORTED" if output == -1 else "OK") + " - Press Enter to return ",
    )
    c.stdscr.attroff(curses.A_REVERSE)
    c.stdscr.refresh()
    while c.stdscr.getch() != ord("\n"):
        pass
    c.wait_clear()


def runner(
    cmd: list, elevate=True, label: str = c.APP_NAME, prompt: bool = True
) -> None:
    if c.stdscr is None:
        cli_runner(cmd, elevate=elevate, prompt=prompt)
    else:
        tui_runner(label, cmd, elevate=elevate)


def mrunner(
    cmds: list, elevate=True, label: str = c.APP_NAME, prompt: bool = True
) -> None:
    cmd = " && ".join(" ".join(b.replace("'", "\\'") for b in a) for a in cmds)
    runner(["sh", "-c", cmd], elevate, label, prompt)


def debug_info() -> None:
    grub = dt.grub_exists()
    ext = dt.extlinux_exists()
    efi = dt.booted_with_edk()
    c.message(
        [
            f"GRUB: {grub}",
            f"EXTLINUX: {ext}",
            f"EFI: {efi}",
            f"Elevated: {elevator.spawned}",
            f"DryRun: {DRYRUN}",
        ],
        "Debug Information",
    )


def normalize_filename(filename: str, extension: str) -> str:
    base = os.path.basename(filename)
    name, ext = os.path.splitext(base)
    return f"{name}.{extension}"


def set_base_dtb(dtb: str = None) -> None:
    grub = dt.grub_exists()
    ext = dt.extlinux_exists()
    normalized_dtb = None
    matched_dtb = None
    dtb_cache = dt.gencache()

    if dtb is not None:
        bases = []
        for i in list(dtb_cache["base"].keys()):
            bases.append(normalize_filename(i, "dtb"))

        normalized_dtb = normalize_filename(dtb, "dtb")

        if (normalized_dtb is None) or (normalized_dtb not in bases):
            c.message(
                [f'DTB "{dtb}" not found on system, refusing to continue.'], "ERROR"
            )
            return

        matched_dtb = utilities.match_filename(
            normalized_dtb, list(dtb_cache["base"].keys())
        )
        if matched_dtb is None:
            c.message(["Failed to match DTB!"], "ERROR")
            return
    else:
        c.message("Refusing to unset Base DTB!", "ERROR")
        return

    if grub:
        grubcfg = dt.parse_grub()

        if not dt.uefi_overriden():
            grubdtbn = matched_dtb
            if grubdtbn.startswith("/boot/"):
                grubdtbn = grubdtbn[6:]
            grubcfg["GRUB_DTB"] = grubdtbn

            grubcfg = dt.encode_grub(grubcfg)

            if not DRYRUN:
                c.elevated_file_write("/etc/default/grub", grubcfg)
            else:
                c.message(
                    [
                        "The GRUB config would have been updated with the following:",
                        "",
                        grubcfg,
                    ],
                    "DRYRUN Simulated Output",
                )

            runner(
                ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
                True,
                "Update GRUB Configuration",
            )
        else:
            # Remove all old Base DTBs
            efidir = dt.detect_efidir()
            listing = utilities.ls(efidir + "/dtb/base/")
            listing_str = " ".join(str(p) for p in listing)

            cmds = [
                ["rm", "-v", listing_str],
                ["cp", "-v", matched_dtb, efidir + "/dtb/base/"],
            ]

            # Changes here must also be performed down in set_overlays
            if normalized_dtb == "rk3588s-fydetab-duo.dtb":
                cmds.append(
                    [
                        "cp",
                        "-v",
                        matched_dtb,
                        efidir + "/dtb/base/rk3588s-tablet-12c-linux.dtb",
                    ]
                )
            elif normalized_dtb == "rk3588-rock-5b-plus.dtb":
                cmds.append(
                    ["cp", "-v", matched_dtb, efidir + "/dtb/base/rk3588-rock-5bp.dtb"]
                )
            cmds.append(["sync", efidir])
            mrunner(cmds, True, "Performing Changes", False)

    if ext:
        extcfg = dt.parse_uboot()

        extcfg["U_BOOT_FDT"] = normalized_dtb

        extcfg = dt.encode_uboot(extcfg)

        if not DRYRUN:
            c.elevated_file_write("/etc/default/u-boot", extcfg)
        else:
            c.message(
                [
                    "The u-boot config would have been updated with the following:",
                    "",
                    extcfg,
                ],
                "DRYRUN Simulated Output",
            )

        runner(
            ["u-boot-update"],
            True,
            "Trigger U-Boot Update",
        )


def set_overlays(dtbos: list = []) -> None:
    grub = dt.grub_exists()
    ext = dt.extlinux_exists()

    normalized_dtbos = []
    matched_dtbos = []
    dtb_cache = dt.gencache()

    if dtbos:
        overlays = []

        # Populate overlays cache with trimmed names
        for i in list(dtb_cache["overlays"].keys()):
            overlays.append(normalize_filename(i, "dtbo"))

        # Format inputted dtbos
        for i in dtbos:
            normalized_dtbos.append(normalize_filename(i, "dtbo"))

        # Ensure they all exist
        for i in normalized_dtbos:
            if (i is None) or i not in overlays:
                c.message(
                    [f'Overlay "{i}" not found on system, refusing to continue.'],
                    "ERROR",
                )
                return

        # Match inputted dtbos
        for i in normalized_dtbos:
            matched_dtbos.append(
                utilities.match_filename(i, list(dtb_cache["overlays"].keys()))
            )

        # Ensure they all exist
        for i in matched_dtbos:
            if i is None:
                c.message(
                    [f'Overlay "{i}" failed to match, refusing to continue.'], "ERROR"
                )
                return

    # c.message(["Dtbos:"] + dtbos + ["", "Normalized:"] + normalized_dtbos + ["", "Matched:"] + matched_dtbos, "test")

    if grub:
        efidir = dt.detect_efidir()

        if not dt.uefi_overriden():
            if c.confirm(
                [
                    "IMPORTANT NOTICE --!!-- IMPORTART NOTICE",
                    "",
                    "Upon the next reboot UEFI setup is required!",
                    "",
                    "To enter into UEFI setup you can either:",
                    " - Hold down ESC",
                    "  or",
                    ' - Select "Enter UEFI Setup" from within the GRUB Bootloader',
                    "",
                    "In the UEFI setup menu you need to select:",
                    "",
                    "Device Manager > Rockchip Platform Configuration > ACPI / Device Tree",
                    "",
                    "And do the following:",
                    "",
                    ' - Set "Config Table Mode" to "Device Tree"',
                    ' - Change "Support DTB override & overlays" to "Enabled"',
                    "",
                    "Press Y to continue, or N to abort this operation.",
                ],
                "UEFI Setup required!",
            ):
                grubcfg = dt.parse_grub()

                # Fetch original base DTB
                base_dtb_normalized = normalize_filename(grubcfg["GRUB_DTB"], "dtb")
                base_dtb_path = utilities.match_filename(
                    base_dtb_normalized, list(dtb_cache["base"].keys())
                )
                if base_dtb_path is None:
                    c.message(["Failed to match DTB!"], "ERROR")
                    return

                cmds = [
                    ["mkdir", "-vp", efidir + "/dtb/base/", efidir + "/dtb/overlays/"],
                    ["cp", "-v", base_dtb_path, efidir + "/dtb/base/"],
                ]

                # Copy base DTB, changes here must be also be performed to set_base_dtb
                if normalized_dtb == "rk3588s-fydetab-duo.dtb":
                    cmds.append(
                        [
                            "cp",
                            "-v",
                            base_dtb_path,
                            efidir + "/dtb/base/rk3588s-tablet-12c-linux.dtb",
                        ]
                    )
                elif normalized_dtb == "rk3588-rock-5b-plus.dtb":
                    cmds.append(
                        [
                            "cp",
                            "-v",
                            base_dtb_path,
                            efidir + "/dtb/base/rk3588-rock-5bp.dtb",
                        ]
                    )
                cmds.append(["sync", efidir])

                mrunner(cmds, True, "Performing Changes", False)

                # Disable dtb in grub
                del grubcfg["GRUB_DTB"]
                grubcfg = dt.encode_grub(grubcfg)

                if not DRYRUN:
                    c.elevated_file_write("/etc/default/grub", grubcfg)
                else:
                    c.message(
                        [
                            "The GRUB config would have been updated with the following:",
                            "",
                            grubcfg,
                        ],
                        "DRYRUN Simulated Output",
                    )

                runner(
                    ["grub-mkconfig", "-o", "/boot/grub/grub.cfg"],
                    True,
                    "Update GRUB Configuration",
                )
            else:
                c.message(["Cannot continue, returning."], "ABORTED")
                return

        # Remove all current DTBOs
        listing = utilities.ls(efidir + "/dtb/overlays/")
        listing_str = " ".join(str(p) for p in listing)

        cmds = [["rm", "-v", listing_str]]

        for dtbo in matched_dtbos:
            cmds.append(["cp", "-v", dtbo, efidir + "/dtb/overlays/"])

        mrunner(cmds, True, "Updating Overlays", False)

    if ext:
        extcfg = dt.parse_uboot()

        if dtbos:
            for i in range(len(dtbos)):
                dtbos[i] = normalize_filename(i, "dtbo")
                dtbo_path = utilities.match_filename(
                    dtbo[i], list(dtb_cache["overlays"].keys())
                )
                if dtbo_path is None:
                    c.message(
                        [
                            f'Overlay "{dtbo[i]}" not found on system, refusing to continue.'
                        ],
                        "ERROR",
                    )
                    return

            extcfg["U_BOOT_FDT_OVERLAYS"] = " ".join(dtbos)
        else:
            if "U_BOOT_FDT_OVERLAYS" in extcfg.keys():
                del extcfg["U_BOOT_FDT_OVERLAYS"]

        extcfg = dt.encode_uboot(extcfg)

        if not DRYRUN:
            c.elevated_file_write("/etc/default/u-boot", extcfg)
        else:
            c.message(
                [
                    "The u-boot config would have been updated with the following:",
                    "",
                    extcfg,
                ],
                "DRYRUN Simulated Output",
            )

        runner(
            ["u-boot-update"],
            True,
            "Trigger U-Boot Update",
        )


# -------- ACTIVATABLE COMMANDS ---------


def filesystem_maint() -> None:
    cmd = [
        "sh",
        "-c",
        'findmnt -n -o FSTYPE / | grep -q btrfs && echo "Detected BTRFS root, performing balance operation." && btrfs balance start -dusage=20 -musage=20 /',
    ]
    if c.confirm(
        [
            "This will perform a BTRFS balance operation.",
            "",
            "Authentication will be required, do you wish to continue?",
        ],
        "Filesystem Maintenance",
    ):
        runner(cmd, True, "Filesystem Maintenance")


def filesystem_check() -> None:
    cmd = [
        "sh",
        "-c",
        'findmnt -n -o FSTYPE / | grep -q btrfs && echo "Detected BTRFS root, performing scrub." && btrfs scrub start -Bd /',
    ]
    if c.confirm(
        [
            "This will perform a full BTRFS scrub operation.",
            "Cancelling is not adviced.",
            "",
            "Save your work before continuing.",
            "",
            "ARE YOU SURE YOU WANT TO CONTINUE?",
        ],
        "Filesystem Check",
    ):
        runner(cmd, True, "Filesystem Check")


def filesystem_resize() -> None:
    cmd = [
        "sh",
        "-c",
        'systemctl enable resizefs && echo "The filesystem will be expanded upon the next reboot!"',
    ]
    if c.confirm(
        [
            "This enables a service present and used upon the first boot of the device.",
            "This is a safe operation.",
            "",
            "Despite this I have the undying urge to pester you. Are you a loyal bred?",
        ],
        "Pancake spirit",
    ):
        runner(cmd, True, "Filesystem Resize")
    else:
        c.message(["This incident will be reported to Santa Claus."], "Pancake spirit")


def uboot_migrator() -> bool:
    if (not dt.extlinux_exists()) or dt.booted_with_edk():
        return True  # UEFI system

    installed = dt.safe_exists("/usr/bin/u-boot-update")
    if not installed:
        res = c.confirm(
            [
                "Migrating to u-boot-update is required!",
                "",
                "Please make sure a system backup is available.",
                "Press Y to continue, N to abort.",
            ],
            "U-Boot-Update Migrator",
        )
        if not res:
            return False

        runner(
            [
                "sh",
                "-c",
                '"pacman -Sy && pacman -S --noconfirm u-boot-update',
            ],
            True,
            "Installing U-Boot Updater",
            prompt=False,
        )

    extcfg = dt.parse_uboot()  # Will load defaults if not found
    if extcfg["U_BOOT_IS_SETUP"] == "false":
        oldextcfg = dt.parse_extlinux_conf(
            Path("/boot/extlinux/extlinux.conf").read_text()
        )

        labels = oldextcfg["labels"]
        _, label_data = next(iter(labels.items()))

        extcfg["U_BOOT_IS_SETUP"] = "true"
        extcfg["U_BOOT_MENU_LABEL"] = "BredOS"
        extcfg["U_BOOT_COPY_DTB_TO_BOOT"] = "true"

        if "append" in label_data:
            params = label_data["append"]
            extcfg["U_BOOT_PARAMETERS"] = re.sub(r"\s*root=[^\s]+", "", params).strip()

        if "fdt" in label_data:
            dtb = label_data["fdt"]
            extcfg["U_BOOT_FDT"] = dtb[dtb.rfind("/") + 1 :]
        else:
            if "U_BOOT_FDT" in extcfg:
                del extcfg["U_BOOT_FDT"]

        if "fdtoverlays" in label_data:
            dtbos = label_data["fdtoverlays"].split()
            extcfg["U_BOOT_FDT_OVERLAYS"] = " ".join(
                i.rsplit("/", 1)[-1] for i in dtbos
            )

        extcfg = dt.encode_uboot(extcfg)

        if not DRYRUN:
            c.elevated_file_write("/etc/default/u-boot", extcfg)
        else:
            c.message(
                [
                    "The U-Boot config would have been updated with the following:",
                    "",
                    extcfg,
                ],
                "DRYRUN Simulated Output",
            )

        runner(
            ["u-boot-update"],
            True,
            "Trigger U-Boot Update",
        )

        c.message(["Migration complete!"], "U-Boot-Update Migrator")
    return True


def dt_manager(cmd: list = []) -> None:
    c.message(["Please wait.."], "Generating Device Tree Caches", False)

    migrated = uboot_migrator()
    if not migrated:
        return

    dts = dt.gencache()
    if not dts["base"]:
        c.message(["No Device Trees were detected!"], "Device Tree Manager", True)
        return

    if c.stdscr is None:
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
                base, overlays = dt.detect_live()
                print(f"Base: {base} (detected)\n\nOverlay-like entries (diffs):")
                for line in overlays:
                    print("  +", line)
            elif cmd[0] == "base":
                if len(cmd) - 1:
                    set_base_dtb(cmd[1])
                else:
                    print("\nLive System Tree:")
                    base, overlays = dt.detect_live()
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

    res = c.confirm(
        [
            "This command is only for advanced users!",
            "",
            "Only continue if you know EXACTLY what you're doing.",
        ],
        "Device Tree Management",
    )

    if not res:
        return

    options = [
        "Set the Base Device Tree",
        "Enable / Disable Overlays",
        "View Currently Enabled Trees",
        "Main Menu",
    ]

    while True:
        selection = c.draw_menu("Device Tree Manager", options)
        if selection is None or options[selection] == "Main Menu":
            return

        c.stdscr.clear()
        c.stdscr.refresh()
        if options[selection] == "Set the Base Device Tree":
            maxnl = max(len(v["name"]) for v in dts["base"].values())
            maxde = max(
                len(v["description"] if v["description"] is not None else [])
                for v in dts["base"].values()
            )
            maxco = max(len(",".join(v["compatible"])) for v in dts["base"].values())

            basedt = []
            matchdt = []
            preselect = -1
            live, _ = dt.detect_live()
            if live:
                live = str(live)
                live = live[live.rfind("/") + 1 : live.rfind(".")]

            for tree in dts["base"].keys():
                base = dts["base"][tree]
                name = base["name"]

                compat = base["compatible"]
                if compat:
                    compat_str = '"' + '","'.join(compat) + '"'
                else:
                    compat_str = ""

                basedt.append(f"{name.ljust(maxnl)} | {compat_str}")
                if name == live:
                    preselect = len(matchdt)
                matchdt.append(tree)

            res = c.selector(basedt, False, "Select a device Tree", preselect=preselect)

            if res is not None:
                set_base_dtb(matchdt[res])

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
            live = dt.identify_overlays()
            preselect = []

            for i in range(len(live)):
                live[i] = str(live[i])
                live[i] = live[i][live[i].rfind("/") + 1 : live[i].rfind(".")]

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
                if name in live:
                    preselect.append(len(matchdt))
                matchdt.append(tree)

            res = c.selector(basedt, True, "Select overlays", preselect=preselect)

            if res:
                dtbos = []
                for i in res:
                    dtbos.append(matchdt[i])

                set_overlays(dtbos)

        if options[selection] == "View Currently Enabled Trees":
            pass


def hack_pipewire() -> None:
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
        if c.confirm(["Remove the hack?"], "Pipewire CPU Fix") and not DRYRUN:
            service_path.unlink()
    else:
        if c.confirm(["Apply the hack?"], "Pipewire CPU Fix") and not DRYRUN:
            service_path.parent.mkdir(parents=True, exist_ok=True)
            service_path.write_text(service_content)
        res = True

    c.message(
        [
            "Pipewire CPU Fix " + ("applied" if res else "removed") + ".",
            "Relog or Reboot to apply.",
        ],
        "Pipewire CPU Fix",
    )


def hack_wol() -> None:
    cmd = [
        "bash",
        "-c",
        'pacman -Qi bredos-wol &>/dev/null && echo "Removing.." && pacman -R --noconfirm bredos-wol || { echo "Installing.."; pacman -Sy; pacman -S --noconfirm bredos-wol; }',
    ]
    if c.confirm(["Toggle the Wake-On-Lan hack?"], "Wake On Lan"):
        runner(cmd, True, "Wake On Lan")


def pacman_integrity() -> None:
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
    runner(cmd, False, "Check Packages Integrity")


def install_recommends() -> None:
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
    if c.confirm(
        [
            "This will install the following packages:",
            "",
            " - webcord-bin",
            " - ayugram-desktop",
            " - thunderbird",
            " - gnome-disk-utility",
            " - mpv",
            " - libreoffice-fresh",
            " - timeshift",
            " - proton-run",
            " - evince",
            " - loupe",
            "",
            "Are you sure you wish to continue?",
        ],
        "Install Recommended Packages",
    ):
        runner(cmd, True, "Install Recommended Packages")


def install_docker() -> None:
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
    if c.confirm(
        [
            "This will install AND ENABLE the following packages:",
            "",
            " - docker",
            " - docker-buildx",
            " - docker-compose",
            " - docker-compose",
            " - pigz",
            "",
            "Are you sure you wish to continue?",
        ],
        "Install Docker",
    ):
        runner(cmd, True, "Install Docker")


def install_steam_any() -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed steam steam-libs-any",
    ]
    if c.confirm(
        [
            "This will install Steam for ARM, for mainline mesa systems.",
            "",
            "Should only be used on RK3588 if you've switched to Panthor graphics.",
            "Are you sure you wish to continue?",
        ]
    ):
        runner(cmd, True, "Install Steam (Any)")


def install_steam_panfork() -> None:
    cmd = [
        "sh",
        "-c",
        "pacman -Sy && pacman -S --noconfirm --needed steam steam-libs-rk3588",
    ]
    if c.confirm(
        [
            "This will install Steam for ARM, suitable for RK3588 systems with Panfork graphics.",
            "",
            "Panfork is the default BredOS video driver.",
            "Are you sure you wish to continue?",
        ]
    ):
        runner(cmd, True, "Install Steam (RK3588, Panfork graphics)")


def install_development() -> None:
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
    if c.confirm(
        [
            "This will install the following packages:",
            "",
            " - python-prettytable",
            " - grub",
            " - parted",
            " - gptfdisk",
            " - edk2-rk3588-devel",
            " - dtc",
            " - xmlto",
            " - docbook-xsl",
            " - kmod",
            " - bc",
            " - uboot-tools",
            " - vboot-utils",
            " - bredos-tools",
            "",
            "Are you sure you wish to continue?",
        ],
        "Install BredOS Development Packages",
    ):
        runner(cmd, True, "Install BredOS Development Packages")


def unlock_pacman() -> None:
    cmd = [
        "bash",
        "-c",
        '[ -f /var/lib/pacman/db.lck ] && ! pgrep -x pacman >/dev/null && { sudo rm -f /var/lib/pacman/db.lck && echo "Pacman DB lock removed."; } || echo "No action needed."',
    ]
    elevate = True
    runner(cmd, True, "Unlock Pacman Database")


def autoremove() -> None:
    cmd = [
        "bash",
        "-c",
        "while pacman -Qdtq >/dev/null 2>&1; do sudo pacman -Rns --noconfirm $(pacman -Qdtq); done",
    ]
    elevate = True
    if c.confirm(
        [
            "This will REMOVE ALL PACKAGES that aren't:",
            " - Depended upon by another package",
            "  AND",
            " - Haven't been installed manually.",
            "",
            "Are you SURE you want this?",
        ],
        "Remove Unused Packages",
    ):
        runner(cmd, True, "Remove Unused Packages")


def sys_health_menu():
    options = [
        "Perform Filesystem Maintenance",
        "Check & Repair Filesystem",
        "Expand Fileystem",
        "Check Packages Integrity",
        "Manage Device Trees",
        "Main Menu",
    ]

    while True:
        selection = c.draw_menu("Filesystem", options)
        if selection is None or options[selection] == "Main Menu":
            return

        c.stdscr.clear()
        c.stdscr.refresh()
        if options[selection] == "Perform Filesystem Maintenance":
            filesystem_maint()
        if options[selection] == "Check & Repair Filesystem":
            filesystem_check()
        if options[selection] == "Expand Fileystem":
            filesystem_resize()
        if options[selection] == "Check Packages Integrity":
            pacman_integrity()
        if options[selection] == "Manage Device Trees":
            dt_manager()


def sys_tweaks_menu() -> None:
    options = ["Pipewire CPU fix", "Wake On Lan", "Main Menu"]

    while True:
        selection = c.draw_menu("System Tweaks", options)
        if selection is None or options[selection] == "Main Menu":
            return

        c.stdscr.clear()
        c.stdscr.refresh()
        if options[selection] == "Pipewire CPU fix":
            hack_pipewire()
        if options[selection] == "Wake On Lan":
            hack_wol()


def packages_menu() -> None:
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
        selection = c.draw_menu("Packages", options)
        if selection is None or options[selection] == "Main Menu":
            return

        c.stdscr.clear()
        c.stdscr.refresh()
        if options[selection] == "Install Recommended Desktop Packages":
            install_recommends()
        if options[selection] == "Install Docker":
            install_docker()
        if options[selection] == "Install Steam (Any)":
            install_steam_any()
        if options[selection] == "Install Steam (Panfork graphics)":
            install_steam_panfork()
        if options[selection] == "Install BredOS Development Packages":
            install_development()
        if options[selection] == "Unlock Pacman Database":
            unlock_pacman()
        if options[selection] == "Autoremove Unused packages":
            autoremove()
        if options[selection] == "Check Packages Integrity":
            pacman_integrity()


def main_menu():
    curses.start_color()
    curses.use_default_colors()
    try:
        curses.init_pair(1, 166, -1)
    except:
        try:
            curses.init_pair(1, curses.COLOR_RED, -1)
        except:
            pass
    c.stdscr.bkgd(" ", curses.color_pair(1))
    c.stdscr.clear()

    options = ["System Upkeep", "System Tweaks", "Packages", "Debug", "Exit"]

    while True:
        selection = c.draw_menu(c.APP_NAME, options)
        if selection is None or options[selection] == "Exit":
            return

        if options[selection] == "System Upkeep":
            sys_health_menu()
        if options[selection] == "System Tweaks":
            sys_tweaks_menu()
        if options[selection] == "Packages":
            packages_menu()
        if options[selection] == "Debug":
            debug_info()


def tui():
    c.resume()

    try:
        main_menu()
    finally:
        c.stdscr.clear()
        c.stdscr.keypad(False)
        curses.echo()
        curses.nocbreak()
        curses.endwin()


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
    parser = argparse.ArgumentParser(prog="bredos-config", description=c.APP_NAME)
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
        c.DRYRUN = True

    if check_root():
        ROOT_MODE = True

    if args.command is None:
        tui()
    else:
        dp(args)


if __name__ == "__main__":
    main()
