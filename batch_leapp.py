#!/usr/bin/env python3
"""
batch_leapp.py — recursively find every .zip in a directory and run a LEAPP
tool (iLEAPP / ALEAPP / RLEAPP / VLEAPP) on each.

Each zip gets its own output directory so you end up with a folder full of
ready-to-review LEAPP report directories, plus a master index.html linking them.

Example:
    python batch_leapp.py /Volumes/Cases/extractions /Volumes/Cases/reports \
        --leapp /path/to/iLEAPP/ileapp.py

The LEAPP tool is invoked as:  python <x>leapp.py -t zip -i <zip> -o <out dir>
which is the shared CLI of iLEAPP, ALEAPP, RLEAPP and VLEAPP.
"""

import argparse
import html
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


def find_zips(root: Path):
    """Return every *.zip under root, recursively, case-insensitively, sorted."""
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".zip")


# Display names for the known LEAPP tools, keyed by script stem.
LEAPP_NAMES = {
    "ileapp": "iLEAPP",
    "aleapp": "ALEAPP",
    "rleapp": "RLEAPP",
    "vleapp": "VLEAPP",
}


def tool_name_from(leapp_path: Path) -> str:
    """Derive a display name (e.g. 'ALEAPP') from the LEAPP script's filename."""
    stem = leapp_path.stem.lower()
    if stem in LEAPP_NAMES:
        return LEAPP_NAMES[stem]
    # Generic *leapp.py fallback, else just the upper-cased stem.
    if stem.endswith("leapp") and len(stem) > len("leapp"):
        return stem[:-len("leapp")] + "LEAPP"
    return stem.upper()


def unique_dir(parent: Path, name: str) -> Path:
    """Return a non-existing directory path under parent based on name."""
    candidate = parent / name
    n = 1
    while candidate.exists():
        candidate = parent / f"{name}_{n}"
        n += 1
    return candidate


def locate_report_files(dest: Path):
    """Find the report index.html and the .lava file iLEAPP wrote under dest.

    iLEAPP writes a timestamped 'iLEAPP_Reports_*' folder inside dest; the
    report files live in there. Return (index_html, lava_file) as Paths or None.
    """
    indexes = sorted(dest.rglob("index.html"))
    lavas = sorted(dest.rglob("*.lava"))
    return (indexes[0] if indexes else None,
            lavas[0] if lavas else None)


def href(target: Path, base: Path) -> str:
    """URL-quoted relative href from base to target (forward slashes)."""
    rel = target.relative_to(base).as_posix()
    return quote(rel)


