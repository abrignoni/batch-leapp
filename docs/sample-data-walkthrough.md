# Step-by-step: populating `sample_data` from your own test images

This is the hands-on walkthrough for LEAPP developers. It takes you from "a
folder of test extractions" to "an iLEAPP/ALEAPP pull request where every
artifact that parsed your images documents it" — like this, in each
artifact's `__artifacts_v2__` block:

```python
"sample_data": {
    "ctf2020_ios12": "iOS 12.4 | 107 rows",
    "dexter_ios18": "iOS 18.3.2 | TikTok - Videos, Shop & LIVE 41.8.0 | 63 rows",
    "hc_ios18_7": "iOS 18.7.8 | 126 rows",
},
```

Each entry says: on this test image (key), running this artifact found data
from this OS version, this app version, and produced this many rows. That
tells reviewers and future maintainers exactly which images exercise an
artifact — and where to look when they need sample data.

The reference documentation (samples.json fields, the entry grammar, merge
rules) lives in [sample-data.md](sample-data.md). This guide is the
procedure.

---

## 0. What you need

| Requirement | Notes |
|---|---|
| **batch-leapp checkout** | this repo — `git clone https://github.com/abrignoni/batch-leapp` |
| **iLEAPP and/or ALEAPP source checkout** | `main`, clean tree. Compiled binaries won't work — coverage needs `scripts/alternate_artifacts/appInventory.py` from the repo |
| **Python 3.9+** with the LEAPP's `requirements.txt` installed | quick check: `cd iLEAPP && PYTHONPATH=. python3 -c "from scripts.plugin_loader import PluginLoader; print(len(list(PluginLoader().plugins)))"` should print ~600 without errors |
| **pylint** (optional but recommended) | the apply step uses it to guarantee the PR passes CI |
| **Test images** | zip/tar/gz archives of full file system extractions |
| **Disk space on an internal drive** | reports run roughly 2–6 GB per image |

> **⚠️ Where your outputs live matters.** SQLite cannot write on exFAT or
> network volumes (you'll get `attempt to write a readonly database` from
> every run). Your **images can stay** on an external/exFAT drive; the
> **output directory must be on your internal disk** (APFS/NTFS/ext4).

## 1. Organize the corpus

