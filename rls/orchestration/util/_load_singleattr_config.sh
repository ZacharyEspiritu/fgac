# shellcheck shell=bash
# Single-attribute reconstruction (C-R6, Table 3) config loader.
#
# Sources the shared GCP config loader (orchestration/util/_load_gcloud_config.sh) for the common
# infra + plumbing, then adds the single-attribute sweep parameters read from the
# config's `singleattr:` block. This is the C-R6 claim's thin loader (config
# strategy B); the canonical config is orchestration/config/shared_config.yml.
#
# The config path is REQUIRED: the sourcing script passes it as the first argument
# (from its --config flag), e.g. `. util/_load_singleattr_config.sh "${CONFIG_ARG}"`.

_LSC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "${_LSC_DIR}/_load_gcloud_config.sh" "${1:-}" || return 1 2>/dev/null || exit 1

# Bookkeeping alias (forwarded to provision/install child steps).
SINGLEATTR_CONFIG="${GCLOUD_CONFIG}"

set -a

# ---- tunable: singleattr: block of the config -----------------------------
# Columns are tokens (ssn|age|zip); the driver maps zip -> zip_code for the DB
# query and keeps the token in the render-classifiable dir name.
SA_COLUMNS="$(_cfg_yq '.singleattr.columns' 'ssn,age,zip')"
# Binary worker counts (W). One binary rep per (column, W) cell (× SA_REPS).
SA_WORKERS="$(_cfg_yq '.singleattr.workers' '1,2,4,8,16')"
# Reps per column (default 1). Each cell runs SA_REPS times; >1 suffixes -r<rep>
# so single_attribute_table.py aggregates them (mean ± 95% CI).
SA_REPS="$(_cfg_yq '.singleattr.reps' 1)"
# Probe rounds (k) per oracle probe — matches launch_c4_single_attr_reps.sh (1).
SA_PROBE_ROUNDS="$(_cfg_yq '.singleattr.probe_rounds' 1)"
# Also run the LinearProber baseline (one W=1 run per column; ssn reduced +
# extrapolated by single_attribute_table.py). true/false (default true).
SA_RUN_LINEAR="$(_cfg_yq '.singleattr.run_linear' true)"
# Reconstruction configs (git-tracked; shipped to the attacker by
# orchestration/install/install_artifact_on_attacker.sh).
SA_BINARY_RECONSTRUCTION_CONFIG="$(_cfg_yq '.singleattr.binary_reconstruction_config' src/reconstruction/config/singleattr_binary.yml)"
SA_LINEAR_RECONSTRUCTION_CONFIG="$(_cfg_yq '.singleattr.linear_reconstruction_config' src/reconstruction/config/singleattr_linear.yml)"

set +a

# Make the shared scripts skip their .env sourcing and use this environment.
export CONFIG_FILE=/dev/null
