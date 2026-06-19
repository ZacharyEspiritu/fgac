#!/usr/bin/env bash
# Install + configure PostgreSQL on the database VM, over the transport.
#
# The database counterpart of orchestration/install/install_artifact_on_attacker.sh, split
# out of provisioning: orchestration/provision/provision_vms.sh creates a BARE database VM
# (no PostgreSQL) and writes a machine descriptor; this script consumes that
# descriptor and installs + configures PostgreSQL on the DB VM over the
# provider-agnostic transport (orchestration/util/_remote_transport.sh). Because it goes through
# the transport, it runs against GCP VMs (gcloud descriptor) or any SSH-reachable
# machine (ssh descriptor) alike — so the DB install is not tied to Google Cloud.
#
# What it installs (the same steps the DB VM's cloud-init startup used to do):
#   * PostgreSQL 18 from the PGDG apt repo (+ contrib)
#   * listen_addresses='*', shared_preload_libraries='pg_stat_statements',
#     max_connections=1024 (headroom for the Table 1 noise clients)
#   * a pg_hba host line allowing DB_ALLOW_CIDR (so the attacker can connect)
#   * sets the postgres superuser password
# Needs sudo + apt on the DB host (Debian/Ubuntu) and SSH access to it (the
# gcloud descriptor's db.vm/db.zone, or the ssh descriptor's db.host/user/...).
#
# Usage (--config <file> and --machines <file> are REQUIRED):
#   bash orchestration/install/install_artifact_on_database.sh --config orchestration/config/shared_config.yml --machines results/machines/${RUN_ID}.yml
#
# Inputs:
#   --config <file>   - REQUIRED: config.yml to read (e.g. orchestration/config/shared_config.yml;
#                       supplies the postgres password + subnet_range used below)
#   --machines <file> - REQUIRED: machine descriptor (e.g. results/machines/<RUN_ID>.yml)
#   DB_ALLOW_CIDR     - CIDR allowed in pg_hba (default: subnet_range from config.yml;
#                       set to the attacker's source range for bring-your-own hosts)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${ORCHESTRATION_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

MACHINES_FILE_ARG=""
CONFIG_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machines) MACHINES_FILE_ARG="$2"; shift 2 ;;
    --config)   CONFIG_ARG="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Load shared infra/plumbing config from --config (REQUIRED; any claim's config.yml);
# sets the env + CONFIG_FILE=/dev/null so the shared GCP scripts use it.
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_load_gcloud_config.sh" "${CONFIG_ARG}"
# shellcheck source=/dev/null
. "${ORCHESTRATION_DIR}/util/_remote_transport.sh"

DB_ALLOW_CIDR="${DB_ALLOW_CIDR:-${SUBNET_RANGE:-}}"

log() { echo "[$(date -u +%FT%TZ)] [${RUN_ID:-no-run-id}] $*"; }

if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "POSTGRES_PASSWORD is required (set postgres_password in the YAML config)." >&2
  exit 1
fi

if [[ -z "${MACHINES_FILE_ARG}" ]]; then
  echo "--machines <file> is required (e.g. --machines results/machines/${RUN_ID:-<id>}.yml)." >&2
  exit 1
fi
MACHINES_FILE="${MACHINES_FILE_ARG}"
transport_load "${MACHINES_FILE}"

log "=== Install PostgreSQL on the database VM ==="
log "  Machines:    ${MACHINES_FILE}"
transport_summary
log "  pg_hba allow: ${DB_ALLOW_CIDR:-<none>}"

# Build the installer locally; run it on the DB role over the transport. It is a
# self-contained script (no local expansion) that reads POSTGRES_PASSWORD and
# DB_ALLOW_CIDR from its environment, set at invocation below.
PG_INSTALL_SH="$(mktemp -t pg_install_XXXXXX.sh)"
cleanup_temp() { rm -f "${PG_INSTALL_SH}"; }
trap cleanup_temp EXIT
cat > "${PG_INSTALL_SH}" <<'PG_INSTALL'
#!/usr/bin/env bash
set -euo pipefail
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}"
DB_ALLOW_CIDR="${DB_ALLOW_CIDR:-}"

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y wget gnupg lsb-release ca-certificates

