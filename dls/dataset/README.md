# Enron Dataset Files

This directory contains JSONL slices of the Enron email dataset used by the
OpenSearch/Elasticsearch attack experiments.

For reproducibility, we have included the exact Enron slices as part of the
artifact:

| File                | Documents | Dataset slice   |
| ------------------- | --------: | --------------- |
| `enron_d1.jsonl`    |         1 | `train[0:1]`    |
| `enron_d10.jsonl`   |        10 | `train[0:10]`   |
| `enron_d100.jsonl`  |       100 | `train[0:100]`  |
| `enron_d1000.jsonl` |      1000 | `train[0:1000]` |

See "How These Files Were Generated" below for details on how these slices were
generated.

## Using These Files

Pass a JSONL file to the attack driver with `--corpus-file`:

```bash
unfilter-dls enumerate \
  --corpus-file dataset/enron_d10.jsonl \
  --chars "abcdefghijklmnopqrstuvwxyz0123456789'.,:_"
```

When the attack driver loads a file corpus, it indexes only the `text` value and
marks each document as hidden with `public=false`.

### File Format

Each `.jsonl` file is JSON Lines encoded: one JSON object per line. Each record
has this shape:

```json
{
  "dataset": "snoop2head/enron_aeslc_emails",
  "split": "train",
  "revision": null,
  "offset": 0,
  "text": "Date: Mon, 14 May 2001 ..."
}
```

Fields:

- `dataset`: Hugging Face dataset name used to fetch the document.
- `split`: dataset split used to fetch the document.
- `revision`: optional dataset revision or commit. `null` means the default
  revision was used when the file was written.
- `offset`: absolute record offset in the selected split.
- `text`: raw email text loaded into the attack index.

The attack driver only requires a non-empty string `text` field. The metadata
fields are included so the exact source slice is auditable.

## How These Files Were Generated

`src/dataset/util/parse_hf_dataset.py` writes the first `D` records (or a
deterministic offset slice) from the `snoop2head/enron_aeslc_emails` Hugging
Face dataset to a local JSONL file. (This dataset contains pre-processed emails
from the Enron corpus.) The specific Hugging Face commit used was
`3169dce694816726ebe3761fcf6c77bcca5aa68e`.

Install dependencies from the repository root:

```bash
uv python install 3.10
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

Regenerate the checked-in files:

```bash
for d in 1 10 100 1000; do
  unfilter-dls parse-hf-dataset \
    --revision 3169dce694816726ebe3761fcf6c77bcca5aa68e \
    --docs "$d" \
    --out "dataset/enron_d${d}.jsonl"
done
```

Generate a slice starting at a nonzero offset:

```bash
unfilter-dls parse-hf-dataset \
  --revision 3169dce694816726ebe3761fcf6c77bcca5aa68e \
  --docs 100 \
  --offset 500 \
  --out dataset/enron_offset500_d100.jsonl
```

### Script Options

- `--docs D`: required number of documents to write. Must be positive.
- `--out PATH`: required output JSONL path. Parent directories are created.
- `--offset N`: starting dataset record offset. Defaults to `0`.
- `--dataset NAME`: Hugging Face dataset name. Defaults to
  `snoop2head/enron_aeslc_emails`.
- `--split NAME`: dataset split. Defaults to `train`.
- `--revision REV`: optionally, pin the dataset to a specific Hugging Face
  dataset revision or commit for stronger reproducibility.

The script validates that the selected dataset contains a `text` column, that
the requested number of rows was loaded, and that every selected `text` value is
a non-empty string.
