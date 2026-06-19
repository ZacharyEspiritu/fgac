# shellcheck shell=bash
# Cross-zone Table-1 (C-R4, Table 2) config loader.
#
# Sources the shared GCP config loader (orchestration/util/_load_gcloud_config.sh) for the common
# infra + plumbing — INCLUDING the per-role attacker zone/subnet that make this a
# cross-region run — then adds the C-R4 cell/sweep parameters from the config's
# `crosszone:` block. This is C-R4's thin loader (config strategy B); the config is
# orchestration/config/crosszone_config.yml.
#
# The config path is REQUIRED: the sourcing script passes it as the first argument
# (from its --config flag), e.g. `. util/_load_crosszone_config.sh "${CONFIG_ARG}"`.

_LCZ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${_LCZ_DIR}/_load_gcloud_config.sh" "${1:-}" || return 1 2>/dev/null || exit 1

# Bookkeeping alias: provision/provision_vms.sh / run_crosszone_experiment.sh
# reference ${CROSSZONE_CONFIG} and write it into the machine descriptor.
CROSSZONE_CONFIG="${GCLOUD_CONFIG}"

set -a

# ---- C-R4 cells / sweep (crosszone: block of the config) ------------------
# Two cells run in sequence on one stack: Base (no load) + cpu50 (50% DB CPU).
CZ_NOISE_SWEEP="$(_cfg_yq '.crosszone.noise_sweep' 'baseline:0:0,cpu50:96:5000:50')"
CZ_PROBES="$(_cfg_yq '.crosszone.probes' 10000)"
CZ_K_VALUES="$(_cfg_yq '.crosszone.k_values' 1,2,4,8)"
CZ_CALIBRATION_MODE="$(_cfg_yq '.crosszone.calibration_mode' trial)"
CZ_SEED="$(_cfg_yq '.crosszone.seed' 1)"
CZ_NONEXISTENT_OFFSET="$(_cfg_yq '.crosszone.nonexistent_offset' 1000)"
CZ_SKIP_WARMUP="$(_cfg_yq '.crosszone.skip_warmup' 1)"
# cpu50 cell: range-query noise + the closed-loop DB-CPU controller.
CZ_NOISE_QUERY_MODE="$(_cfg_yq '.crosszone.noise_query_mode' range)"
CZ_NOISE_RANGE_WIDTH="$(_cfg_yq '.crosszone.noise_range_width' 100)"
CZ_CPU_TOLERANCE_PCT="$(_cfg_yq '.crosszone.cpu_tolerance_pct' 2)"
CZ_CPU_READY_TIMEOUT="$(_cfg_yq '.crosszone.cpu_ready_timeout' 1200)"
CZ_CPU_SAMPLE_SECONDS="$(_cfg_yq '.crosszone.cpu_sample_seconds' 3)"
CZ_CPU_READY_STREAK="$(_cfg_yq '.crosszone.cpu_ready_streak' 2)"
CZ_CPU_GAIN="$(_cfg_yq '.crosszone.cpu_gain' 0.75)"
# Render (Table 2): cross-region-vs-same-zone paired diff.
CZ_RENDER_SCENARIOS="$(_cfg_yq '.crosszone.render_scenarios' baseline,cpu50)"
CZ_SAME_ZONE_BASELINE_DIR="$(_cfg_yq '.crosszone.same_zone_baseline_dir' results/table1/samezone-baseline)"

set +a

# Make the shared scripts skip their .env sourcing and use this environment.
export CONFIG_FILE=/dev/null
