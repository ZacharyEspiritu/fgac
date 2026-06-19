# Copyright 2026 MongoDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from util.paths import resolve_artifact_path

from .models import KgramCandidate, LoadedKgrams


LOG_RECOVERED_RE = re.compile(
    r"^recovered\s+(\d+)-gram:\s+(.+?)(?:\s+\(.*\))?\s*$"
)
LOG_LIST_RE = re.compile(r"^recovered\s+(\d+)-grams:\s+(\[.*\])\s*$")


def tokenise_kgram(kgram: str) -> Tuple[str, ...]:
    return tuple(part for part in kgram.split() if part)


def infer_k(kgrams: Iterable[str]) -> Optional[int]:
    lengths = {len(tokenise_kgram(kgram)) for kgram in kgrams}
    lengths.discard(0)
    if not lengths:
        return None
    return max(lengths)


def normalise_kgrams(kgrams: Iterable[str], k: int) -> List[str]:
    result = []
    seen = set()
    for kgram in kgrams:
        tokens = tokenise_kgram(kgram)
        if len(tokens) != k:
            continue
        normalised = " ".join(tokens)
        if normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return sorted(result)


def get_nested(data: Dict[str, Any], path: Sequence[str]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def stage_k(stage: Dict[str, Any]) -> Optional[int]:
    value = stage.get("ngram_size")
    if isinstance(value, int):
        return value
    recovered = stage.get("recovered_ngrams")
    if isinstance(recovered, list):
        return infer_k(str(item) for item in recovered)
    return None


def stage_source_ngrams(stage: Dict[str, Any], source: str) -> List[str]:
    recovered = [str(item) for item in stage.get("recovered_ngrams", [])]
    sets = get_nested(stage, ("corpus_ngram_stats", "sets")) or {}
    indexed = [str(item) for item in sets.get("indexed_ngrams", [])]
    missing = [str(item) for item in sets.get("missing_indexed_ngrams", [])]
    extra = [str(item) for item in sets.get("extra_recovered_ngrams", [])]

    if source == "recovered":
        return recovered
    if source == "indexed":
        return indexed
    if source == "missing":
        return missing
    if source == "extra":
        return extra
    if source == "recovered-indexed":
        if not indexed:
            return recovered
        return sorted(set(recovered) & set(indexed))
    raise ValueError(f"unknown source: {source}")


def stats_candidates(data: Dict[str, Any], source: str) -> List[KgramCandidate]:
    candidates: List[KgramCandidate] = []

    ngram_recovery = data.get("ngram_recovery")
    if isinstance(ngram_recovery, dict):
        k = stage_k(ngram_recovery)
        kgrams = stage_source_ngrams(ngram_recovery, source)
        if k is not None and kgrams:
            candidates.append(
                KgramCandidate(
                    name="ngram_recovery",
                    priority=2,
                    k=k,
                    kgrams=kgrams,
                )
            )

    for index, stage in enumerate(data.get("attack_stages", [])):
        if not isinstance(stage, dict) or "recovered_ngrams" not in stage:
            continue
        k = stage_k(stage)
        kgrams = stage_source_ngrams(stage, source)
        if k is None or not kgrams:
            continue
        name = (
            stage.get("stage_name")
            or stage.get("field")
            or f"attack_stages[{index}]"
        )
        candidates.append(
            KgramCandidate(
                name=str(name),
                priority=1,
                k=k,
                kgrams=kgrams,
            )
        )

    return candidates


def select_candidate(
    candidates: Sequence[KgramCandidate],
    *,
    source: str,
    requested_k: Optional[int],
) -> KgramCandidate:
    filtered = list(candidates)
    if requested_k is not None:
        filtered = [candidate for candidate in filtered if candidate.k == requested_k]
    if not filtered:
        raise ValueError(
            f"no {source} k-grams found"
            + (f" for k={requested_k}" if requested_k is not None else "")
        )
    return max(filtered, key=lambda candidate: (candidate.k, candidate.priority))


def load_from_stats(
    path: Path,
    source: str,
    requested_k: Optional[int],
) -> LoadedKgrams:
    data = json.loads(path.read_text())
    selected = select_candidate(
        stats_candidates(data, source),
        source=source,
        requested_k=requested_k,
    )
    return LoadedKgrams(
        kind="stats",
        stage=selected.name,
        k=selected.k,
        kgrams=normalise_kgrams(selected.kgrams, selected.k),
    )


def log_kgrams_by_size(path: Path) -> Dict[int, Set[str]]:
    by_k: Dict[int, Set[str]] = defaultdict(set)
    for line in path.read_text(errors="replace").splitlines():
        match = LOG_RECOVERED_RE.match(line)
        if match:
            by_k[int(match.group(1))].add(match.group(2))
            continue
        match = LOG_LIST_RE.match(line)
        if not match:
            continue
        try:
            values = ast.literal_eval(match.group(2))
        except Exception:
            continue
        if isinstance(values, list):
            by_k[int(match.group(1))].update(str(value) for value in values)
    return by_k


def load_from_log(path: Path, requested_k: Optional[int]) -> LoadedKgrams:
    by_k = log_kgrams_by_size(path)
    if requested_k is not None:
        k = requested_k
    elif by_k:
        k = max(by_k)
    else:
        raise ValueError("no recovered k-grams found in log")

    return LoadedKgrams(
        kind="log",
        stage=f"log recovered {k}-grams",
        k=k,
        kgrams=normalise_kgrams(by_k.get(k, set()), k),
    )


def load_kgrams(path: Path, source: str, requested_k: Optional[int]) -> LoadedKgrams:
    path = resolve_artifact_path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        return load_from_stats(path, source=source, requested_k=requested_k)
    except json.JSONDecodeError:
        if source != "recovered":
            raise ValueError("non-JSON logs only support --source recovered")
        return load_from_log(path, requested_k=requested_k)
