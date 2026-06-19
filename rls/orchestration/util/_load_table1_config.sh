# shellcheck shell=bash
# Table 1 (C-R3) config loader.
#
# Sources the shared GCP config loader (orchestration/util/_load_gcloud_config.sh) for the common
# infra + plumbing, then adds the Table 1 CPU-sweep parameters read from the
# config's `table1:` block. This is the C-R3 claim's thin loader (config strategy
# B); the canonical config is orchestration/config/shared_config.yml.
#
# The config path is REQUIRED: the sourcing script passes it as the first argument
# (from its --config flag), e.g. `. util/_load_table1_config.sh "${CONFIG_ARG}"`.

_LTC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${_LTC_DIR}/_load_gcloud_config.sh" "${1:-}" || return 1 2>/dev/null || exit 1

# Bookkeeping alias: provision/provision_vms.sh / run_oracle_accuracy.sh
# reference ${TABLE1_CONFIG} and write it into the machine descriptor.
TABLE1_CONFIG="${GCLOUD_CONFIG}"

set -a

# ---- shared sweep params (table1: block of the config) --------------------
T1_PROBES="$(_cfg_yq '.table1.probes' 100000)"
T1_SEED="$(_cfg_yq '.table1.seed' 1)"
T1_CALIBRATION_MODE="$(_cfg_yq '.table1.calibration_mode' trial)"
T1_NONEXISTENT_OFFSET="$(_cfg_yq '.table1.nonexistent_offset' 1000)"

# ---- per-variant params ---------------------------------------------------
# Join: point-query noise, controller off (sweep cells carry no target-% field).
T1_JOIN_POLICIES="$(_cfg_yq '.table1.join.policies' join)"
T1_JOIN_K_VALUES="$(_cfg_yq '.table1.join.k_values' 1,2,3,4,5,6,7,8,9,10)"
T1_JOIN_NOISE_QUERY_MODE="$(_cfg_yq '.table1.join.noise_query_mode' point)"
T1_JOIN_NOISE_RANGE_WIDTH="$(_cfg_yq '.table1.join.noise_range_width')"
T1_JOIN_NOISE_SWEEP="$(_cfg_yq '.table1.join.noise_sweep')"
# Inline: range-query noise, controller on (sweep cells carry a target-% field).
T1_INLINE_POLICIES="$(_cfg_yq '.table1.inline.policies' inline)"
T1_INLINE_K_VALUES="$(_cfg_yq '.table1.inline.k_values' 1,2,4,8)"
T1_INLINE_NOISE_QUERY_MODE="$(_cfg_yq '.table1.inline.noise_query_mode' range)"
T1_INLINE_NOISE_RANGE_WIDTH="$(_cfg_yq '.table1.inline.noise_range_width' 1000)"
T1_INLINE_NOISE_SWEEP="$(_cfg_yq '.table1.inline.noise_sweep')"

set +a

# Make the shared scripts skip their .env sourcing and use this environment.
export CONFIG_FILE=/dev/null
