# tmpfs4hana

A Python utility to automatically create, manage, and recreate `tmpfs` filesystems for the SAP HANA Fast Restart Option (FRO), ensuring file systems align with the system's NUMA node memory topology.

---

## Short Introduction

The SAP HANA FRO leverages `tmpfs` in-memory file systems to preserve column store table data across database restarts, significantly reducing startup times. Because SAP HANA performance depends on NUMA affinity, `tmpfs` filesystems must match the underlying NUMA topology (using mount options like `mpol=prefer:<node>`).

[`tmpfs4hana.py`](tmpfs4hana.py) automates this lifecycle by:
- Inspecting active NUMA nodes with memory via `/sys/devices/system/node/`.
- Creating and mounting per-node `tmpfs` volumes at `<mntparent><node>/<SID>` (e.g., `/hana/tmpfs0/DBO`).
- Setting ownership to `<sid>adm:sapsys` (using resolved UID and GID) and permissions to `777`.
- Managing `/etc/fstab` while preserving existing entries and comments.
- Updating `basepath_persistent_memory_volumes` in the SAP HANA `global.ini` configuration file.

---

## Usage

```bash
tmpfs4hana.py -c <file> [-l <file>] [-m] [-u] [-r] [-s] [-V] [-v] [-h]
```

### Options

| Option | Argument | Description |
|---|---|---|
| `-c` | `<file>` | Full path to the JSON configuration file (**required**) |
| `-l` | `<file>` | Full path to the log file (default: `/tmp/tmpfs4hana.log`) |
| `-m` | — | Create and delete `tmpfs` filesystems to match NUMA topology |
| `-u` | — | Update SAP HANA `global.ini` (`basepath_persistent_memory_volumes`). Implies `-m` |
| `-r` | — | Force rebuild: recreate filesystem(s) regardless of whether existing mounts are valid |
| `-s` | — | Simulate: inspect and log all planned actions without modifying the system. Implies `-v` and `-u` |
| `-V` | — | Print script version and exit |
| `-v` | — | Enable verbose log messages |
| `-h`, `--help` | — | Display command-line help message and exit |

---

## Examples

### 1. Inspect Current State (Dry Run)
Inspect current NUMA topology, existing `/proc/mounts`, and `/etc/fstab` entries without applying any changes:

```bash
sudo ./tmpfs4hana.py -c /etc/tmpfs4hana.cfg -v
```

### 2. Simulate Mount Creation and Configuration Update
Preview all commands, `/etc/fstab` changes, and SAP HANA configuration updates:

```bash
sudo ./tmpfs4hana.py -c /etc/tmpfs4hana.cfg -s
```

### 3. Create Filesystems and Update SAP HANA Configuration
Mount required `tmpfs` filesystems, update `/etc/fstab`, and write paths to `global.ini`:

```bash
sudo ./tmpfs4hana.py -c /etc/tmpfs4hana.cfg -u
```

**Example Log Output:**

```text
20261022.120000 [tmpfs4hana.py:12345] = Start: tmpfs4hana.py version 2.0 ==========================================
20261022.120000 [tmpfs4hana.py:12345] LPAR Topology ->  Node Memory
20261022.120000 [tmpfs4hana.py:12345]                   ---- ------
20261022.120000 [tmpfs4hana.py:12345]                      0    Y
20261022.120000 [tmpfs4hana.py:12345]                      1    Y
20261022.120000 [tmpfs4hana.py:12345] NUMA nodes with memory: [0, 1]
20261022.120000 [tmpfs4hana.py:12345] tmpfs entries of '/proc/mounts' for 'DBO':
20261022.120000 [tmpfs4hana.py:12345] Name         Mountpoint                                       Type     Options                          Node
20261022.120000 [tmpfs4hana.py:12345] ------------ ------------------------------------------------ -------- -------------------------------- ----
20261022.120001 [tmpfs4hana.py:12345] /etc/fstab updated
20261022.120001 [tmpfs4hana.py:12345] HANA configuration file /usr/sap/DBO/SYS/global/hdb/custom/config/global.ini updated
```

### 4. Force Rebuild Filesystems
Unmount all existing `tmpfs` filesystems and recreate them cleanly:

```bash
sudo ./tmpfs4hana.py -c /etc/tmpfs4hana.cfg -u -r
```

---

## Dependencies

- **Python**: Python 3.6 or newer (`python3`).
- **Standard Library Modules**: Uses Python standard library only (`argparse`, `glob`, `json`, `os`, `pwd`, `re`, `subprocess`, `sys`, `tempfile`, `datetime`, `typing`). No external Python packages are required.
- **System Utilities**: `mkdir`, `mount`, `umount`, `chown`, `chmod`, `which`.
- **Privileges**: Root privileges (`sudo` / UID 0) are required for mount and file operations.
- **SAP User**: The SAP HANA administrator user `<sid>adm` (e.g. `dboadm`) must exist in `/etc/passwd`.

---

## Installation

### Step 1: Location for the Script and Configuration File

Install the script into a standard administrative binary directory and create a configuration directory:

```bash
# Create directory for the script and configuration file (if not already existing)
sudo mkdir -p /usr/sap/tmpfs

# Copy script and to the created directory
sudo cp tmpfs4hana.py /usr/sap/tmpfs/tmpfs4hana.py
sudo chmod 755 /usr/sap/tmpfs/tmpfs4hana.py
```

### Step 2: Example for a Valid and Filled Configuration File

Create the JSON configuration file at `/usr/sap/tmpfs/tmpfs4hana.cfg`:

```json
[
    {
        "sid": "DBO",
        "mntparent": "/hana/tmpfs"
    }
]
```

- **`sid`**: The SAP HANA System Identifier (3 characters, alphanumeric).
- **`mntparent`**: Base mount path where NUMA-specific directories will be created (e.g., `/hana/tmpfs` produces `/hana/tmpfs0/DBO`, `/hana/tmpfs1/DBO`, etc.).

### Step 3: How to Create a Systemd Service File for This Script

Create the unit file `/etc/systemd/system/tmpfs4hana.service`:

```ini
[Unit]
Description=Fast Restart SAP HANA tmpfs Mount Adjustment
After=local-fs.target
After=network.target
After=remote-fs.target
Before=sapinit.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /usr/sap/tmpfs/tmpfs4hana.py -m -u -c /usr/sap/tmpfs/tmpfs4hana.cfg -l /var/log/tmpfs4hana.log

[Install]
WantedBy=multi-user.target
```

### Step 4: How to Activate the Systemd Service File

Reload `systemd`, enable the service to start automatically on OS boot, and start it immediately:

```bash
# Reload systemd manager configuration
sudo systemctl daemon-reload

# Enable service to run at boot
sudo systemctl enable tmpfs4hana.service

# Run service immediately
sudo systemctl start tmpfs4hana.service

# Verify service status and logs
sudo systemctl status tmpfs4hana.service
```
