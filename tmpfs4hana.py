#!/usr/bin/env python3
"""
tmpfs4hana.py: A convenience script to recreate tmpfs filesystems for the SAP HANA
               Fast Restart Option on OS boot.
               The filesystems are created to match NUMA node topology.
"""

import argparse
import glob
import json
import os
import pwd
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from typing import List, Optional, Tuple

SCRIPT_NAME = os.path.basename(sys.argv[0])
SCRIPT_VERSION = "2.0"
MINIMUM_PYTHON_VERSION = (3, 6)

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

_verbose = False
_logfile_handle = None


def _log_line(msg: str) -> None:
    """Write a timestamped log line to stdout (and to the log file if open)."""
    line = f"{datetime.now().strftime('%Y%m%d.%H%M%S')} [{SCRIPT_NAME}:{os.getpid()}] {msg}"
    print(line)
    if _logfile_handle is not None:
        _logfile_handle.write(line + "\n")
        _logfile_handle.flush()


def log(msg: str) -> None:
    _log_line(msg)


def log_verbose(msg: str) -> None:
    if _verbose:
        _log_line(msg)


def log_error(msg: str) -> None:
    _log_line(f"ERROR: {msg}")


def log_command_line() -> None:
    """Log the complete command line including all parameters in verbose mode."""
    log_verbose(f"Command line: <{' '.join(sys.argv)}>")


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def verify_python_version() -> None:
    if sys.version_info < MINIMUM_PYTHON_VERSION:
        log_error(f"Python {MINIMUM_PYTHON_VERSION[0]}.{MINIMUM_PYTHON_VERSION[1]} "
                      f"or newer required, but found {sys.version_info.major}.{sys.version_info.minor}")
        sys.exit(2)
    else:
        log_verbose(f"Used Python version: {sys.version_info.major}.{sys.version_info.minor}")


def verify_permissions() -> None:
    if os.geteuid() != 0:
        log_error("This script must be run as root")
        sys.exit(3)


