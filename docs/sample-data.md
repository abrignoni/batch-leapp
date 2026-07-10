# Populating artifact `sample_data` from a test-image corpus

The LEAPP `__artifacts_v2__` metadata block supports an optional
`sample_data` key — a `dict[str, str]` of *sample-image name →
human-readable coverage note* (documented in iLEAPP's
`admin/docs/artifact_info_block.md`). It tells reviewers and future
maintainers which test images exercise an artifact and what they saw there:

```python
"sample_data": {
    "josh_ios_15": "iOS 15.4.1 | TikTok 30.1.0 | 32 rows",
    "pixel6_a13": "Android 13 | com.viber.voip vc 22450123 | 0 rows",
},
```

`sample_data_update.py` fills these entries **automatically** for iLEAPP and
ALEAPP from the coverage databases a `--coverage` batch produces. Each entry
records the device OS version, the owning app's version (when the artifact is
app-linked), and the row count the artifact produced on that image.

> **New to this?** Follow the hands-on developer walkthrough:
> [sample-data-walkthrough.md](sample-data-walkthrough.md) — step by step
> from a folder of zips to the metadata PR, with troubleshooting. This page
> is the reference.

## How the pipeline fits together

```
test zips ──► batch_leapp --coverage ──► batch_apps.sqlite ──► sample_data_update.py ──► PRs
             (runs the LEAPP + App       per artifact:          rewrites sample_data
              Inventory artifacts)       files matched,         in the repo checkouts
                                         rows, app, versions
```

The coverage aggregate records, per extraction:

| Table | Contents |
|---|---|
| `artifact_results` | per artifact function key: files matched by its search patterns + rows in its LAVA table (empty = no table) |
| `app_versions` | installed-app versions — iOS App Store metadata (`bundleShortVersionString`) and Android Play-services `versionCode` |
| `artifact_errors` | artifacts whose read crashed (`Reading X artifact had errors!` in the run log) — distinguishes a crash from a genuine 0-row result |
| `artifact_files` | which matched file belongs to which app, now at artifact granularity |

OS version comes from the App Inventory artifacts (`extractioninfo`), with
fallbacks to the `last_build` / `get_build` / `usagestatsversion` artifact
tables when the inventory didn't run.

## The corpus manifest: `samples.json`

Lives next to your test zips (it describes the *corpus*, not one batch run).
Only extractions registered here are ever written into artifact metadata, and
**only these keys are managed** — hand-written `sample_data` keys the tool
doesn't know are always left alone.

```json
{
  "version": 1,
  "samples": {
    "josh_ios_15": {
      "match": {"zip": "Josh_iPhone11_ios15.zip"},
      "platform": "ios",
      "os_version": null,
      "app_versions": {},
      "notes": "Josh Hickman public iOS 15.4.1 image"
    }
  }
}
```

- The **key** (`josh_ios_15`) becomes the `sample_data` key in the artifacts.
- `match.zip` is the input archive's file name — or its **corpus-relative
  path** (e.g. `"Otto/EXTRACTION_FFS 01/EXTRACTION_FFS.zip"`) when several
  archives share a name, as Cellebrite's `EXTRACTION_FFS.zip` exports do.
  Add `"sha256"` to pin the exact input. `--init-samples` picks the right
  form automatically.
- `os_version` / `app_versions` are **manual overrides** for when
  auto-detection fails or is wrong — an override always wins. Android
  overrides also replace the raw `versionCode` with your human-readable
  version string.
- `notes` is yours; the tool never reads or writes it.
- Bootstrap entries for unregistered extractions with `--init-samples`.

## Entry format

```
"<sample_key>": "<os> | <apps> | <rows>"
```

- `<os>` — `iOS 15.4.1` / `Android 13`, your verbatim override, or
  `OS unknown`.
- `<apps>` — the 1–3 apps owning the matched files, each with its version
  (`TikTok 30.1.0`, `com.viber.voip vc 22450123`, bare id when no version is
  known). Omitted for OS-level artifacts and for generic artifacts that
  matched 4+ apps.
- `<rows>` — `N rows`, `0 rows` (module ran, source present but empty), or
  `files found` (artifact produces no LAVA table, e.g. `output_types 'none'`).
  Artifacts that **crashed** on that image get no entry and a warning instead
  of a misleading `0 rows`.

## Runbook: adding a new extraction

```bash
# 0. drop the zip into the platform corpus dir and register it
python3 sample_data_update.py --db ... --samples /corpus/samples.json --init-samples
vi /corpus/samples.json        # review the stub key / add overrides

# 1. batch runs — one per platform; --skip-existing only processes new zips,
#    and the aggregate rebuilds batch_apps.sqlite over the whole output dir
python3 batch_leapp.py /corpus/ios /corpus_out/ios --leapp ~/GitHub/iLEAPP/ileapp.py --coverage --skip-existing
python3 batch_leapp.py /corpus/android /corpus_out/android --leapp ~/GitHub/ALEAPP/aleapp.py --coverage --skip-existing

# 2. fresh branch in each repo
git -C ~/GitHub/iLEAPP switch -c sample-data-refresh main
git -C ~/GitHub/ALEAPP switch -c sample-data-refresh main

# 3. dry run — review the proposed entries and warnings
python3 sample_data_update.py \
    --db /corpus_out/ios/batch_apps.sqlite --db /corpus_out/android/batch_apps.sqlite \
    --samples /corpus/samples.json --ileapp ~/GitHub/iLEAPP --aleapp ~/GitHub/ALEAPP

# 4. apply — runs the validation gates (ast re-parse, py_compile,
#    PluginLoader plugin-count, pylint new-warning check); restores the
#    original files if any gate fails
python3 sample_data_update.py ... --apply

# 5. idempotency check: run step 4 again — it must report 0 changes
# 6. fix any reported pre-existing pylint warnings in touched files
#    (zero-behavior pragmas; both repos' CI re-lints every changed file)
# 7. review `git diff`, commit, push, open the PRs (review/merge stays manual)
```

## Rules and gotchas

- **Only artifact files change.** The updater edits `scripts/artifacts/*.py`
  metadata only — never framework code. Edits preserve each file's quote
  style, line endings (CRLF kept) and key order, and re-running with the same
  inputs is a no-op.
- **Managed vs hand-written keys.** A key already in an artifact's
  `sample_data` that matches a registered sample name is *managed*: its value
  is regenerated (the dry run warns when that replaces hand-written prose —
  move such prose to `notes` first if you want to keep it). Unknown keys are
  never touched; a managed key is removed only when its sample re-ran and no
  longer matches the artifact.
- **One LEAPP version per corpus output.** After upgrading a LEAPP checkout,
  regenerate that platform's output directory from scratch instead of
  `--skip-existing`, so every run's artifact keys and tables match the
  current repo (stale outputs otherwise surface as duplicate-match errors or
  "not in repo" warnings; `--pick-latest` chooses the newest run on
  duplicates).
- **Android versions are `versionCode`.** No file in an Android extraction
  yields the human-readable `versionName`; the integer `versionCode` comes
  from Play-services `gass.db`. Devices without Google services fall back to
  a bare package name — use the `app_versions` override, or a future
  ALEAPP App Inventory update that emits `packages.xml`'s `@version`.
- **iOS versions cover App Store apps.** `iTunesMetadata.plist` doesn't exist
  for system apps; those render as bare bundle ids unless overridden.
- Scope the run while testing with `--sample <key>` / `--module <stem>`, and
  point the PluginLoader smoke test at a specific interpreter with
  `--smoke-python` when the repos' dependencies live outside the default
  `python3`.
