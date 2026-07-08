#!/usr/bin/env python3
"""
batch_coverage.py — app parsing-coverage support for batch_leapp.

Two responsibilities:

1. enable_coverage(): make a LEAPP run include the developer-only
   "App Inventory" artifacts (scripts/alternate_artifacts/appInventory.py in
   the iLEAPP/ALEAPP repos), which write extractioninfo /
   installedappinventory / appfileinventory tables into each run's
   _lava_artifacts.db.
   - iLEAPP has a --custom_artifacts_path CLI option, so the module is
     enabled with extra arguments.
   - ALEAPP has no such option yet, so the module is staged (copied) into
     scripts/artifacts/ for the duration of the batch and removed afterwards.

2. aggregate(): after a batch (or standalone on any batch output folder),
   walk every report's _lava_artifacts.db and merge the inventory tables,
   the framework's artifact-match registry tables and the manifest into a
   single batch_apps.sqlite with views that answer "which installed apps
   were NOT parsed by the tooling?".

Standalone usage (re-aggregate an existing batch output directory):

    python3 batch_coverage.py /path/to/batch_output
"""

import argparse
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

COVERAGE_DB_NAME = "batch_apps.sqlite"
INVENTORY_MODULE = "appInventory"

# Android package-owned locations (mirrors the ALEAPP inventory artifact).
_PKG = r'([A-Za-z0-9_][A-Za-z0-9_.\-]*)'
_ANDROID_LOCATIONS = tuple(re.compile(p) for p in (
    r'Android/data/' + _PKG + r'(?=/|$)',
    r'Android/media/' + _PKG + r'(?=/|$)',
    r'Android/obb/' + _PKG + r'(?=/|$)',
    r'data/data/' + _PKG + r'(?=/|$)',
    r'data/user/\d+/' + _PKG + r'(?=/|$)',
    r'data/user_de/\d+/' + _PKG + r'(?=/|$)',
    r'data/app/(?:~~[^/]+/)?' + _PKG + r'-[^/]+(?=/|$)',
))

# iOS container GUIDs (mirrors the iLEAPP inventory artifact).
_IOS_GUID_RE = re.compile(
    r'containers/(?:Bundle/Application|Data/Application|Shared/AppGroup|Data/PluginKitPlugin)'
    r'/([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})(?=/|$)',
    re.IGNORECASE)
_ITUNES_DOMAIN_RE = re.compile(r'^AppDomain(?:Group|Plugin)?-([^/]+)/')


# --------------------------------------------------------------------------
# Enabling the inventory artifacts on a LEAPP run
# --------------------------------------------------------------------------

def enable_coverage(leapp: Path, tool: str, log=print):
    """Prepare a LEAPP source checkout to run the App Inventory artifacts.

    Returns (extra_args, cleanup) where extra_args is a list to append to
    every LEAPP command line and cleanup is a callable to run once the whole
    batch is finished. Logs a warning and returns ([], noop) when the tool
    has no inventory support (RLEAPP/VLEAPP, compiled binaries, or a checkout
    that predates the module).
    """
    def noop():
        return None

    if leapp.suffix.lower() != ".py":
        log("coverage: only LEAPP source checkouts are supported "
            "(compiled binaries do not bundle scripts/alternate_artifacts) — "
            "inventory artifacts disabled, aggregation will still run.")
        return [], noop

    alternate = leapp.resolve().parent / "scripts" / "alternate_artifacts" / f"{INVENTORY_MODULE}.py"
    if not alternate.is_file():
        log(f"coverage: {alternate} not found — this {tool} checkout has no "
            "App Inventory module; inventory artifacts disabled.")
        return [], noop

    if tool == "iLEAPP":
        return ["--custom_artifacts_path", str(alternate.parent)], noop

    if tool == "ALEAPP":
        staged = alternate.parent.parent / "artifacts" / alternate.name
        if staged.exists():
            log(f"coverage: {staged} already present, leaving it in place.")
            return [], noop
        shutil.copy2(alternate, staged)
        log(f"coverage: staged {alternate.name} into {staged.parent}")

        def cleanup():
            try:
                staged.unlink()
                log(f"coverage: removed staged {staged}")
            except OSError as ex:
                log(f"coverage: could not remove staged {staged}: {ex}")
        return [], cleanup

    log(f"coverage: {tool} has no App Inventory module — inventory artifacts "
        "disabled, aggregation will still run.")
    return [], noop


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE extractions (
    extraction_id   INTEGER PRIMARY KEY,
    output_folder   TEXT,   -- batch destination folder name
    report_folder   TEXT,   -- LEAPP report folder name
    report_path     TEXT,   -- absolute path of the report folder
    zip             TEXT,   -- input archive (from manifest.json)
    sha256          TEXT,
    tool            TEXT,
    tool_version    TEXT,
    extraction_type TEXT,
    input_name      TEXT,
    input_path      TEXT,
    os_version      TEXT,
    device_name     TEXT,
    model           TEXT,
    manufacturer    TEXT,
    serial_number   TEXT,
    time_zone       TEXT,
    build_id        TEXT,
    has_inventory   INTEGER -- 1 when the App Inventory artifacts ran
);
CREATE TABLE installed_apps (
    extraction_id  INTEGER,
    app_id         TEXT,
    location_type  TEXT,
    container_path TEXT,
    container_uuid TEXT,
    install_time   INTEGER,
    update_time    INTEGER,
    installer      TEXT,
    data_source    TEXT
);
CREATE TABLE app_files (
    extraction_id INTEGER,
    app_id        TEXT,
    location_type TEXT,
    container_uuid TEXT,
    file_path     TEXT,
    file_size     TEXT,
    modified_time TEXT
);
CREATE TABLE artifact_patterns (
    extraction_id INTEGER,
    pattern_id    INTEGER,
    module_name   TEXT,
    artifact_name TEXT,
    regex         TEXT
);
CREATE TABLE artifact_files (
    extraction_id INTEGER,
    file_path     TEXT,
    app_id        TEXT,   -- resolved by the aggregator
    module_name   TEXT    -- one row per (file, matching module)
);
CREATE INDEX idx_app_files ON app_files(extraction_id, app_id);
CREATE INDEX idx_artifact_files ON artifact_files(extraction_id, app_id);
CREATE INDEX idx_installed ON installed_apps(extraction_id, app_id);

