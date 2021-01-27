#!/usr/bin/env bash
#
# tmpfs4hana.sh: A convenience script to recreate tmpfs filesystems for the SAP HANA Fast Restart Option on OS boot.
#                The filesystems are created to match NUMA node topology. 
#
# Dependencies:
# - jq
#

# Utils ################################################
function usage() {
    cat <<EOUSAGE >&2
$@
Usage: $NAME -c <file> [-l <file>] [-n] [-m] [-u] [-r] [-s] [-V] [-v] [-h] 
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
      $ $NAME -c /tmp/tmpfs4hana.cfg 
    Delete and create tmpfs filesystems to match topology and update HANA config file. 
      $ $NAME -c /tmp/tmpfs4hana.cfg -u

EOUSAGE
}

function log() {
    echo "$(date +%Y%m%d.%H%M%S) [$NAME:$$] $@"
}

function logVerbose() {
    if [[ $VERBOSE == true ]]; then
        log "$@"
    fi
}

function logError() {
    log "ERROR: $@"
}

verifyDependencies() {
    local cmd
    for cmd in "$@"
    do
        command -v $cmd >/dev/null 2>&1 || {
            logError "Required dependency $cmd not found"
            exit 1
        }
    done
}
verifyExists() {
    if [[ ! -r $1 || ! -w $1 ]]; then
        logError "$1 is not a readable/writable"
        exit 2
    fi
}
verifyJSON() {
    if jq -e . >/dev/null 2>&1 <<<"$1"; then
        logError "$1 is not a valid JSON file"
        exit 2
    fi
}
verifyPermissions() {
    if [[ $EUID -ne 0 ]]; then
        logError "This script must be run as root"
        exit 3
    fi
}

function runCommandExitOnError() {
    local cmd="$*"
    eval $cmd
    local -r rc=$?
    if [[ $rc != 0 ]]; then
        logError "Error: rc=$rc, cmd='$cmd'\n"
        exit $rc
    fi
}

# Funcs ################################################

function get_cfg_info() {
    jq -rc '.[]' $CFGFILE | while IFS='' read instance
    do
        sid=$(echo $instance | jq .sid | tr -d '"' )
        if [[ -z $sid ]]; then
            logError "'sid' not found in $CFGFILE"
            exit 4;
        fi
        mntparent=$(echo $instance | jq .mntparent | tr -d '"')
        if [[ -z $mntparent ]]; then
            logError "'mntparent' not found in $CFGFILE"
            exit 4;
        fi
    done
}