def run_job(job: dict, timeout, capture: bool) -> dict:
    """Run one iLEAPP subprocess. When capture is True (parallel mode) the
    combined output is captured and written to a per-job log file so concurrent
    runs don't garble the terminal. Returns a result dict."""
    start = time.time()
    try:
        if capture:
            proc = subprocess.run(
                job["cmd"], timeout=timeout, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            output = proc.stdout
        else:
            proc = subprocess.run(job["cmd"], timeout=timeout)
            output = None
        rc, error = proc.returncode, None
    except subprocess.TimeoutExpired as e:
        output = e.output.decode(errors="replace") if isinstance(e.output, bytes) else e.output
        rc, error = None, "timeout"

    if capture and output:
        try:
            (job["dest"] / job["log_name"]).write_text(output, encoding="utf-8")
        except OSError:
            pass
    return {"job": job, "rc": rc, "elapsed": time.time() - start, "error": error}


def write_index(output_dir: Path, entries: list, tool: str = "LEAPP") -> Path:
    """Write a master index.html linking every extraction's report folder,
    its index.html and its .lava file. Returns the path written."""
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"{tool} batch report index"

    def cell_link(target, base, label):
        if target is None:
            return '<span class="missing">—</span>'
        return f'<a href="{href(target, base)}">{html.escape(label)}</a>'

    rows = []
    for e in entries:
        status_class = {
            "ok": "ok", "failed": "failed",
            "skipped": "skipped", "dry-run": "dry",
        }.get(e["status"], "")
        rows.append(
            "<tr>"
            f'<td>{html.escape(e["root"])}</td>'
            f'<td>{html.escape(e["zip"])}</td>'
            f'<td>{cell_link(e["dest"], output_dir, e["dest"].name)}</td>'
            f'<td>{cell_link(e["index"], output_dir, "report")}</td>'
            f'<td>{cell_link(e["lava"], output_dir, "lava")}</td>'
            f'<td class="{status_class}">{html.escape(e["status"])}</td>'
            "</tr>"
        )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 2rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .meta {{ color: #666; margin-bottom: 1.25rem; font-size: .9rem; }}
  table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }}
  th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #e3e3e3; }}
  th {{ background: #f5f5f7; position: sticky; top: 0; }}
  tr:hover td {{ background: #fafafa; }}
  a {{ color: #0066cc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .missing {{ color: #bbb; }}
  td.ok {{ color: #137333; font-weight: 600; }}
  td.failed {{ color: #c5221f; font-weight: 600; }}
  td.skipped {{ color: #a86400; }}
  td.dry {{ color: #666; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<div class="meta">{len(entries)} extraction(s) &middot; generated {html.escape(generated)}</div>
<table>
<thead>
<tr><th>Source dir</th><th>Zip</th><th>Report folder</th><th>Report</th><th>LAVA</th><th>Status</th></tr>
</thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</body>
</html>
"""
    index_path = output_dir / "index.html"
    index_path.write_text(doc, encoding="utf-8")
    return index_path


def main():
    parser = argparse.ArgumentParser(
        description="Recursively run a LEAPP tool (iLEAPP/ALEAPP/RLEAPP/VLEAPP) "
                    "on every zip in a directory.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_dir", type=Path, help="Directory to search recursively for .zip files")
    parser.add_argument("output_dir", type=Path, help="Directory to write the report folders into")
    parser.add_argument(
        "--leapp", "--ileapp",
        dest="leapp",
        type=Path,
        default=Path("ileapp.py"),
        help="Path to the LEAPP script, e.g. ileapp.py / aleapp.py / rleapp.py / vleapp.py",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used to run the LEAPP tool",
    )
    parser.add_argument(
        "-t", "--type",
        default="zip",
        help="iLEAPP extraction type passed with -t",
    )
    parser.add_argument(
        "-j", "--jobs",
        type=int,
        default=1,
        help="Number of iLEAPP runs to execute in parallel",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Per-zip timeout in seconds (default: no timeout)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip a zip if its output directory already exists and is non-empty",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would run without invoking the LEAPP tool",
    )
    args = parser.parse_args()

    input_dir = args.input_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    leapp = args.leapp.expanduser().resolve()
    tool = tool_name_from(leapp)
    log_name = f"{tool.lower()}_run.log"

    if not input_dir.is_dir():
        parser.error(f"input_dir is not a directory: {input_dir}")
    if not args.dry_run and not leapp.is_file():
        parser.error(f"LEAPP script not found: {leapp}  (use --leapp to point at it)")

    zips = find_zips(input_dir)
    if not zips:
        print(f"No .zip files found under {input_dir}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Tool: {tool}  ({leapp})")
    print(f"Found {len(zips)} zip file(s) under {input_dir}")
    print(f"Reports will be written under {output_dir}\n")

    succeeded, failed, skipped = [], [], []
    entries = {}   # keyed by dest so parallel results land in the right row
    jobs = []

    # --- Phase 1: assign output dirs and decide what to run (sequential,
    # so unique_dir() can't race) ---
    for zip_path in zips:
        # Name the per-zip output dir after the zip's path relative to input_dir
        # so two same-named zips in different folders don't collide.
        rel = zip_path.relative_to(input_dir)
        # "Source dir" = the top-level directory the zip lives under; if the zip
        # sits directly in input_dir, fall back to input_dir's own name.
        root = rel.parts[0] if len(rel.parts) > 1 else input_dir.name
        stem = "_".join(rel.with_suffix("").parts)
        dest = output_dir / stem

        if args.skip_existing and dest.exists() and any(dest.iterdir()):
            print(f"skip (exists): {rel.as_posix()}")
            skipped.append(zip_path)
            idx, lava = locate_report_files(dest)
            entries[dest] = {"root": root, "zip": rel.as_posix(), "dest": dest,
                             "index": idx, "lava": lava, "status": "skipped"}
            continue

        dest = unique_dir(output_dir, stem)
        dest.mkdir(parents=True, exist_ok=True)
        cmd = [args.python, str(leapp), "-t", args.type,
               "-i", str(zip_path), "-o", str(dest)]

        if args.dry_run:
            print(f"dry-run: {' '.join(cmd)}")
            entries[dest] = {"root": root, "zip": rel.as_posix(), "dest": dest,
                             "index": None, "lava": None, "status": "dry-run"}
            continue

        jobs.append({"zip": zip_path, "rel": rel, "root": root,
                     "dest": dest, "cmd": cmd, "log_name": log_name})

    # --- Phase 2: execute (parallel when --jobs > 1) ---
    workers = max(1, args.jobs)
    capture = workers > 1
    if jobs:
        print(f"\nRunning {len(jobs)} job(s) with {workers} worker(s)...\n")

    def record(res):
        job = res["job"]
        idx, lava = locate_report_files(job["dest"])
        rel_str = job["rel"].as_posix()
        if res["error"] == "timeout":
            print(f"  TIMEOUT  {rel_str}  (after {args.timeout}s)")
            failed.append((job["zip"], "timeout"))
            status = "failed"
        elif res["rc"] == 0:
            print(f"  OK       {rel_str}  ({res['elapsed']:.0f}s)")
            succeeded.append(job["zip"])
            status = "ok"
        else:
            log_hint = f", see {log_name}" if capture else ""
            print(f"  FAILED   {rel_str}  (exit {res['rc']}{log_hint})")
            failed.append((job["zip"], f"exit {res['rc']}"))
            status = "failed"
        entries[job["dest"]] = {"root": job["root"], "zip": rel_str,
                                "dest": job["dest"], "index": idx,
                                "lava": lava, "status": status}

    try:
        if workers == 1:
            for job in jobs:
                print(f"running: {job['rel'].as_posix()}")
                record(run_job(job, args.timeout, capture=False))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run_job, job, args.timeout, capture): job
                           for job in jobs}
                for fut in as_completed(futures):
                    record(fut.result())
    except KeyboardInterrupt:
        print("\nInterrupted by user — writing index for completed runs.")

    # --- Phase 3: master index + summary (entries ordered to match zip order) ---
    ordered = [entries[d] for d in
               sorted(entries, key=lambda d: d.as_posix())]
    if ordered:
        index_path = write_index(output_dir, ordered, tool)
        print(f"\nWrote master index: {index_path}")

    print("=" * 60)
    print(f"Done. {len(succeeded)} ok, {len(failed)} failed, {len(skipped)} skipped.")
    if failed:
        print("\nFailures:")
        for zip_path, why in failed:
            print(f"  - {zip_path}  ({why})")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