-- How many different apps each module matched files for, per extraction.
-- Modules that touch many apps (userDefaults, appGrouplisting, packageInfo,
-- the framework's generic plist/db sweeps, ...) are 'generic': they match
-- every app's housekeeping files without decoding app content, so they must
-- not make an app count as parsed.
CREATE VIEW v_module_app_spread AS
SELECT extraction_id, module_name,
       COUNT(DISTINCT app_id) AS apps_matched,
       COUNT(DISTINCT app_id) >= 10 AS is_generic
FROM artifact_files
WHERE app_id != ''
GROUP BY extraction_id, module_name;

-- one row per (extraction, app) with disk/matched file counts.
-- files_matched counts any artifact match; files_matched_specific counts
-- only matches by app-specific (non-generic) modules.
CREATE VIEW v_app_coverage AS
SELECT u.extraction_id, u.app_id,
       (SELECT COUNT(*) FROM app_files f
         WHERE f.extraction_id = u.extraction_id AND f.app_id = u.app_id) AS files_on_disk,
       (SELECT COUNT(DISTINCT m.file_path) FROM artifact_files m
         WHERE m.extraction_id = u.extraction_id AND m.app_id = u.app_id) AS files_matched,
       (SELECT COUNT(DISTINCT m.file_path) FROM artifact_files m
         JOIN v_module_app_spread s
              ON s.extraction_id = m.extraction_id
             AND s.module_name = m.module_name
         WHERE m.extraction_id = u.extraction_id AND m.app_id = u.app_id
           AND s.is_generic = 0) AS files_matched_specific,
       EXISTS (SELECT 1 FROM installed_apps i
         WHERE i.extraction_id = u.extraction_id AND i.app_id = u.app_id) AS in_inventory
FROM (SELECT DISTINCT extraction_id, app_id FROM app_files WHERE app_id != ''
      UNION
      SELECT DISTINCT extraction_id, app_id FROM installed_apps WHERE app_id != '') u;

-- apps with data on disk that no app-specific artifact touched
CREATE VIEW v_apps_not_parsed AS
SELECT e.tool, e.input_name, e.os_version, c.*
FROM v_app_coverage c
JOIN extractions e ON e.extraction_id = c.extraction_id
WHERE c.files_matched_specific = 0
ORDER BY e.input_name, c.app_id;

-- cross-extraction rollup: which apps are consistently missed
CREATE VIEW v_apps_not_parsed_rollup AS
SELECT app_id,
       COUNT(*)                          AS extractions_present,
       SUM(files_matched_specific = 0)   AS extractions_unparsed,
       SUM(files_on_disk)                AS total_files_on_disk
FROM v_app_coverage
GROUP BY app_id
HAVING extractions_unparsed > 0
ORDER BY extractions_unparsed DESC, total_files_on_disk DESC;

-- iOS app containers whose owner could not be identified (no metadata plist,
-- not in applicationState.db) — likely uninstalled/orphaned apps; the bundle
-- id is unknown so they cannot appear in v_app_coverage.
CREATE VIEW v_unknown_containers AS
SELECT e.tool, e.input_name, f.extraction_id, f.container_uuid,
       f.location_type, COUNT(*) AS files_on_disk
FROM app_files f
JOIN extractions e ON e.extraction_id = f.extraction_id
WHERE f.app_id = '' AND f.container_uuid != ''
GROUP BY f.extraction_id, f.container_uuid, f.location_type;
"""


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def _load_manifest(output_dir: Path):
    """Map batch destination folder name -> manifest row (zip, sha256)."""
    manifest = output_dir / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        with open(manifest, encoding="utf-8") as f:
            data = json.load(f)
        return {row.get("output_folder", ""): row
                for row in data.get("extractions", [])}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_lava_json(report_dir: Path):
    for candidate in report_dir.glob("*.lava"):
        try:
            with open(candidate, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _extraction_props(con):
    """extractioninfo table -> {property: value}."""
    if not _table_exists(con, "extractioninfo"):
        return {}
    props = {}
    for prop, value in con.execute("SELECT property, value FROM extractioninfo"):
        props.setdefault(prop, value)
    return props


def _first(props, *keys):
    for key in keys:
        if props.get(key):
            return props[key]
    return ""


def _norm_path(path, input_path):
    """Normalize a recorded path so app_files and artifact_files line up.

    Directory-seeker source paths are absolute under the extraction root while
    the inventory strips that root; strip it here too.
    """
    path = str(path).replace("\\", "/")
    if input_path:
        root = str(input_path).replace("\\", "/").rstrip("/")
        if root and path.startswith(root):
            path = path[len(root):]
    return path.lstrip("/")


def _map_path_to_app(path, tool, uuid_map):
    """Best-effort app id for an extraction file path."""
    if tool == "ALEAPP":
        for pattern in _ANDROID_LOCATIONS:
            match = pattern.search(path)
            if match:
                return match.group(1)
        return ""
    match = _IOS_GUID_RE.search(path)
    if match:
        return uuid_map.get(match.group(1).upper(), "")
    match = _ITUNES_DOMAIN_RE.match(path)
    if match:
        return match.group(1)
    return ""


def _ingest_report(out_con, extraction_id, report_dir, dest_name, manifest_row, log):
    lava_db = report_dir / "_lava_artifacts.db"
    con = sqlite3.connect(f"file:{lava_db}?mode=ro", uri=True)
    try:
        props = _extraction_props(con)
        lava_json = _load_lava_json(report_dir)
        parser_info = lava_json.get("parser_info", {})

        tool = _first(props, "LEAPP Name") or parser_info.get("leapp_name", "")
        input_path = _first(props, "Input Path") or lava_json.get("param_input", "")
        has_inventory = int(_table_exists(con, "appfileinventory")
                            or _table_exists(con, "installedappinventory"))

        out_con.execute(
            "INSERT INTO extractions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (extraction_id, dest_name, report_dir.name, str(report_dir),
             manifest_row.get("zip", ""), manifest_row.get("sha256", ""),
             tool,
             _first(props, "LEAPP Version") or parser_info.get("leapp_version", ""),
             _first(props, "Extraction Type") or lava_json.get("param_type", ""),
             _first(props, "Input Name") or Path(str(input_path)).name,
             str(input_path),
             _first(props, "iOS Version", "Android Version"),
             _first(props, "Device Name"),
             _first(props, "Model", "Product Type"),
             _first(props, "Manufacturer"),
             _first(props, "Serial Number"),
             _first(props, "Time Zone"),
             _first(props, "Build ID", "ProductBuildVersion"),
             has_inventory))

        # installed_apps (iOS and Android tables have different shapes)
        uuid_map = {}
        if _table_exists(con, "installedappinventory"):
            cols = _columns(con, "installedappinventory")
            if "bundle_id" in cols:   # iLEAPP shape
                for bid, ctype, cpath, cuuid, src in con.execute(
                        "SELECT bundle_id, container_type, container_path,"
                        " container_uuid, data_source FROM installedappinventory"):
                    out_con.execute(
                        "INSERT INTO installed_apps VALUES (?,?,?,?,?,?,?,?,?)",
                        (extraction_id, bid, ctype, cpath, cuuid, None, None, "", src))
                    if cuuid and bid:
                        uuid_map[cuuid.upper()] = bid
            else:                     # ALEAPP shape
                for pkg, it, ut, installer, cpath, src in con.execute(
                        "SELECT package_name, install_time, update_time,"
                        " installer, code_path, data_source FROM installedappinventory"):
                    out_con.execute(
                        "INSERT INTO installed_apps VALUES (?,?,?,?,?,?,?,?,?)",
                        (extraction_id, pkg, "apk", cpath, "", it, ut, installer, src))

        # app_files
        if _table_exists(con, "appfileinventory"):
            cols = _columns(con, "appfileinventory")
            id_col = "bundle_id" if "bundle_id" in cols else "package_name"
            type_col = "container_type" if "container_type" in cols else "location_type"
            uuid_expr = "container_uuid" if "container_uuid" in cols else "''"
            for app_id, ltype, cuuid, fpath, fsize, mtime in con.execute(
                    f"SELECT {id_col}, {type_col}, {uuid_expr}, file_path,"
                    " file_size, modified_time FROM appfileinventory"):
                out_con.execute(
                    "INSERT INTO app_files VALUES (?,?,?,?,?,?,?)",
                    (extraction_id, app_id, ltype, cuuid,
                     _norm_path(fpath, input_path), fsize, mtime))
                if cuuid and app_id:
                    uuid_map.setdefault(cuuid.upper(), app_id)

        # framework registry: patterns and matched files (exclude the
        # inventory module itself, or every app would look "parsed")
        if _table_exists(con, "_artifact_search_patterns"):
            for pid, module, artifact, regex in con.execute(
                    "SELECT id, module_name, artifact_name, regex"
                    " FROM _artifact_search_patterns"):
                out_con.execute(
                    "INSERT INTO artifact_patterns VALUES (?,?,?,?,?)",
                    (extraction_id, pid, module, artifact, regex))
        if _table_exists(con, "_artifact_pattern_to_file"):
            seen = set()
            for fpath, module in con.execute("""
                    SELECT DISTINCT f.file_path, p.module_name
                    FROM _artifact_pattern_to_file link
                    JOIN _file_path_list f ON f.id = link.file_path_id
                    JOIN _artifact_search_patterns p
                         ON p.id = link.artifact_search_pattern_id
                    WHERE p.module_name != ?""", (INVENTORY_MODULE,)):
                norm = _norm_path(fpath, input_path)
                if (norm, module) in seen:
                    continue
                seen.add((norm, module))
                out_con.execute(
                    "INSERT INTO artifact_files VALUES (?,?,?,?)",
                    (extraction_id, norm,
                     _map_path_to_app(norm, tool, uuid_map), module))
    finally:
        con.close()
    return True


def aggregate(output_dir, log=print):
    """Merge every report under output_dir into batch_apps.sqlite.

    Full rebuild each time (idempotent). Returns the path of the coverage DB,
    or None when no reports were found.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    lava_dbs = sorted(p for p in output_dir.rglob("_lava_artifacts.db"))
    if not lava_dbs:
        log(f"coverage: no _lava_artifacts.db found under {output_dir}")
        return None

    db_path = output_dir / COVERAGE_DB_NAME
    if db_path.exists():
        db_path.unlink()
    out_con = sqlite3.connect(db_path)
    out_con.executescript(_SCHEMA)

    manifest = _load_manifest(output_dir)
    count = 0
    for extraction_id, lava_db in enumerate(lava_dbs, start=1):
        report_dir = lava_db.parent
        rel = report_dir.relative_to(output_dir)
        dest_name = rel.parts[0] if rel.parts else report_dir.name
        try:
            _ingest_report(out_con, extraction_id, report_dir, dest_name,
                           manifest.get(dest_name, {}), log)
            count += 1
        except sqlite3.Error as ex:
            log(f"coverage: skipped {report_dir} ({ex})")
    out_con.commit()

    apps = out_con.execute("SELECT COUNT(DISTINCT app_id) FROM v_app_coverage").fetchone()[0]
    unparsed = out_con.execute("SELECT COUNT(*) FROM v_apps_not_parsed").fetchone()[0]
    with_inventory = out_con.execute(
        "SELECT COUNT(*) FROM extractions WHERE has_inventory = 1").fetchone()[0]
    out_con.close()

    log(f"coverage: aggregated {count} extraction(s) "
        f"({with_inventory} with inventory data) into {db_path}")
    log(f"coverage: {apps} distinct app(s); {unparsed} app/extraction "
        "pair(s) with no artifact coverage (see v_apps_not_parsed / "
        "v_apps_not_parsed_rollup)")
    return db_path


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate LEAPP batch reports into batch_apps.sqlite "
                    "for app parsing-coverage analysis.")
    parser.add_argument("output_dir", type=Path,
                        help="Batch output directory containing the report folders")
    args = parser.parse_args()
    if not args.output_dir.is_dir():
        print(f"Error: not a directory: {args.output_dir}", file=sys.stderr)
        return 2
    return 0 if aggregate(args.output_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
