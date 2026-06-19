#!/usr/bin/env python3

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

import argparse
import json
from pathlib import Path
from typing import List, Optional, cast


def load_enron_texts(
    limit: int,
    *,
    dataset_name: str,
    split: str,
    offset: int,
    revision: Optional[str],
) -> List[str]:
    try:
        from datasets import load_dataset  # type: ignore[import-untyped]
    except Exception as e:
        raise RuntimeError(
            'Failed to import "datasets". Install it or run from an environment '
            "where it is available."
        ) from e

    split_slice = f"{split}[{offset}:{offset + limit}]"
    kwargs = {"revision": revision} if revision else {}
    dataset = load_dataset(dataset_name, split=split_slice, **kwargs)
    if "text" not in dataset.column_names:
        raise RuntimeError(
            f'Unexpected dataset schema, missing "text" column: {dataset.column_names}'
        )

    texts = dataset["text"]
    if len(texts) < limit:
        raise RuntimeError(
            f"Loaded only {len(texts)} Enron records from {split_slice}; requested {limit}"
        )

    bad_offsets = [
        offset + i
        for i, text in enumerate(texts)
        if not isinstance(text, str) or not text.strip()
    ]
    if bad_offsets:
        raise RuntimeError(
            "Selected Enron records have empty/non-string text at absolute offsets: "
            + ", ".join(str(i) for i in bad_offsets[:10])
        )
    return cast(List[str], texts)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a deterministic Enron dataset slice to a local JSONL file."
    )
    parser.add_argument("--docs", type=int, required=True, help="Number of documents D to write.")
    parser.add_argument("--out", required=True, help="Output JSONL path.")
    parser.add_argument("--offset", type=int, default=0, help="Starting dataset record offset.")
    parser.add_argument(
        "--dataset",
        default="snoop2head/enron_aeslc_emails",
        help="Hugging Face dataset name.",
    )
    parser.add_argument("--split", default="train", help="Dataset split to read from.")
    parser.add_argument(
        "--revision",
        default=None,
        help="Optional Hugging Face dataset revision/commit for reproducibility.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.docs <= 0:
        raise SystemExit("--docs must be positive")
    if args.offset < 0:
        raise SystemExit("--offset must be non-negative")

    texts = load_enron_texts(
        args.docs,
        dataset_name=args.dataset,
        split=args.split,
        offset=args.offset,
        revision=args.revision,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for i, text in enumerate(texts):
            record = {
                "dataset": args.dataset,
                "split": args.split,
                "revision": args.revision,
                "offset": args.offset + i,
                "text": text,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        f"Wrote {len(texts)} docs from {args.dataset} at slice "
        f"[{args.offset}, {args.offset + len(texts)}) to {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
