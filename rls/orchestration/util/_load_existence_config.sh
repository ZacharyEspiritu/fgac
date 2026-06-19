# shellcheck shell=bash
# Existence-family (C-R1 / C-R2 / C-R5) config loader.
#
# Sources the shared GCP config loader (orchestration/util/_load_gcloud_config.sh) for the common
# infra + plumbing, then adds the existence/range experiment parameters read from
# the config's existence:/range: blocks. This is the existence claim's thin loader
# (config strategy B); the canonical config is orchestration/config/shared_config.yml (C-R5
# passes orchestration/config/tde_config.yml instead — same blocks, TDE VM setup).
#
# The config path is REQUIRED: the sourcing script passes it as the first argument
# (from its --config flag), e.g. `. util/_load_existence_config.sh "${CONFIG_ARG}"`.

_LEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${_LEC_DIR}/_load_gcloud_config.sh" "${1:-}" || return 1 2>/dev/null || exit 1

# Bookkeeping alias: provision/provision_vms.sh / run_existence_experiment.sh
# reference ${EXISTENCE_CONFIG} and write it into the machine descriptor.
EXISTENCE_CONFIG="${GCLOUD_CONFIG}"

set -a

# ---- tunable: existence: / range: blocks of the config --------------------
EXISTENCE_SAMPLES="$(_cfg_yq '.existence.samples' 1000)"
EXISTENCE_SEED="$(_cfg_yq '.existence.seed' 1)"
RANGE_ATTACK_SAMPLES="$(_cfg_yq '.range.samples' 1000)"
RANGE_ATTACK_WIDTH="$(_cfg_yq '.range.width' 4)"
RANGE_ATTACK_MAX_TRIES="$(_cfg_yq '.range.max_tries' 50000)"
RANGE_ATTACK_NONEXISTENT_OFFSET="$(_cfg_yq '.range.nonexistent_offset' 1000)"
RANGE_ATTACK_SEED="$(_cfg_yq '.range.seed' 1)"

# ---- existence/range attack: output paths + fixed flags -------------------
EXISTENCE_FAST="0"; EXISTENCE_WARM_CACHE="1"; EXISTENCE_AUTH_CARD="1"; EXISTENCE_UNAUTH_CARD="1"
EXISTENCE_OUTPUT="results/existence_latency.csv"
EXISTENCE_PLOT_OUTPUT="results/existence_kde.pgf"; EXISTENCE_PLOT_FORMAT="pgf"
RANGE_ATTACK_FAST="1"; RANGE_ATTACK_WARM_CACHE="1"; RANGE_ATTACK_EXPLAIN="0"
RANGE_ATTACK_OUTPUT="results/existence_range_latency.csv"
RANGE_ATTACK_PLOT_OUTPUT="results/existence_range_kde.pgf"; RANGE_ATTACK_PLOT_FORMAT="pgf"

set +a

# Make the shared scripts skip their .env sourcing and use this environment.
export CONFIG_FILE=/dev/null