function get_tmpfs_mnts() {
    local node=-2

    log "Name         Mountpoint                                       Type     Options                          Node"
    log "------------ ------------------------------------------------ -------- -------------------------------- ----"
    grep $mntparent /proc/mounts | grep tmpfs | grep -i $sid | while IFS=' ' read -r mntname mntpoint mnttype mntopts rest
    do
        node=-1
        if [[ $mntopts == *"prefer"* ]]; then
            node=${mntopts##*prefer:}
            node=${node%%,*}
        fi

        printf -v pad %32s
	local tmntname=$mntname$pad
	local tmntpoint=$mntpoint$pad
	local tmnttype=$mnttype$pad
	local tmntopts=$mntopts$pad
	local tnode=$node$pad

        log "${tmntname:0:12} ${tmntpoint:0:48} ${tmnttype:0:8} ${tmntopts:0:32} ${tnode:0:4}"

        tmpfs_nodes+=($node)
        tmpfs_mnts+=($mntpoint)
    done
    if (( $node < -1 )); then
        log "No mounts found"
    fi
}

function get_numa_nodes() {
    log "LPAR Topology ->  Node Memory"
    log "                  ---- ------"
    local nodepath
    local node
    for nodepath in /sys/devices/system/node/node*
    do
        node=${nodepath##*\/node}
        printf -v tnode "%3d" $node
	if compgen -G "${nodepath}/memory*" > /dev/null; then
            numa_nodes+=($node)
            log "                  $tnode    Y"
        else
            log "                  $tnode    N"
        fi
    done
}

function get_tmpfs_mounts_to_remove() {
    local idx=0
    for node in "${tmpfs_nodes[@]}"
    do
        if (( $node < 0 )); then
            tmpfs_remove_idx+=($idx)
        else
            if [[ ! " ${numa_nodes[@]} " =~ " ${node} " ]]; then
                tmpfs_remove_idx+=($idx)
            else
                tmpfs_retain_idx+=($idx)
            fi
        fi
        ((idx++))
    done
}

function get_tmpfs_mounts_to_create() {
    local node
    for node in "${numa_nodes[@]}"
    do
        if [[ ! " ${tmpfs_nodes[@]} " =~ " ${node} " ]]; then
            tmpfs_create+=($node)
        fi
    done
}

function create_tmpfs_mounts() {

## TODO:
##   handle mnt name conflict
##
    local -r sidlc=${sid,,}
    local -r siduc=${sid^^}
    local node
    for node in "${tmpfs_create[@]}"
    do

        local cmd="mkdir -p $mntparent/tmpfs$node"
        logVerbose "Execute: $cmd"
        if [[ $SIMULATE == false ]]; then
            runCommandExitOnError $cmd
        fi

        cmd="mount tmpfs${siduc}${node} -t tmpfs -o mpol=prefer:${node} $mntparent/tmpfs$node"
        logVerbose "Execute: $cmd"
        if [[ $SIMULATE == false ]]; then
            runCommandExitOnError $cmd
        fi

        cmd="chown -R ${sidlc}adm:sapsys $mntparent/tmpfs$node"
        logVerbose "Execute: $cmd"
        if [[ $SIMULATE == false ]]; then
            runCommandExitOnError $cmd
        fi

        cmd="chmod 777 -R $mntparent/tmpfs$node"
        logVerbose "Execute: $cmd"
        if [[ $SIMULATE == false ]]; then
            runCommandExitOnError $cmd
        fi

        tmpfs_filesystems+="$mntparent/tmpfs$node;"

    done
}

function remove_tmpfs_all_mnts() {
    local mnt
    for mnt in "${tmpfs_mnts[@]}"
    do
        local cmd="umount $mnt"
        logVerbose "Execute: $cmd"
        if [[ $SIMULATE == false ]]; then
            runCommandExitOnError $cmd
        fi
    done
}

function update_hana_cfg() {
    local -r sid=${sid^^}
    local -r config_file="/usr/sap/$sid/SYS/global/hdb/custom/config/global.ini"
    local -r param="basepath_persistent_memory_volumes"
    if [[ ! -f $config_file ]]; then
        logError "$config_file does not exist"
        exit 1;
    fi
    grep $param $config_file > /dev/null 2>&1
    local -r rc=$?
    if [[ $rc != 0 ]]; then
        logError "$config_file does not contain a 'basepath_persistent_memory_volumes' property."
        exit 1;
    else
        if [[ $SIMULATE == false ]]; then
            runCommandExitOnError 'sed -i "s#^${param}.*\$#${param}=${tmpfs_filesystems}#g" $config_file'
            log "HANA configuration file $config_file updated"
        fi
    fi
}

# Main #################################################
NAME=$(basename $0)
VERSION="1.0"

shopt -s lastpipe

# Defaults
declare LOGFILE="/tmp/${NAME}.log"
declare CFGFILE=""
declare REBUILD_FS=false
declare FS_SIMPLE_NUMBERING=false
declare SIMULATE=false
declare VERBOSE=false
declare MOUNTS=false
declare UPDATE=false

while getopts ":hc:l:surnmvV" opt; do
    case $opt in
        c) 
          CFGFILE=$OPTARG
          ;;
        l) 
          LOGFILE=$OPTARG
          ;;
        r) 
          REBUILD_FS=true
          ;;
        s) 
          SIMULATE=true
          UPDATE=true
          VERBOSE=true
          ;;
        m) 
          MOUNTS=true
          ;;
        u) 
          MOUNTS=true
          UPDATE=true
          ;;
        v) 
          VERBOSE=true
          ;;
        n) 
          FS_SIMPLE_NUMBERING=true
          ;;
        V) echo "$NAME: version $VERSION" ; exit 0;;
        h) usage "Help" ; exit 0;;
        :) usage "Option -${OPTARG} requires an argument." ; exit 1 ;;
        \?) usage "Invalid option -${OPTARG}" ; exit 1;;
    esac
done
shift $((OPTIND-1))

exec &> >(tee -a "$LOGFILE")
exec 2>&1

if [[ -z $CFGFILE ]]; then
    usage "ERROR: -c must by specified" ; exit 1;
fi

log "= Start =========================================="
verifyPermissions 
verifyDependencies "jq"
verifyExists $CFGFILE
verifyJSON $CFGFILE
##TODO user exists check

declare sid
declare mntparent
get_cfg_info
logVerbose "sid $sid"
logVerbose "mntparent $mntparent"

##
## Not necessary.  On reboot tmpfs is gone.
## - But maybe for dynamic changes ?
##
declare -a tmpfs_mnts
declare -a tmpfs_nodes
get_tmpfs_mnts
logVerbose "Existing mnts ${tmpfs_mnts[*]}"
logVerbose "Existing preferred nodes ${tmpfs_nodes[*]}"

##
##
declare -a numa_nodes
get_numa_nodes
logVerbose "NUMA node with memory ${numa_nodes[*]}"

if [[ $MOUNTS == false ]]; then
    exit 0
fi

##
## Not necessary.  On reboot tmpfs is gone. 
## - But maybe for dynamic changes ?
##
declare -a tmpfs_remove_idx
declare -a tmpfs_retain_idx
get_tmpfs_mounts_to_remove
logVerbose "Mount idx to remove ${tmpfs_remove_idx[*]}"
logVerbose "Mount idx to retain ${tmpfs_retain_idx[*]}"

if [[ $REBUILD_FS == true ]]; then
    log "Force rebuild all tmpfs mounts"
    remove_tmpfs_all_mnts
    unset tmpfs_mnts
    unset tmpfs_nodes
fi

declare -a tmpfs_create
get_tmpfs_mounts_to_create
logVerbose "Mount to create ${tmpfs_create[*]}"

create_tmpfs_mounts

get_tmpfs_mnts

if [[ $UPDATE == true ]]; then
    update_hana_cfg
fi

exit 0
# EOF