One directory per platform, zips anywhere beneath it (subfolders are fine —
they're searched recursively):

```
~/corpus/ios/                        # or an external drive — read-only is fine
    Josh_iPhone11_ios15.zip
    Otto/EXTRACTION_FFS 01/EXTRACTION_FFS.zip
    ...
~/corpus/android/
    Pixel6_android13.zip
    ...
```

Notes from the field:

- **Password-protected zips parse as almost nothing.** Decrypt them first or
  plan to leave them unregistered.
- **Duplicate copies of the same image** waste a run each. If you suspect
  duplicates, the batch manifest's SHA-256 column will confirm it afterwards
  — register only one.
- Multiple archives may share a file name (every Cellebrite export is
  `EXTRACTION_FFS.zip`). That's fine — registration handles it by path.

## 2. Run the batch with `--coverage`

One command per platform. Point `--leapp` at the LEAPP script in your source
checkout; the output directory goes on your **internal** disk:

```bash
cd batch-leapp

python3 batch_leapp.py ~/corpus/ios ~/batch_out_ios \
    --leapp ~/GitHub/iLEAPP/ileapp.py --coverage --timeout 10800

python3 batch_leapp.py ~/corpus/android ~/batch_out_android \
    --leapp ~/GitHub/ALEAPP/aleapp.py --coverage --timeout 10800
```

- Expect **5–15 minutes per image** (a 12-image / 335 GB corpus takes about
  1¾ hours). `--timeout 10800` stops any single hung image at 3 h without
  killing the batch.
- `--coverage` does two extra things: it enables the App Inventory artifacts
  on every run, and at the end it aggregates every report into
  **`batch_apps.sqlite`** at the output root — the database everything below
  reads. Success looks like:

```
Done. 12 ok, 0 failed, 0 skipped.
coverage: aggregated 12 extraction(s) (12 with inventory data) into .../batch_apps.sqlite
```

- If a run shows `failed`, or finishes suspiciously fast (an encrypted or
  corrupt zip finishes in under a minute), note it — you'll simply not
  register that image in step 3.

## 3. Register your images in `samples.json`

`samples.json` lives in the **corpus root, next to the zips** (it describes
the corpus, not one batch run — plain JSON is fine even on exFAT). Bootstrap
it:

```bash
python3 sample_data_update.py \
    --db ~/batch_out_ios/batch_apps.sqlite \
    --samples ~/corpus/ios/samples.json --init-samples
```

This writes one stub per extraction it found. **Now open the file and edit
it — this is the human step of the whole procedure:**

```json
{
  "version": 1,
  "samples": {
    "josh_ios_15": {
      "match": {"zip": "Josh_iPhone11_ios15.zip", "sha256": "bda2..."},
      "platform": "ios",
      "os_version": null,
      "app_versions": {},
      "notes": "Josh Hickman public iOS 15.4.1 image"
    }
  }
}
```

1. **Rename the keys.** The key becomes the permanent `sample_data` dict key
   inside the LEAPP artifacts, visible to everyone. Prefer short names that
   hint at the source and OS: `josh_ios_15`, `otto_ios17`, `pixel6_a13` —
   not `extraction_ffs_2`.
2. **Delete stubs you don't want in the metadata**: encrypted images,
   duplicate acquisitions of the same device, broken runs. Only registered
   samples are ever written to the repos.
3. **Leave `os_version: null`** unless detection failed (you'll see an
   `OS unknown` warning later) — then set it verbatim, e.g. `"iOS 15.4.1"`.
4. `app_versions` overrides per-app versions when auto-detection can't
   (Android devices without Play services; sideloaded iOS apps):
   `{"com.some.app": "9.9.9"}`.
5. Don't touch `match.zip`/`sha256` — `--init-samples` already picked the
   right form (basename, or corpus-relative path when several archives share
   a name, plus a SHA-256 pin).

## 4. Dry-run and review

Never apply blind. The dry run prints **every entry it would write** plus
warnings:

```bash
python3 sample_data_update.py \
    --db ~/batch_out_ios/batch_apps.sqlite \
    --samples ~/corpus/ios/samples.json \
    --ileapp ~/GitHub/iLEAPP
```

(Use `--aleapp ~/GitHub/ALEAPP` for Android; both flags together are fine —
extractions route to the right repo automatically.)

What to check in the output:

- **The proposed entries look sane** — a quick skim:
  ```
      added   sms.py::sms
              ctf2020_ios12: iOS 12.4 | 107 rows
              hc_ios18_7: iOS 18.7.8 | 126 rows
  ```
- `'X' errored during the Y run — no entry written` — normal; those are real
  artifact crashes on that image (worth fixing separately, but the tool
  correctly refuses to record a false "0 rows").
- `extraction '...' is not registered in samples.json — skipped` — expected
  for images you deliberately left out; unexpected names mean you forgot one.
- `replacing hand-written text under managed key '...'` — one of your keys
  collides with a note someone typed by hand. Either rename your sample key,
  or move that prose into the artifact's `notes` field first.
- `artifact '...' is in the coverage DB but not in <repo>` — your batch ran
  against a different LEAPP version than the checkout you're updating.
  Regenerate the output directory with the current checkout (see §8).

Scope the run while iterating: `--sample josh_ios_15` and/or
`--module tikTok` limit what's processed.

## 5. Apply on a fresh branch

```bash
git -C ~/GitHub/iLEAPP switch -c sample-data-refresh main

python3 sample_data_update.py \
    --db ~/batch_out_ios/batch_apps.sqlite \
    --samples ~/corpus/ios/samples.json \
    --ileapp ~/GitHub/iLEAPP \
    --apply
```

`--apply` edits the artifact files, then validates automatically:

1. re-parses every changed file and verifies the written `sample_data`
   equals what it computed,
2. byte-compiles them,
3. reloads the PluginLoader and checks the plugin count didn't change,
4. runs pylint before/after and **fails if the edits introduced any new
   warning** — on failure it restores every file untouched.

Expected tail:

```
validate: 239 file(s) re-parsed, verified and compiled
validate: PluginLoader OK (598 plugins)
iLEAPP: applied and validated 239 file(s)
```

If the PluginLoader smoke says it *could not run* (missing deps under your
default `python3`), point it at the right interpreter:
`--smoke-python python3.12`.

**Idempotency check** — run the exact same `--apply` again. It must say:

```
iLEAPP: 0 artifact change(s) across 0 file(s)
```

Anything else means the inputs changed between runs — stop and investigate.

## 6. Clear pre-existing lint debt (if reported)

Both repos' CI runs `pylint --disable=C,R` on **every changed file** — and
touching a file re-lints warnings that were already there. The apply step
lists them:

```
validate: pre-existing pylint warnings in touched files (CI re-lints them;
fix with zero-behavior pragmas before the PR):
    scripts/artifacts/Ph1BasicAssetData.py: W0611
    scripts/artifacts/Ph1BasicAssetData.py: W0613
```

Fix each listed file with a **file-scoped pragma naming exactly its
pre-existing symbols** as the first line — a comment, zero behavior change:

```python
# pylint: disable=W0611,W0613,W0631
```

Then confirm the whole changeset is green, exactly like CI will:

```bash
cd ~/GitHub/iLEAPP
PYTHONPATH=. pylint $(git diff --name-only main) --disable=C,R
# must exit 0 / 10.00
```

## 7. Commit, push, open the PR

```bash
git -C ~/GitHub/iLEAPP add scripts/artifacts
git -C ~/GitHub/iLEAPP commit -m "Populate sample_data from <N>-image iOS test corpus"
git -C ~/GitHub/iLEAPP push -u origin sample-data-refresh
gh pr create --repo abrignoni/iLEAPP --base main --head sample-data-refresh \
    --title "Populate sample_data from <N>-image iOS test corpus"
```

In the PR description, mention: how many images and their OS range, that the
change is **metadata-only** (plus lint pragmas if any), that hand-written
notes were preserved, and that a second `--apply` reports zero changes.
Review and merge stay with the maintainers as usual.

## 8. Adding another image later (the whole point)

```bash
# 1. drop the zip into the corpus dir
cp NewPhone_ios19.zip ~/corpus/ios/

# 2. batch only processes what's new
python3 batch_leapp.py ~/corpus/ios ~/batch_out_ios \
    --leapp ~/GitHub/iLEAPP/ileapp.py --coverage --skip-existing

# 3. register it, rename the stub key, review
python3 sample_data_update.py --db ~/batch_out_ios/batch_apps.sqlite \
    --samples ~/corpus/ios/samples.json --init-samples
vi ~/corpus/ios/samples.json

# 4. dry-run -> apply -> idempotency -> PR   (steps 4-7 above)
```

Only artifacts touched by the new image change — everything else is
untouched, so the refresh PR stays small.

**One rule:** after you **update your LEAPP checkout**, don't `--skip-existing`
— delete (or redirect) the platform's output directory and regenerate it, so
every run's artifact keys and tables match the code you're editing. Mixed-
version outputs surface as duplicate-match errors or "not in repo" warnings.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| every run fails instantly with `sqlite3.OperationalError: attempt to write a readonly database` | output directory is on exFAT or a network share — move it to your internal disk (§0) |
| an image finishes in seconds and detects no OS | password-protected or corrupt archive — decrypt it, or don't register it |
| `sample 'X' matches N extractions ... Remove stale output folders or pass --pick-latest` | the same image was processed more than once (re-runs, old folders). Clean the output dir, or `--pick-latest` |
| `artifact '...' is in the coverage DB but not in <repo>` | batch ran with a different LEAPP version — regenerate the output dir with the current checkout |
| `extractions.zip` / `sha256` empty in an old `batch_apps.sqlite` | produced by a pre-fix batch-leapp — update batch-leapp, or just re-aggregate: `python3 batch_coverage.py ~/batch_out_ios` |
| PluginLoader smoke "could not run … missing deps" | your default `python3` lacks the repo's requirements — `--smoke-python <interpreter that has them>` |
| CI fails on files you touched, with warnings you didn't add | pre-existing lint debt — §6 |
| two samples' entries look identical | you registered duplicate copies of one image — check their `sha256` in `manifest.json`, drop one |

## Quick reference

```
entry     = "<os> | <apps> | <rows>"
os        = "iOS 15.4.1" / "Android 13" / your override / "OS unknown"
apps      = 1-3 owning apps, each "Name version" (iOS) or "pkg vc <code>" (Android);
            omitted for OS-level artifacts and generic (4+ app) matchers
rows      = "<n> rows" | "0 rows" (source present, empty) | "files found" (no LAVA output)
crashed artifact on that image -> no entry + a warning
```

- Managed keys = the keys in your samples.json. Everything else in an
  artifact's `sample_data` is never touched.
- Android versions are integer `versionCode`s (from Play-services gass.db) —
  no Android file yields the marketing version; use `app_versions` overrides
  when you need one.
- Full reference: [sample-data.md](sample-data.md).
