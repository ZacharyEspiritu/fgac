# Noise VM Wrappers

This directory contains shell glue for experiments that need a separate
background-load process. The Python noise implementation lives in
`src/noise/`; this layer starts and coordinates it on the remote noise VM.

## Contents

|                                           File | Purpose                                                                                  |
| ---------------------------------------------: | ---------------------------------------------------------------------------------------- |
| `run_oracle_accuracy_with_noise_controller.sh` | Remote wrapper used by C-R3/C-R4 to start noise, drive CPU/load targets, and run probes. |

## Developer Notes

The wrapper is invoked by `orchestration/run_oracle_accuracy.sh` and
`orchestration/run_crosszone_experiment.sh`. It should not contain claim-specific render
logic; rendering stays in the higher-level run scripts and Python renderers.