# PGDG repo for PostgreSQL 18.
sudo install -d /usr/share/keyrings
wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/pgdg.gpg
echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null
sudo apt-get update
PG_PKG_VER="$(apt-cache madison postgresql-18 | awk '/18\.[0-9]+-/{print $3; exit}')"
if [[ -z "${PG_PKG_VER}" ]]; then
  echo "Could not resolve postgresql-18 package version from apt-cache madison" >&2
  exit 1
fi
# postgresql-contrib-18 is a virtual package provided by postgresql-18 and has
# no version of its own, so it cannot take a `=${PG_PKG_VER}` pin. The pin on
# postgresql-18 is what actually fixes the upstream version.
sudo apt-get install -y "postgresql-18=${PG_PKG_VER}" postgresql-contrib-18

PG_VERSION="18"
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"
PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"

sudo sed -i "s/^#listen_addresses.*/listen_addresses = '*'/" "${PG_CONF}"
# Read the config files with sudo: this installer runs over SSH as a normal user
# (not root, unlike the cloud-init path), and pg_hba.conf is mode 640 postgres:postgres.
# A plain grep would hit "Permission denied" and silently defeat these idempotency
# guards (re-runs would then duplicate the appended lines).
if sudo grep -Eq '^[[:space:]]*#?[[:space:]]*shared_preload_libraries[[:space:]]*=' "${PG_CONF}"; then
  sudo sed -i "s|^[[:space:]]*#\?[[:space:]]*shared_preload_libraries[[:space:]]*=.*|shared_preload_libraries = 'pg_stat_statements'|" "${PG_CONF}"
else
  echo "shared_preload_libraries = 'pg_stat_statements'" | sudo tee -a "${PG_CONF}" >/dev/null
fi
# max_connections headroom: the Table 1 sweep drives up to ~96 concurrent noise
# clients plus the attacker + admin connections; the default postgresql.conf ships
# max_connections=100, far too low. Bump to 1024 (matches the c4table1 / cross-zone
# Table 1 startup scripts).
if sudo grep -Eq '^[[:space:]]*#?[[:space:]]*max_connections[[:space:]]*=' "${PG_CONF}"; then
  sudo sed -i "s|^[[:space:]]*#\?[[:space:]]*max_connections[[:space:]]*=.*|max_connections = 1024|" "${PG_CONF}"
else
  echo "max_connections = 1024" | sudo tee -a "${PG_CONF}" >/dev/null
fi
if [[ -n "${DB_ALLOW_CIDR}" ]] && ! sudo grep -q "${DB_ALLOW_CIDR}" "${PG_HBA}"; then
  echo "host all all ${DB_ALLOW_CIDR} md5" | sudo tee -a "${PG_HBA}" >/dev/null
fi

sudo systemctl restart postgresql
sudo -u postgres psql -c "ALTER USER postgres WITH PASSWORD '${POSTGRES_PASSWORD}';"

# Confirm the EFFECTIVE max_connections after the restart. The 'selecting default
# max_connections ... 100' line printed earlier is from initdb (cluster creation
# during the package install, before this config edit) — it is the cluster default,
# not the running value. SHOW reports what the restart actually applied.
EFFECTIVE_MAXCONN="$(sudo -u postgres psql -tAc 'SHOW max_connections' | tr -d '[:space:]')"
echo "PostgreSQL up; effective max_connections=${EFFECTIVE_MAXCONN} (initdb's earlier '... 100' was the cluster default, now overridden)."
if [[ "${EFFECTIVE_MAXCONN}" != "1024" ]]; then
  echo "WARNING: effective max_connections=${EFFECTIVE_MAXCONN}, expected 1024 — check ${PG_CONF} and any conf.d overrides." >&2
fi
PG_INSTALL

log "Stage 1: push the PostgreSQL installer to the DB VM"
transport_push db "${PG_INSTALL_SH}" "/tmp/install_db.sh"

log "Stage 2: install + configure PostgreSQL on the DB VM"
transport_exec db \
  "POSTGRES_PASSWORD='${POSTGRES_PASSWORD}' DB_ALLOW_CIDR='${DB_ALLOW_CIDR}' bash /tmp/install_db.sh && rm -f /tmp/install_db.sh"

log "Stage 3: verify PostgreSQL is responding on the DB VM"
if ! transport_exec db "sudo -u postgres psql -tAc 'SELECT 1' 2>/dev/null" 2>/dev/null | grep -q 1; then
  log "ERROR: PostgreSQL did not come up on the DB VM after install" >&2
  exit 1
fi
log "=== PostgreSQL installed and running on the DB VM ==="
