# Orchestration Shell Drivers

This directory contains the shell orchestration layer for cloud-backed artifact
runs. The reviewer-facing entry point is `unfilter-rls claims run ...`; the CLI
dispatches to the claim drivers here. These scripts keep infrastructure,
installation, experiment execution, and cleanup separate so the same experiment
drivers can also run against bring-your-own machine descriptors.

## Subdirectories

|       Folder | Purpose                                                                                     |
| -----------: | ------------------------------------------------------------------------------------------- |
|    `config/` | YAML configurations for the supported claim stacks and experiment parameters.               |
|   `install/` | Role-specific installers that prepare already-provisioned DB, attacker, and noise machines. |
|     `noise/` | Remote wrapper for oracle-accuracy runs that need a background-load controller.             |
| `provision/` | GCP VM/network provisioning and cleanup, plus low-level provisioning helpers.               |
|      `util/` | Shared shell helpers for config loading, preflight checks, run IDs, and remote transport.   |

## Top-Level Drivers

|                                 File | Purpose                                                                                  |
| -----------------------------------: | ---------------------------------------------------------------------------------------- |
|               `run_samezone_exps.sh` | One-shot same-zone claim runner for C-R1, C-R2, C-R3, C-R6, C-R7, C-R8, and C-R9.        |
|              `run_crosszone_exps.sh` | One-shot cross-zone C-R4 runner: provision, install, run Table 2 cells, render, cleanup. |
|                    `run_tde_exps.sh` | One-shot C-R5 TDE runner using a LUKS-backed database disk.                              |
|        `run_existence_experiment.sh` | Attached/managed driver for C-R1, C-R2, and the TDE existence/range microbenchmarks.     |
|             `run_oracle_accuracy.sh` | Attached/managed C-R3 oracle-accuracy sweep driver.                                      |
|        `run_crosszone_experiment.sh` | Attached/managed C-R4 cell runner and Table 2 render staging.                            |
|       `run_singleattr_experiment.sh` | Attached/managed C-R6 single-attribute reconstruction sweep.                             |
|          `run_tuplext_experiment.sh` | Attached/managed C-R7 tuple-extension reconstruction sweep.                              |
| `run_join_mitigations_experiment.sh` | Attached/managed C-R8 mitigation sweep.                                                  |
|          `run_db_size_experiment.sh` | Attached/managed C-R9 database physical-size measurement.                                |

The one-shot `*_exps.sh` scripts are claim bundles used by the `unfilter-rls claims` CLI.
The singular `run_*_experiment.sh` scripts are lower-level drivers that operate
on a machine descriptor and are useful for debugging or BYO-machine runs.
