# tmpfs-4-hana
A convenience script to recreate tmpfs filesystems for SAP HANA Fast Restart Option on OS boot to match NUMA node topology (if required)

## Usage
```
Usage: tmpfs4hana.sh -c <file> [-l <file>] [-n] [-m] [-u] [-r] [-s] [-V] [-v] [-h]
 OPTIONS
 ============  =========================================
 -c <file>     Full path configuration file
 -l <file>     Full path log file
 -n            Filesystem numbering by index. Default is by numa node number.
 -m            Delete/create tmpfs filesystems to match numa topology.
 -u            Update HANA config file. Implies -m.
 -r            Recreate filesystem. This options forces recreation of the filesystem(s) regardless of whether valid or not.
 -s            Simulate. Inspect but do not perform actions. Implies -v
 -V            Print version
 -v            Verbose messages
 -h            Help

Examples
    List the currently mounted tmpfs filesystems match SID in config file
      and actions if any necessary to be done.
      $ tmpfs4hana.sh -c /tmp/tmpfs4hana.cfg
    Delete and create tmpfs filesystems to match topology and update HANA config file.
      $ tmpfs4hana.sh -c /tmp/tmpfs4hana.cfg -u

      20210115.144000 [tmpfs4hana.sh:146491] = Start ==========================================
      20210115.144001 [tmpfs4hana.sh:146491] No mounts found
      20210115.144001 [tmpfs4hana.sh:146491] LPAR Topology ->  Node Memory
      20210115.144001 [tmpfs4hana.sh:146491]                   ---- ------
      20210115.144001 [tmpfs4hana.sh:146491]                     0    N
      20210115.144001 [tmpfs4hana.sh:146491]                     2    Y
      20210115.144001 [tmpfs4hana.sh:146491]                     3    Y
      20210115.144001 [tmpfs4hana.sh:146491] Name         Mountpoint                                       Type     Options                          Node
      20210115.144001 [tmpfs4hana.sh:146491] ------------ ------------------------------------------------ -------- -------------------------------- ----
      20210115.144001 [tmpfs4hana.sh:146491] tmpfsPOW2    /hana/tmpfs/tmpfs2                               tmpfs    rw,relatime,mpol=prefer:2        2
      20210115.144001 [tmpfs4hana.sh:146491] tmpfsPOW3    /hana/tmpfs/tmpfs3                               tmpfs    rw,relatime,mpol=prefer:3        3
      20210115.144001 [tmpfs4hana.sh:146491] HANA configuration file /usr/sap/POW/SYS/global/hdb/custom/config/global.ini updated

```

## Dependencies:
- jq

## Installation:
 1. Choose a location for the script and config file (referred to as /mountpoint/path/to below)
 2. Create /mountpoint/path/to/tmpfs4hana.cfg
```
   [
	   {
		   "sid": "JE6" 
		   ,"mntparent": "/hana/tmpfs"
	   }
   ]
```
 3. Create /etc/systemd/system/tmpfs_hana.service taking care of the NOTEs below
```
    [Unit]
    Description=Fast Restart SAP HANA Adjustment Script
    After=local-fs.target
    After=network.target
    After=remote-fs.target

    [Service]
    Type=oneshot
    # NOTE: Adjust the path to the startup script.
    ExecStart=/bin/sh -c "/usr/sap/tmpfs/tmpfs4hana.sh -m -u -c /usr/sap/tmpfs/tmpfs4hana.cfg -l /usr/sap/tmpfs/tmpfs4hana.log"

    [Install]
    WantedBy=multi-user.target    
```
 4. Start service now and on reboot
```
    systemctl start tmpfs4hana.service
    systemctl status tmpfs4hana.service
    systemctl enable tmpfs4hana.service
```

