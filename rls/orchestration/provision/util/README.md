# Provisioning Utilities

This directory contains internal role-specific helpers used by
`orchestration/provision/provision_vms.sh`. They are not standalone entry points; direct
use is intentionally de-supported so all VM creation goes through the descriptor
workflow.

## Contents

|                              File | Purpose                                                                       |
| --------------------------------: | ----------------------------------------------------------------------------- |
| `setup_gcloud_postgres_vm.sh`     | Create/configure the bare PostgreSQL VM, optional TDE data disk, and network. |
| `setup_gcloud_attacker_vm.sh`     | Create/configure the bare attacker VM and wait for it to become reachable.    |
| `setup_gcloud_noise_vm.sh`        | Create/configure the bare noise VM used by background-load experiments.       |

## Developer Notes

These helpers expect their environment to be prepared by the config loader and
run-id overlay. Add new provider-specific VM setup here only when it is still
called through `provision_vms.sh` and recorded in the machine descriptor.
