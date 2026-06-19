# shellcheck shell=bash
# Tuple-extension reconstruction (C-R7, Table 4) config loader.
#
# Sources the shared GCP config loader (orchestration/util/_load_gcloud_config.sh) for the common
# infra + plumbing, then adds the tuple-extension sweep parameters read from the
# config's `tuplext:` block. This is the C-R7 claim's thin loader (config strategy
# B); the canonical config is orchestration/config/shared_config.yml.
#
# The config path is REQUIRED: the sourcing script passes it as the first argument
# (from its --config flag), e.g. `. util/_load_tuplext_config.sh "${CONFIG_ARG}"`.

_LTX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${_LTX_DIR}/_load_gcloud_config.sh" "${1:-}" || return 1 2>/dev/null || exit 1

# Bookkeeping alias (forwarded to provision/install child steps).
TUPLEXT_CONFIG="${GCLOUD_CONFIG}"

set -a

# ---- tunable: tuplext: block of the config --------------------------------
# Attribute orderings (the "most-selective-first" axis of Table 4): a subset of the
# 6 permutations of {ssn,zip,age} — sza,saz,asz,azs,zsa,zas.
TX_ORDERINGS="$(_cfg_yq '.tuplext.orderings' 'sza,saz,asz,azs,zsa,zas')"
# Attacker worker counts (W). Only W=1 is needed for C-R7 (the render knows 1,2,4).
TX_WORKERS="$(_cfg_yq '.tuplext.workers' '1')"
# Reps per (ordering, W) cell (default 1; original C-R7 ran 3). >1 suffixes -r<rep>
# so tuple_extension_table.py aggregates them (mean ± CI).
TX_REPS="$(_cfg_yq '.tuplext.reps' 1)"
# Tuple-extension attack parameters for the C-R7 claim.
TX_SAMPLE_TUPLES="$(_cfg_yq '.tuplext.sample_tuples' 100000)"
TX_TUPLE_MODE="$(_cfg_yq '.tuplext.tuple_extension_mode' between)"
TX_RECOMPUTE_CAL_ROUNDS="$(_cfg_yq '.tuplext.tuple_recompute_cal_rounds' 1)"
TX_PROBE_ROUNDS="$(_cfg_yq '.tuplext.probe_rounds' 1)"
# Reconstruction config (git-tracked; shipped to the attacker by
# orchestration/install/install_artifact_on_attacker.sh).
TX_RECONSTRUCTION_CONFIG="$(_cfg_yq '.tuplext.reconstruction_config' src/reconstruction/config/tuplext.yml)"

set +a

# Make the shared scripts skip their .env sourcing and use this environment.
export CONFIG_FILE=/dev/null
