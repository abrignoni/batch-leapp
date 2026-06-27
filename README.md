# batch_leapp.py

Recursively find every `.zip` in a directory and run a **LEAPP** tool — [iLEAPP](https://github.com/abrignoni/iLEAPP), [ALEAPP](https://github.com/abrignoni/ALEAPP), [RLEAPP](https://github.com/abrignoni/RLEAPP), or [VLEAPP](https://github.com/abrignoni/VLEAPP) — on each one, producing a folder full of ready-to-review report directories plus a single master `index.html` that links them all.

Point it at a directory of extractions, walk away, and come back to a review-ready set of LEAPP reports.

---

## What it does

- **Recursively** finds every `.zip` (case-insensitive) under the input directory.
- Runs your chosen LEAPP tool on each zip into **its own output folder**, so reports never overwrite each other.
- Writes a **master `index.html`** at the root of the output directory with one row per extraction, linking:
  - the report folder,
  - the LEAPP `index.html`,
  - the `_lava_data.lava` file (when the tool produces one).
- Adds a **Source dir** column showing the top-level directory each zip came from (handy for grouping by case).
- **Keeps going on failure** — one bad zip won't stop the batch — and prints an ok/failed/skipped summary at the end.
- Optionally runs **multiple LEAPP processes in parallel**.

iLEAPP, ALEAPP, RLEAPP and VLEAPP all share the same command line — `python <x>leapp.py -t zip -i <zip> -o <out>` — so the same script drives any of them. The tool is auto-detected from the script filename you pass to `--leapp` and used for labels, the per-job log filename, and the index title.

---

## Requirements

- Python 3.8+ (standard library only — nothing to `pip install` for this script).
- A working LEAPP install. You point at its main script with `--leapp` (e.g. `ileapp.py`, `aleapp.py`, `rleapp.py`, `vleapp.py`).
- If the LEAPP tool lives in its own virtual environment, point `--python` at that environment's interpreter.

---

## Usage

```bash
python batch_leapp.py INPUT_DIR OUTPUT_DIR --leapp /path/to/<x>leapp.py
```

- `INPUT_DIR` — directory searched recursively for `.zip` files.
- `OUTPUT_DIR` — where the per-zip report folders and the master `index.html` are written (created if it doesn't exist).

### Examples

iLEAPP:

```bash
python batch_leapp.py /Volumes/Cases/ios /Volumes/Cases/ios_reports \
    --leapp ~/tools/iLEAPP/ileapp.py
```

ALEAPP:

```bash
python batch_leapp.py /Volumes/Cases/android /Volumes/Cases/android_reports \
    --leapp ~/tools/ALEAPP/aleapp.py
```

Open the result:

```bash
open /Volumes/Cases/ios_reports/index.html
```

---

## Options

| Option | Default | Description |
|---|---|---|
| `--leapp PATH` | `ileapp.py` | Path to the LEAPP script (`ileapp.py` / `aleapp.py` / `rleapp.py` / `vleapp.py`). `--ileapp` is accepted as an alias. |
| `--python PATH` | current interpreter | Python used to run the tool (point at the tool's venv if it has one). |
| `-t`, `--type TYPE` | `zip` | Extraction type passed with `-t`. |
| `-j`, `--jobs N` | `1` | Number of LEAPP runs to execute in parallel. |
| `--timeout SECONDS` | none | Per-zip timeout; a run exceeding it is marked failed and the batch continues. |
| `--skip-existing` | off | Skip a zip whose output folder already exists and is non-empty (resume a partial run). |
| `--dry-run` | off | Print the exact commands without running the tool. |

---

## Recommended first run

Always sanity-check the zip list and commands before turning it loose on a case load:

```bash
python batch_leapp.py INPUT_DIR OUTPUT_DIR --leapp /path/to/<x>leapp.py --dry-run
```

---

## Parallel runs

```bash
python batch_leapp.py INPUT_DIR OUTPUT_DIR --leapp /path/to/<x>leapp.py -j 4
```

- Output dirs are assigned **before** any run starts, so names never collide.
- In parallel mode each run's output is captured to `<tool>_run.log` (e.g. `aleapp_run.log`) inside that extraction's folder, so concurrent runs don't garble the terminal; the screen shows just `OK` / `FAILED` / `TIMEOUT` per zip as they finish.
- With `-j 1` (the default), the tool's output streams live as usual.

### Why parallel runs need isolation

LEAPP tools keep one **shared** history/settings file (e.g. macOS `~/Library/Application Support/LEAPP/history.json`) and update it with a read-modify-write that uses a fixed temp filename. Two tools running at once race on that file — one wins the rename, the other dies with `history.tmp -> history.json: No such file`, and a later read sees a half-written file (`JSONDecodeError: Extra data`).

To avoid this, **parallel runs (`-j > 1`) each get a private config dir** at `<output>/<zip>/.leapp_home/`, set via `HOME` / `APPDATA` / `XDG_CONFIG_HOME`. Consequences:

- Concurrent runs never touch the same history file, so no corruption.
- Your real, user-level LEAPP history is left untouched and parallel runs are **not** recorded in it (an empty private config dir means history recording is simply off for those runs).
- Sequential runs (`-j 1`) use your normal config dir and record history as usual.

> **Caution:** LEAPP runs are CPU-, disk-, and RAM-heavy. On a typical workstation `-j 2`–`-j 4` is a sane range. Pushing to your full core count can thrash disk I/O and run *slower* — and large extractions can exhaust memory. Start conservative.

---

## Console output

Every run prints a banner (detected tool, zip count, output dir) and a running **`[done/total]` progress counter** so you always know how far along the batch is.

Sequential (`-j 1`) — the tool's own output streams live, framed by counter lines:

```
[1/4] running: caseA/a.zip
... live iLEAPP/ALEAPP output ...
[1/4] OK       caseA/a.zip  (37s)
[2/4] running: caseB/b.zip
```

Parallel (`-j > 1`) — verbose output goes to the per-job log; the screen shows one counter line per zip as each completes (the counter climbs `1 → total` in completion order):

```
Running 4 job(s) with 3 worker(s)...
[1/4] OK       caseC/c.zip  (41s)
[2/4] OK       caseA/a.zip  (44s)
[3/4] FAILED   caseB/b.zip  (exit 1, see ileapp_run.log)
[4/4] OK       caseD/d.zip  (52s)
```

It ends with the master-index path and a summary line: `Done. X ok, Y failed, Z skipped.` (plus a list of any failures).

---

## Output layout

```
OUTPUT_DIR/
├── index.html                      ← master index (open this)
├── caseA_phone/                    ← one folder per zip
│   ├── ileapp_run.log              ← captured tool output (parallel mode)
│   ├── .leapp_home/                ← private config dir (parallel mode only)
│   └── iLEAPP_Reports_<timestamp>/
│       ├── index.html              ← the LEAPP report
│       └── _lava_data.lava         ← the LAVA file
├── caseB_tablet/
│   └── ...
└── ...
```

(The report subfolder is named by the tool — `iLEAPP_Reports_*`, `ALEAPP_Reports_*`, etc. — and the log file is named after the detected tool.)

Per-zip folders are named from each zip's path **relative to** `INPUT_DIR` (e.g. `caseA/sub/phone.zip` → `caseA_sub_phone`), so two same-named zips in different subfolders never collide. A `_N` suffix is appended if a name still clashes.

### The master index

`index.html` is one table with these columns:

| Column | Meaning |
|---|---|
| **Source dir** | Top-level directory the zip came from (e.g. `caseA`). Falls back to `INPUT_DIR`'s name for zips sitting directly in it. |
| **Zip** | The zip's path relative to `INPUT_DIR`. |
| **Report folder** | Link to that extraction's output directory. |
| **Report** | Link straight to the LEAPP `index.html`. |
| **LAVA** | Link to the `_lava_data.lava` file. |
| **Status** | `ok` / `failed` / `skipped` / `dry-run`, color-coded. |

All links are **relative**, so the whole `OUTPUT_DIR` is portable — zip it, move it, or drop it on a share and the links still resolve. The report and LAVA files are located by scanning each output folder, so they're found wherever the tool writes them. If a tool doesn't emit a `.lava` file, that cell simply shows `—`.

---

## Behavior notes

- **Exit code** is `1` if any zip failed, otherwise `0` — convenient for scripting.
- **Ctrl-C** stops launching new work and still writes the master index for whatever finished.
- Rows for **failed** or **skipped** extractions still appear in the index; report/LAVA links show only when those files actually exist.
- The script is self-contained — copy it anywhere; it doesn't depend on this repository.
