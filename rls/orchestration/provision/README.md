# Provisioning

This directory owns GCP resource lifecycle: creating isolated VM stacks and
tearing them down. Provisioning writes a machine descriptor that the install and
run layers consume through the transport abstraction.

## Contents

|     File or folder | Purpose                                                                                |
| -----------------: | -------------------------------------------------------------------------------------- |
| `provision_vms.sh` | Create the VPC/subnet/firewall rules and bare DB, attacker, and optional noise VMs.    |
|   `cleanup_vms.sh` | Delete a descriptor-backed stack, a RUN_ID/config-backed stack, or stale labelled VMs. |
|            `util/` | Internal role-specific GCP setup helpers called only by `provision_vms.sh`.            |

## Developer Notes

`provision_vms.sh` does not install the repo, PostgreSQL, Python packages, or
datasets. It only creates infrastructure and emits
`results/machines/<RUN_ID>.yml`. The descriptor is the boundary between
provider-specific provisioning and the provider-agnostic install/run scripts.
