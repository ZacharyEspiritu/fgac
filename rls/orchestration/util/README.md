# GCP Shell Utilities

This directory contains shared shell helpers sourced by the GCP drivers. Most
files set environment variables for callers, so they are intentionally written
as shell libraries rather than standalone scripts.

## Contents

|                          File | Purpose                                                                                |
| ----------------------------: | -------------------------------------------------------------------------------------- |
|      `_load_gcloud_config.sh` | Shared YAML loader for common infrastructure, dataset, image, and PostgreSQL settings. |
|   `_load_existence_config.sh` | Thin loader for C-R1/C-R2/C-R5 existence and range parameters.                         |
|      `_load_table1_config.sh` | Thin loader for C-R3 oracle-accuracy Table 1 parameters.                               |
|   `_load_crosszone_config.sh` | Thin loader for C-R4 cross-zone cells and render parameters.                           |
|  `_load_singleattr_config.sh` | Thin loader for C-R6 single-attribute reconstruction sweep parameters.                 |
|     `_load_tuplext_config.sh` | Thin loader for C-R7 tuple-extension reconstruction parameters.                        |
| `_load_mitigations_config.sh` | Thin loader for C-R8 mitigation sweep parameters.                                      |
|        `_remote_transport.sh` | Descriptor-backed transport abstraction for `gcloud` and BYO `ssh` machines.           |
|          `_run_id_overlay.sh` | Applies RUN_ID suffixes and labels to VM/network/firewall resource names.              |
|         `_local_preflight.sh` | Local dependency and authentication preflight used by claim bundle scripts.            |
|               `check_deps.py` | Python dependency checker used by `_local_preflight.sh`; reads `requirements.txt`.     |

## Developer Notes

The `_load_*` files are sourced by shell scripts and intentionally export
variables for their callers. `_remote_transport.sh` is the main abstraction that
keeps experiment drivers independent of whether machines came from GCP or a BYO
SSH descriptor.
