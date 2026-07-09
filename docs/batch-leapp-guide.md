# Batch LEAPP: process all your test images at once (and find the apps we're not parsing)

**Short version:** Batch LEAPP points iLEAPP or ALEAPP at a folder full of extractions and runs the tool on every one of them, hands-free. One command, one report per image, one master index. And now with the new `--coverage` mode it also builds a database that tells you which installed apps the LEAPPs are NOT parsing yet. That list is our module-writing to-do list.

**Long version:** keep reading.

## What is Batch LEAPP?

When a new LEAPP release comes out I like to run it against my collection of test images to see what changed, what broke, and what improved. Doing that by hand — one extraction at a time, one output folder at a time — gets old fast. Batch LEAPP automates it:

- Finds every extraction archive (.zip, .tar, .gz) under a folder, recursively.
- Runs the LEAPP tool of your choice on each one (iLEAPP, ALEAPP, RLEAPP, or VLEAPP — same command line for all of them).
- Gives every extraction its own output folder so nothing collides.
- Writes a master `index.html` linking every report, plus a manifest (CSV and JSON) with the SHA-256 of every input archive, per-run timing, and status.

It is smart enough to skip things that aren't extractions (a lone gzipped log file is not a filesystem image) and it never descends into its own output folders.

🔗 Get it here: https://www.leapps.org/releases#section-batch-leapp

## The original use: release regression testing

You need Python 3 and a source checkout of the LEAPP you want to run. Then:

```
python3 batch_leapp.py ~/test_images ~/reports --leapp ~/iLEAPP/ileapp.py
```

That's it. Every archive under `~/test_images` gets parsed, every report lands under `~/reports`, and when it's done you open `~/reports/index.html` and start reviewing.

The options I actually use:

| Option | What it does |
|---|---|
| `-j 2` | Run two images in parallel. Big time saver when you have the disk for it. |
| `--skip-existing` | Resume a partial batch. Already-done images are skipped. |
| `--timeout 3600` | Don't let one bad image hold the whole batch hostage. |
| `--dry-run` | Show me the exact commands without running anything. |
| `-- <args>` | Anything after a literal `--` goes straight to every LEAPP run. |

Two lessons from my own runs, so you don't learn them the hard way:

1. **Don't stop the terminal mid-batch** unless you mean it. If you do, delete the partial output folder for the interrupted image and rerun with `--skip-existing` — the finished ones are left alone and only the interrupted one reruns.
2. **The tooling will catch mislabeled images.** I had an "Android" extraction in my collection that turned out to be an iPhone. More on how the coverage mode caught that below.

## The new use: parsing-coverage analysis

Here is the question that started all this: **out of all the apps installed on my test devices, which ones do the LEAPPs actually parse — and which ones are we missing?**

Answering it needs three things per extraction: the list of installed apps, the list of every file each app owns, and the list of files the LEAPP modules actually touched. The first two now come from a developer-only artifact module (`appInventory.py`, shipped in `scripts/alternate_artifacts/` in both iLEAPP and ALEAPP — regular users never see it). The third one the LEAPPs already record on every run. Batch LEAPP glues it all together:

```
python3 batch_leapp.py ~/test_images ~/reports --leapp ~/iLEAPP/ileapp.py --coverage -j 2
```

The `--coverage` flag does two things:

1. **Turns on the App Inventory artifacts** for every run (via the LEAPPs' `--custom_artifacts_path` option — no changes to your checkout, nothing left behind).
2. **Aggregates everything at the end** into a single `batch_apps.sqlite` at the output root, with views that answer the question directly — plus a `batch_apps.lava` file so you can open the whole thing in LAVA and browse it like any other project.

Already ran a batch and just want to rebuild the analysis? No need to reparse anything:

```
python3 batch_coverage.py ~/reports
```

A few practical notes:

- Coverage needs **source checkouts** of iLEAPP/ALEAPP (the compiled binaries don't bundle the inventory module).
- iOS and Android batches run separately (one tool per batch), but you can send them to the same output folder and the coverage database will cover both.
- If an extraction comes back with no inventory data, look closer at it. That's how I found out my "Felix" Android image was actually an iPhone 8 running iOS 17.6.1 — no `packages.xml`, no `build.prop`, because there was no Android in there at all. The tooling flagged it on the first run.

What do you do with `batch_apps.sqlite` once you have it? That's the second guide.

Questions? Find me at https://abrignoni.github.io or email abrignoni[at]duck[dot]com.
