# Candidate Domains

This folder parses candidate-domain config and provides lazy value generators.
Candidate domains may be explicit values, integer ranges, formatted ranges,
parts-based Cartesian products, or composites of those forms.

## Contents

|          File | Purpose                                                                |
| ------------: | ---------------------------------------------------------------------- |
| `__init__.py` | Public exports for candidate specs, parsers, and value classes.        |
|    `specs.py` | `CandidateConfig` and `CandidateSpec`, the public candidate contract.  |
|   `parser.py` | YAML/JSON candidate config normalization into `CandidateSpec`.         |
|   `values.py` | Lazy candidate value containers such as ranges, parts, and composites. |
|  `domains.py` | Membership tests without expanding large candidate domains.            |
