# Attribute Reconstruction

This folder reconstructs each individual attribute before tuple extension. It
owns per-attribute calibration, binary/linear probing, and worker fanout for
attribute candidate domains.

## Contents

|                File | Purpose                                                             |
| ------------------: | ------------------------------------------------------------------- |
|       `__init__.py` | Package marker.                                                     |
| `reconstruction.py` | Attribute reconstruction loop and verification handoff.             |
|    `calibration.py` | Missing/existing timing calibration and index warmup per attribute. |
|         `binary.py` | Binary-search probing for indexed candidate domains.                |
|         `linear.py` | Linear scan probing for explicit candidate lists.                   |
|        `workers.py` | Shared attribute worker execution and result merging.               |