def verify_dependencies(*cmds: str) -> None:
    verify_python_version()

    for cmd in cmds:
        result = subprocess.run(
            ["which", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            log_error(f"Required dependency {cmd} not found")
            sys.exit(1)


def verify_file_permissions(path: str) -> None:
    if not os.access(path, os.R_OK | os.W_OK):
        log_error(f"{path} is not readable/writable")
        sys.exit(2)


def verify_json(path: str) -> None:
    try:
        with open(path) as fh:
            json.load(fh)
    except json.JSONDecodeError as exc:
        log_error(f"{path} is not a valid JSON file: {exc}")
        sys.exit(2)


def verify_user_exists(sid: str) -> None:
    """Check if the SAP HANA admin user <sid>adm exists on the system."""
    user = f"{sid.lower()}adm"
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        log_error(f"User '{user}' does not exist")
        sys.exit(1)
    except Exception as exc:
        log_error(f"Error checking user '{user}': {exc}")
        sys.exit(1)
    log_verbose(f"User: '{user}' exists (uid: {entry.pw_uid} gid: {entry.pw_gid})")

# ---------------------------------------------------------------------------
# Retrieve user information helper
# ---------------------------------------------------------------------------

def retrieve_owner_id(sid: str) -> int:
    """Return the UID of the SAP HANA admin user <sid>adm."""
    user = f"{sid.lower()}adm"
    try:
        return pwd.getpwnam(user).pw_uid
    except KeyError:
        log_error(f"User '{user}' does not exist")
        sys.exit(1)
    except Exception as exc:
        log_error(f"Error retrieving UID for user '{user}': {exc}")
        sys.exit(1)


def retrieve_group_id(sid: str) -> int:
    """Return the primary GID of the SAP HANA admin user <sid>adm."""
    user = f"{sid.lower()}adm"
    try:
        return pwd.getpwnam(user).pw_gid
    except KeyError:
        log_error(f"User '{user}' does not exist")
        sys.exit(1)
    except Exception as exc:
        log_error(f"Error retrieving GID for user '{user}': {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Command execution helper
# ---------------------------------------------------------------------------

def run_command_exit_on_error(cmd: List[str]) -> None:
    """Run *cmd* and exit with its return-code on failure."""
    log_verbose(f"Execute: '{' '.join(cmd)}'")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        log_error(f"rc={result.returncode}, cmd='{' '.join(cmd)}'")
        sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def get_cfg_info(cfgfile: str) -> Tuple[str, str]:
    """Return (sid, mntparent) from the first entry in the JSON config file."""
    with open(cfgfile) as fh:
        instances = json.load(fh)

    for instance in instances:
        sid = instance.get("sid", "").strip()
        if not sid:
            log_error(f"'sid' not found in {cfgfile}")
            sys.exit(4)
        mntparent = instance.get("mntparent", "").strip()
        if not mntparent:
            log_error(f"'mntparent' not found in {cfgfile}")
            sys.exit(4)
        # Only the first entry is used (matching the original shell behaviour)
        return sid, mntparent

    log_error(f"No entries found in {cfgfile}")
    sys.exit(4)


def retrieve_tmpfs_entries(
    filepath: str, sid: str, mntparent: str
) -> Tuple[List[str], List[int], List[str]]:
    """
    Parse *filepath* and return (mount-points, preferred NUMA nodes, mount
    options) for tmpfs entries that match *mntparent* and *sid*.
    Node value is -1 when no 'prefer' option is found.
    """
    log(f"tmpfs entries of '{filepath}' for '{sid}':")
    log(
        "Name         Mountpoint                                       "
        "Type     Options                          Node"
    )
    log(
        "------------ ------------------------------------------------ "
        "-------- -------------------------------- ----"
    )

    tmpfs_mnts: List[str] = []
    tmpfs_nodes: List[int] = []
    tmpfs_opts: List[str] = []

    try:
        with open(filepath) as fh:
            lines = fh.readlines()
    except OSError as exc:
        log_error(f"Cannot read '{filepath}': {exc}")
        sys.exit(1)

    for line in lines:
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        mntname, mntpoint, mnttype, mntopts = parts[0], parts[1], parts[2], parts[3]

        if mntparent not in mntpoint:
            continue
        if mnttype != "tmpfs":
            continue
        if os.path.basename(mntpoint.rstrip("/")).upper() != sid.upper():
            continue

        node = -1
        prefer_match = re.search(r"prefer:(\d+)", mntopts)
        if prefer_match:
            node = int(prefer_match.group(1))

        log(
            f"{mntname:<12} {mntpoint:<48} {mnttype:<8} {mntopts:<32} {node:<4}"
        )

        tmpfs_nodes.append(node)
        tmpfs_mnts.append(mntpoint)
        tmpfs_opts.append(mntopts)

    if not tmpfs_mnts:
        log("No mounts found")

    return tmpfs_mnts, tmpfs_nodes, tmpfs_opts


def get_numa_nodes() -> List[int]:
    """Return a sorted list of NUMA node numbers that have memory."""
    log("LPAR Topology ->  Node Memory")
    log("                  ---- ------")

    numa_nodes: List[int] = []

    def _node_key(path: str) -> int:
        try:
            return int(path.split("node")[-1])
        except ValueError:
            return -1

    node_paths = sorted(
        glob.glob("/sys/devices/system/node/node*"),
        key=_node_key,
    )

    for nodepath in node_paths:
        node_str = nodepath.split("node")[-1]
        try:
            node = int(node_str)
        except ValueError:
            continue

        has_memory = bool(glob.glob(os.path.join(nodepath, "memory[0-9]*")))
        if has_memory:
            numa_nodes.append(node)
            log(f"                  {node:3d}    Y")
        else:
            log(f"                  {node:3d}    N")

    return numa_nodes


def retrieve_tmpfs_entries_to_remove(
    tmpfs_nodes: List[int], numa_nodes: List[int]
) -> Tuple[List[int], List[int]]:
    """Return (remove_indices, retain_indices) based on NUMA topology."""
    remove_idx: List[int] = []
    retain_idx: List[int] = []

    for idx, node in enumerate(tmpfs_nodes):
        if node < 0 or node not in numa_nodes:
            remove_idx.append(idx)
        else:
            retain_idx.append(idx)

    return remove_idx, retain_idx


def add_tmpfs_entries_without_nofail(
    remove_idx: List[int], retain_idx: List[int], tmpfs_opts: List[str]
) -> Tuple[List[int], List[int]]:
    """
    Move every retained entry whose mount options lack 'nofail' to the remove
    list. Return the updated (remove_indices, retain_indices).
    """
    new_remove = list(remove_idx)
    new_retain: List[int] = []

    for idx in retain_idx:
        if "nofail" in tmpfs_opts[idx].split(","):
            new_retain.append(idx)
        else:
            new_remove.append(idx)

    return sorted(new_remove), new_retain


def get_tmpfs_mounts_to_create(
    tmpfs_nodes: List[int], numa_nodes: List[int]
) -> List[int]:
    """Return NUMA node numbers for which a tmpfs mount needs to be created."""
    return [node for node in numa_nodes if node not in tmpfs_nodes]


def tmpfs_entries_differ(
    tmpfs_nodes_mnts: List[int],
    tmpfs_nodes_fstab: List[int]
) -> bool:
    """
    Return True if the entries between mounts and fstab differ, otherwise False.
    """
    if sorted(tmpfs_nodes_mnts) != sorted(tmpfs_nodes_fstab):
        log(f"Entries in '/proc/mounts' and '/etc/fstab' differ")
        return True
    else:
        return False


def tmpfs_entries_to_remove(
    remove_idx_mnts: List[int],
    remove_idx_fstab: List[int],
) -> bool:
    """
    Return True if remove_idx_mnts or remove_idx_fstab contains entries,
    otherwise False.
    """
    if len(remove_idx_mnts) > 0 or len(remove_idx_fstab) > 0:
        return True
    return False


def update_tmpfs_mounts(
    sid: str,
    mntparent: str,
    tmpfs_create: List[int],
    simulate: bool,
) -> str:
    """
    Create tmpfs mounts in '/proc/mounts' and tmpfs entries in '/etc/fstab' for
    each node in *tmpfs_create*. Returns a semicolon-separated list of created
    mount-points.
    """
    sid_uc = sid.upper()
    filesystems = ""

    owner_id = retrieve_owner_id(sid)
    group_id= retrieve_group_id(sid)
    mnt_entries_fstab = []

    # SAP HANA uses the following format for the mount path: '<mntparent><numa_node>/<SID>', e.g.
    # '/hana/tmpfs99/OQP'. And the SAP HANA writes entries to '/etc/fstab' like the following:
    # 'tmpfsOQP99 /hana/tmpfs99/OQP tmpfs rw,relatime,mpol=prefer:99,uid=1000,gid=79 0 0'
    for node in tmpfs_create:
        mntdir = os.path.join((mntparent + str(node)), f"{sid_uc}")
        fstab_entry = (
            f"tmpfs{sid_uc}{node} {mntdir} tmpfs "
            f"rw,relatime,mpol=prefer:{node},uid={owner_id},gid={group_id},nofail 0 0"
        )
        mnt_entries_fstab.append(fstab_entry)

        if simulate:
            log_verbose(f"Execute (simulate): 'mkdir -p {mntdir}'")
            log_verbose(
                f"Execute (simulate): 'mount tmpfs{sid_uc}{node} -t tmpfs -o mpol=prefer:{node} {mntdir}'"
            )
            log_verbose(f"Execute (simulate): 'chown -R {owner_id}:{group_id} {mntdir}'")
            log_verbose(f"Execute (simulate): 'chmod 777 -R {mntdir}'")
        else:
            run_command_exit_on_error(["mkdir", "-p", mntdir])
            run_command_exit_on_error(
                [
                    "mount",
                    f"tmpfs{sid_uc}{node}",
                    "-t", "tmpfs",
                    "-o", f"mpol=prefer:{node}",
                    mntdir,
                ]
            )
            run_command_exit_on_error(["chown", "-R", f"{owner_id}:{group_id}", mntdir])
            run_command_exit_on_error(["chmod", "777", "-R", mntdir])

        filesystems += f"{mntdir};"

    update_tmpfs_fstab(None, mnt_entries_fstab, simulate=simulate)

    return filesystems


def update_tmpfs_fstab(
    entries_to_remove: Optional[List[str]] = None,
    entries_to_add: Optional[List[str]] = None,
    fstab_path: str = "/etc/fstab",
    simulate: bool = False,
) -> None:
    """
    Read *fstab_path*, remove entries matching *entries_to_remove*, and add
    *entries_to_add*. Retain entries not in either list. Write updates to a
    temporary file in the same directory and atomically replace *fstab_path*
    using os.replace().
    """
    remove_items = [e.strip() for e in (entries_to_remove or []) if e.strip()]
    add_items = [e.strip() for e in (entries_to_add or []) if e.strip()]

    if not (remove_items or add_items):
        log_verbose(f"No updates to {fstab_path}")
        return

    try:
        with open(fstab_path) as fh:
            lines = fh.readlines()
    except OSError as exc:
        log_error(f"Cannot read {fstab_path}: {exc}")
        return

    new_lines = []
    removed_any = False

    for line in lines:
        stripped = line.strip()
        should_remove = False

        if bool(re.match(rf"^#\s*Updated by\s+{re.escape(SCRIPT_NAME)}\s+", stripped)):
            should_remove = True
        elif stripped and not stripped.startswith("#"):
            parts = stripped.split()
            for r in remove_items:
                if stripped == r:
                    should_remove = True
                    break
                if len(parts) >= 2 and r in (parts[0], parts[1]):
                    should_remove = True
                    break
            for a in add_items:
                if stripped == a:
                    should_remove = True
                    break
                if len(parts) >= 2 and len(a.split()) >= 2 and parts[1] == a.split()[1]:
                    should_remove = True
                    break

        if should_remove:
            if simulate:
                log_verbose(f"Remove (simulate): '{stripped}' from '{fstab_path}'")
            else:
                log_verbose(f"Remove: '{stripped}' from '{fstab_path}'")
            removed_any = True
            continue

        new_lines.append(line)

    added_any = False
    if add_items:
        added_any = True
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        comment = f"# Updated by {SCRIPT_NAME} on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        new_lines.append(comment)
        for entry in add_items:
            if simulate:
                log_verbose(f"Add (simulate): '{entry}' to '{fstab_path}'")
            else:
                log_verbose(f"Add: '{entry}' to '{fstab_path}'")
            new_lines.append(entry + "\n")

    if not (removed_any or added_any):
        log_verbose(f"No updates to {fstab_path}")
        return

    if simulate:
        log_verbose(f"Updating (simulate): '{fstab_path}'")
        return

    fstab_dir = os.path.dirname(os.path.abspath(fstab_path))
    try:
        fstab_mode = os.stat(fstab_path).st_mode & 0o777
    except OSError:
        fstab_mode = 0o644

    temp_file = tempfile.NamedTemporaryFile(mode="w", dir=fstab_dir, delete=False)
    try:
        temp_file.writelines(new_lines)
        temp_file.flush()
        temp_file.close()
        os.chmod(temp_file.name, fstab_mode)
        os.replace(temp_file.name, fstab_path)
        log(f"{fstab_path} updated")
    except Exception as exc:
        if os.path.exists(temp_file.name):
            os.unlink(temp_file.name)
        log_error(f"Cannot update {fstab_path}: {exc}")
        sys.exit(1)


def remove_tmpfs_all_mnts(
    tmpfs_mnts: List[str],
    simulate: bool,
    entries_to_remove: Optional[List[str]] = None,
    entries_to_add: Optional[List[str]] = None,
    fstab_path: str = "/etc/fstab",
) -> None:
    """Unmount every mount-point in *tmpfs_mnts*."""
    for mnt in tmpfs_mnts:
        if simulate:
            log_verbose(f"Execute (simulate): 'umount {mnt}'")
        else:
            run_command_exit_on_error(["umount", mnt])

    update_tmpfs_fstab(
        entries_to_remove=entries_to_remove,
        entries_to_add=entries_to_add,
        fstab_path=fstab_path,
        simulate=simulate,
    )


def update_hana_cfg(sid: str, tmpfs_filesystems: str, simulate: bool) -> None:
    """Update basepath_persistent_memory_volumes in the HANA global.ini."""
    sid_uc = sid.upper()
    config_file = f"/usr/sap/{sid_uc}/SYS/global/hdb/custom/config/global.ini"
    param = "basepath_persistent_memory_volumes"

    if not os.path.isfile(config_file):
        log_error(f"{config_file} does not exist")
        sys.exit(1)

    with open(config_file) as fh:
        content = fh.read()

    has_active_param = bool(
        re.search(rf"^[ \t]*{re.escape(param)}[ \t]*=.*$", content, flags=re.MULTILINE)
    )

    if not has_active_param:
        log(f"Parameter '{param}' does not exist in '{config_file}' and will be added")
        if re.search(r"^[ \t]*\[persistence\][ \t]*$", content, flags=re.MULTILINE | re.IGNORECASE):
            if re.search(r"^[ \t]*\[persistence\][ \t]*\n", content, flags=re.MULTILINE | re.IGNORECASE):
                new_content = re.sub(
                    r"(^[ \t]*\[persistence\][ \t]*\n)",
                    rf"\g<1>{param}={tmpfs_filesystems}\n",
                    content,
                    count=1,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
            else:
                new_content = re.sub(
                    r"(^[ \t]*\[persistence\][ \t]*$)",
                    rf"\g<1>\n{param}={tmpfs_filesystems}\n",
                    content,
                    count=1,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
        else:
            prefix = "\n" if content and not content.endswith("\n") else ""
            new_content = content + f"{prefix}[persistence]\n{param}={tmpfs_filesystems}\n"
    else:
        new_content = re.sub(
            rf"^[ \t]*{re.escape(param)}[ \t]*=.*$",
            f"{param}={tmpfs_filesystems}",
            content,
            flags=re.MULTILINE,
        )

    if new_content != content:
        if simulate:
            log(f"HANA configuration file '{config_file}' updated (simulate)")
        else:
            with open(config_file, "w") as fh:
                fh.write(new_content)
            log(f"HANA configuration file '{config_file}' updated")
    else:
        log(f"Parameter '{param}' in '{config_file}' needs no update")


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Recreate tmpfs filesystems for the SAP HANA Fast Restart Option on OS boot.\n"
            "Filesystems are created to match NUMA node topology."
        ),
        epilog=(
            "Examples\n"
            "  List currently mounted tmpfs filesystems matching SID in config file\n"
            "  and actions (if any) necessary to be done:\n"
            f"    $ {SCRIPT_NAME} -c /tmp/tmpfs4hana.cfg\n\n"
            "  Delete and create tmpfs filesystems to match topology and update HANA config:\n"
            f"    $ {SCRIPT_NAME} -c /tmp/tmpfs4hana.cfg -u"
        ),
    )
    parser.add_argument("-c", metavar="<file>", dest="cfgfile", default=None,
                        help="Full path configuration file")
    parser.add_argument("-l", metavar="<file>", dest="logfile",
                        default=f"/tmp/{os.path.splitext(SCRIPT_NAME)[0]}.log",
                        help="Full path log file")
    parser.add_argument("-m", dest="mounts", action="store_true",
                        help="Delete/create tmpfs filesystems to match NUMA topology.")
    parser.add_argument("-u", dest="update", action="store_true",
                        help="Update HANA config file. Implies -m.")
    parser.add_argument("-r", dest="rebuild_fs", action="store_true",
                        help="Recreate filesystem(s) regardless of whether valid or not.")
    parser.add_argument("-s", dest="simulate", action="store_true",
                        help="Simulate. Inspect but do not perform actions. Implies -v.")
    parser.add_argument("-V", dest="version", action="store_true",
                        help="Print version")
    parser.add_argument("-v", dest="verbose", action="store_true",
                        help="Verbose messages")
    return parser


def main() -> None:
    global _verbose, _logfile_handle

    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        print(f"{SCRIPT_NAME}: version {SCRIPT_VERSION}")
        sys.exit(0)

    if not args.cfgfile:
        parser.error("-c must be specified")

    # Resolve implied flags
    if args.simulate:
        args.verbose = True
        args.update = True
    if args.update:
        args.mounts = True

    _verbose = args.verbose

    # Open log file (append, mirroring shell's `tee -a`)
    try:
        _logfile_handle = open(args.logfile, "a")
    except OSError as exc:
        print(f"WARNING: Cannot open log file {args.logfile}: {exc}", file=sys.stderr)

    log(f"= Start: {SCRIPT_NAME} version {SCRIPT_VERSION} ==========================================")
    log_command_line()
    verify_permissions()
    verify_dependencies()
    verify_file_permissions(args.cfgfile)
    verify_json(args.cfgfile)

    sid, mntparent = get_cfg_info(args.cfgfile)
    log_verbose(f"sid '{sid}'")
    log_verbose(f"mntparent: '{mntparent}'")

    verify_user_exists(sid)

    filepath = "/proc/mounts"
    tmpfs_mnts, tmpfs_nodes_mnts, _ = retrieve_tmpfs_entries(filepath, sid, mntparent)
    log_verbose(f"Existing mnts entries: ({filepath}): {tmpfs_mnts}")
    log_verbose(f"Existing preferred nodes: ({filepath}): {tmpfs_nodes_mnts}")

    filepath = "/etc/fstab"
    tmpfs_entries_fstab, tmpfs_nodes_fstab, tmpfs_opts_fstab = retrieve_tmpfs_entries(
        filepath, sid, mntparent
    )
    log_verbose(f"Existing mnts entries: ({filepath}): {tmpfs_entries_fstab}")
    log_verbose(f"Existing preferred nodes: ({filepath}): {tmpfs_nodes_fstab}")

    numa_nodes = get_numa_nodes()
    log_verbose(f"NUMA nodes with memory: {numa_nodes}")

    if not args.mounts:
        sys.exit(0)

    remove_idx_mnts, retain_idx_mnts = retrieve_tmpfs_entries_to_remove(tmpfs_nodes_mnts, numa_nodes)
    log_verbose(f"mounts idx to remove: {remove_idx_mnts}")
    log_verbose(f"mounts idx to retain: {retain_idx_mnts}")

    remove_idx_fstab, retain_idx_fstab = retrieve_tmpfs_entries_to_remove(tmpfs_nodes_fstab, numa_nodes)
    remove_idx_fstab, retain_idx_fstab = add_tmpfs_entries_without_nofail(
        remove_idx_fstab, retain_idx_fstab, tmpfs_opts_fstab
    )
    log_verbose(f"fstab entries idx to remove: {remove_idx_fstab}")
    log_verbose(f"fstab entries idx to retain: {retain_idx_fstab}")

    if (args.rebuild_fs or
        tmpfs_entries_differ(tmpfs_nodes_mnts, tmpfs_nodes_fstab) or
        tmpfs_entries_to_remove(remove_idx_mnts, remove_idx_fstab)):
        log("Force rebuild all tmpfs mounts")
        remove_tmpfs_all_mnts(tmpfs_mnts, args.simulate, tmpfs_entries_fstab)
        tmpfs_mnts = []
        tmpfs_nodes_mnts = []
        tmpfs_entries_fstab = []

    tmpfs_create = get_tmpfs_mounts_to_create(tmpfs_nodes_mnts, numa_nodes)
    log_verbose(f"Mounts to create: {tmpfs_create}")

    update_tmpfs_mounts(sid, mntparent, tmpfs_create, args.simulate)

    retrieve_tmpfs_entries("/proc/mounts", sid, mntparent)
    retrieve_tmpfs_entries("/etc/fstab", sid, mntparent)

    if args.update:
        tmpfs_filesystems = "".join([f"{mntparent}{node}/{sid.upper()};" for node in numa_nodes])
        update_hana_cfg(sid, tmpfs_filesystems, args.simulate)

    if _logfile_handle:
        _logfile_handle.close()

    sys.exit(0)


if __name__ == "__main__":
    main()
