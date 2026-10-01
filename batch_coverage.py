#!/usr/bin/env python3
"""
batch_coverage.py — app parsing-coverage support for batch_leapp.

Two responsibilities:

1. enable_coverage(): make a LEAPP run include the developer-only
   "App Inventory" artifacts (scripts/alternate_artifacts/appInventory.py in
   the iLEAPP/ALEAPP repos), which write extractioninfo /
   installedappinventory / appfileinventory tables into each run's
   _lava_artifacts.db.
   - When the tool supports --custom_artifacts_path (iLEAPP; ALEAPP since
     PR #939) the module is enabled with extra arguments.
   - Otherwise (older ALEAPP checkouts) the module is staged (copied) into
     scripts/artifacts/ for the duration of the batch and removed afterwards.

2. aggregate(): after a batch (or standalone on any batch output folder),
   walk every report's _lava_artifacts.db and merge the inventory tables,
   the framework's artifact-match registry tables and the manifest into a
   single batch_apps.sqlite with views that answer "which installed apps
   were NOT parsed by the tooling?".

   The aggregate also records per-artifact results (files matched + rows
   produced), installed-app versions and artifact run errors.

Standalone usage (re-aggregate an existing batch output directory):

    python3 batch_coverage.py /path/to/batch_output
"""

import argparse
import json
import platform
import re
import shutil
import sqlite3
import sys
import time
from collections import OrderedDict
from pathlib import Path

COVERAGE_DB_NAME = "batch_apps.sqlite"
LAVA_SIDECAR_NAME = "batch_apps.lava"
COVERAGE_VERSION = "1.0"
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

    if _supports_custom_artifacts_path(leapp):
        return ["--custom_artifacts_path", str(alternate.parent)], noop

    if tool == "ALEAPP":
        # Fallback for ALEAPP checkouts that predate --custom_artifacts_path
        # (abrignoni/ALEAPP#939): stage the module for the batch.
        log("coverage: this ALEAPP has no --custom_artifacts_path option, "
            "staging the module instead.")
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

    log(f"coverage: {tool} does not support --custom_artifacts_path — "
        "inventory artifacts disabled, aggregation will still run.")
    return [], noop


def _supports_custom_artifacts_path(leapp: Path) -> bool:
    """True when the LEAPP script accepts --custom_artifacts_path."""
    try:
        return "custom_artifacts_path" in leapp.read_text(encoding="utf-8",
                                                          errors="replace")
    except OSError:
        return False


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
    owner_app_id  TEXT,
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
    owner_app_id  TEXT,
    module_name   TEXT,   -- one row per (file, matching module, artifact)
    artifact_key  TEXT    -- __artifacts_v2__ function key
);
-- one row per (extraction, artifact function key): how many files its
-- search patterns matched and how many rows it produced. row_count is NULL
-- when the artifact has no LAVA table (0 rows, non-LAVA output_types, or a
-- run error — see artifact_errors).
CREATE TABLE artifact_results (
    extraction_id INTEGER,
    module_name   TEXT,   -- py file stem, e.g. 'tikTok'
    artifact_key  TEXT,   -- __artifacts_v2__ function key, e.g. 'get_tikTok'
    files_matched INTEGER,
    row_count     INTEGER
);
-- installed-app versions per extraction
CREATE TABLE app_versions (
    extraction_id INTEGER,
    app_id        TEXT,   -- bundle id / package name
    app_name      TEXT,   -- iOS iTunes item_name; '' when unknown
    version       TEXT,   -- iOS bundleShortVersionString / Android versionCode
    version_kind  TEXT,   -- ios_app_store | android_gass | android_packages_xml
    source        TEXT    -- LAVA table the fact came from
);
-- artifacts whose read crashed during the run ("Reading X artifact had
-- errors!" in the screen-output log); they leave no LAVA table, so without
-- this record a crash is indistinguishable from 0 rows.
CREATE TABLE artifact_errors (
    extraction_id INTEGER,
    artifact_key  TEXT
);
-- group containers (group.com.x) and app extensions (com.x.SomeExtension)
-- folded into the app that owns them; coverage is computed on owner_app_id
CREATE TABLE app_aliases (
    extraction_id INTEGER,
    app_id        TEXT,
    owner_app_id  TEXT
);
CREATE INDEX idx_app_files ON app_files(extraction_id, app_id);
CREATE INDEX idx_app_files_owner ON app_files(extraction_id, owner_app_id);
CREATE INDEX idx_artifact_files ON artifact_files(extraction_id, owner_app_id);
CREATE INDEX idx_artifact_files_key ON artifact_files(extraction_id, artifact_key);
CREATE INDEX idx_artifact_results ON artifact_results(extraction_id, artifact_key);
CREATE INDEX idx_app_versions ON app_versions(extraction_id, app_id);
CREATE INDEX idx_installed ON installed_apps(extraction_id, app_id);
CREATE INDEX idx_aliases ON app_aliases(extraction_id, app_id);

-- How many different apps each module matched files for, per extraction.
-- Modules that touch many apps (userDefaults, appGrouplisting, packageInfo,
-- the framework's generic plist/db sweeps, ...) are 'generic': they match
-- every app's housekeeping files without decoding app content, so they must
-- not make an app count as parsed.
CREATE VIEW v_module_app_spread AS
SELECT extraction_id, module_name,
       COUNT(DISTINCT owner_app_id) AS apps_matched,
       COUNT(DISTINCT owner_app_id) >= 10 AS is_generic
FROM artifact_files
WHERE owner_app_id != ''
GROUP BY extraction_id, module_name;

-- one row per (extraction, app) with disk/matched file counts.
-- files_matched counts any artifact match; files_matched_specific counts
-- only matches by app-specific (non-generic) modules.
CREATE VIEW v_app_coverage AS
SELECT u.extraction_id, u.app_id,
       (SELECT COUNT(*) FROM app_files f
         WHERE f.extraction_id = u.extraction_id AND f.owner_app_id = u.app_id) AS files_on_disk,
       (SELECT COUNT(DISTINCT m.file_path) FROM artifact_files m
         WHERE m.extraction_id = u.extraction_id AND m.owner_app_id = u.app_id) AS files_matched,
       (SELECT COUNT(DISTINCT m.file_path) FROM artifact_files m
         JOIN v_module_app_spread s
              ON s.extraction_id = m.extraction_id
             AND s.module_name = m.module_name
         WHERE m.extraction_id = u.extraction_id AND m.owner_app_id = u.app_id
           AND s.is_generic = 0) AS files_matched_specific,
       EXISTS (SELECT 1 FROM installed_apps i
                LEFT JOIN app_aliases al ON al.extraction_id = i.extraction_id
                                        AND al.app_id = i.app_id
         WHERE i.extraction_id = u.extraction_id
           AND COALESCE(al.owner_app_id, i.app_id) = u.app_id) AS in_inventory
FROM (SELECT DISTINCT extraction_id, owner_app_id AS app_id
        FROM app_files WHERE owner_app_id != ''
      UNION
      SELECT DISTINCT i.extraction_id, COALESCE(al.owner_app_id, i.app_id)
        FROM installed_apps i
        LEFT JOIN app_aliases al ON al.extraction_id = i.extraction_id
                                AND al.app_id = i.app_id
        WHERE i.app_id != '') u;

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


def _sanitize_sql_name(name):
    """Mirror of the LEAPPs' lavafuncs.sanitize_sql_name — MUST stay in sync.

    It derives an artifact's LAVA table name from its __artifacts_v2__
    function key: strip non-word/non-space chars, whitespace -> '_',
    prepend '_' unless the result starts with a letter/underscore, lowercase.
    """
    sanitized = re.sub(r'[^\w\s]', '', str(name))
    sanitized = re.sub(r'\s+', '_', sanitized)
    if sanitized and not sanitized[0].isalpha() and sanitized[0] != '_':
        sanitized = '_' + sanitized
    return sanitized.lower()


def _os_version_fallback(con):
    """OS version from LEAPP artifact tables when extractioninfo lacks it
    (e.g. the inventory module did not run)."""
    queries = (
        # iLEAPP
        ("last_build",
         "SELECT property_value FROM last_build WHERE property = 'ProductVersion'"),
        ("system_version_plist",
         "SELECT property_value FROM system_version_plist WHERE property = 'ProductVersion'"),
        # ALEAPP (build.py labels the ro.*.build.version.release row 'Android Version')
        ("get_build",
         "SELECT value FROM get_build WHERE key = 'Android Version'"),
        ("usagestatsversion",
         "SELECT property_value FROM usagestatsversion WHERE property = 'Android Version'"),
    )
    for table, sql in queries:
        if not _table_exists(con, table):
            continue
        try:
            row = con.execute(sql).fetchone()
        except sqlite3.Error:
            continue
        if row and row[0]:
            return str(row[0])
    return ""


def _ingest_artifact_results(out_con, con, extraction_id):
    """Per (module, artifact function key): files matched by its search
    patterns and, when a LAVA table exists, the rows it produced."""
    if not _table_exists(con, "_artifact_search_patterns"):
        return
    rows = con.execute("""
        SELECT p.module_name, p.artifact_name,
               COUNT(DISTINCT f.file_path) AS files_matched
        FROM _artifact_search_patterns p
        LEFT JOIN _artifact_pattern_to_file link
               ON link.artifact_search_pattern_id = p.id
        LEFT JOIN _file_path_list f ON f.id = link.file_path_id
        WHERE p.module_name != ?
        GROUP BY p.module_name, p.artifact_name""", (INVENTORY_MODULE,))
    for module, artifact, files_matched in rows:
        row_count = None
        if files_matched:
            table = _sanitize_sql_name(artifact)
            if table and _table_exists(con, table):
                row_count = con.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        out_con.execute("INSERT INTO artifact_results VALUES (?,?,?,?,?)",
                        (extraction_id, module, artifact, files_matched,
                         row_count))


def _ingest_app_versions(out_con, con, extraction_id):
    """Installed-app versions from the run's LAVA artifact tables."""
    # iOS App Store metadata (appItunesmeta artifact); one row per app
    # container, so keep the first non-empty version per bundle id.
    if _table_exists(con, "get_appitunesmeta"):
        cols = set(_columns(con, "get_appitunesmeta"))
        if {"bundle_id", "version_number"} <= cols:
            name_expr = "item_name" if "item_name" in cols else "''"
            best = OrderedDict()
            for bid, name, ver in con.execute(
                    f"SELECT bundle_id, {name_expr}, version_number"
                    " FROM get_appitunesmeta"):
                if bid and ver and bid not in best:
                    best[bid] = (name or "", str(ver))
            for bid, (name, ver) in best.items():
                out_con.execute(
                    "INSERT INTO app_versions VALUES (?,?,?,?,?,?)",
                    (extraction_id, bid, name, ver,
                     "ios_app_store", "get_appitunesmeta"))
    # Android versionCode from Play services gass.db (installedappsGass
    # artifact): prefer the user-0 row, then the highest versionCode.
    if _table_exists(con, "get_installedappsgass"):
        cols = set(_columns(con, "get_installedappsgass"))
        if {"bundle_id", "version_code"} <= cols:
            user_expr = '"user"' if "user" in cols else "''"
            best = {}
            for user, bid, vcode in con.execute(
                    f"SELECT {user_expr}, bundle_id, version_code"
                    " FROM get_installedappsgass"):
                if not bid:
                    continue
                try:
                    numeric = int(vcode)
                except (TypeError, ValueError):
                    continue
                rank = (1 if str(user) == "0" else 0, numeric)
                if bid not in best or rank > best[bid]:
                    best[bid] = rank
            for bid, (_pref, numeric) in best.items():
                out_con.execute(
                    "INSERT INTO app_versions VALUES (?,?,?,?,?,?)",
                    (extraction_id, bid, "", str(numeric),
                     "android_gass", "get_installedappsgass"))
    # Phase-2 hook: an ALEAPP appInventory that emits packages.xml's @version
    # adds a version_code column to installedappinventory.
    if _table_exists(con, "installedappinventory"):
        cols = set(_columns(con, "installedappinventory"))
        if {"package_name", "version_code"} <= cols:
            for pkg, vcode in con.execute(
                    "SELECT DISTINCT package_name, version_code"
                    " FROM installedappinventory"):
                if pkg and vcode not in (None, ""):
                    out_con.execute(
                        "INSERT INTO app_versions VALUES (?,?,?,?,?,?)",
                        (extraction_id, pkg, "", str(vcode),
                         "android_packages_xml", "installedappinventory"))


_ERROR_LINE_RE = re.compile(r"Reading (\S+) artifact had errors!")


def _scan_run_errors(out_con, extraction_id, report_dir):
    """Record artifacts whose read crashed. The framework swallows the
    exception and logs 'Reading <func_key> artifact had errors!' to the
    screen-output log; no trace lands in the LAVA db."""
    log_file = report_dir / "_HTML" / "_Script_Logs" / "Screen_Output.html"
    if not log_file.is_file():
        return
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for key in sorted(set(_ERROR_LINE_RE.findall(text))):
        out_con.execute("INSERT INTO artifact_errors VALUES (?,?)",
                        (extraction_id, key))


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


def _ingest_report(out_con, extraction_id, report_dir, dest_name, manifest_row):
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
             _first(props, "iOS Version", "Android Version")
             or _os_version_fallback(con),
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
                    "INSERT INTO app_files VALUES (?,?,?,?,?,?,?,?)",
                    (extraction_id, app_id, app_id, ltype, cuuid,
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
            for fpath, module, artifact in con.execute("""
                    SELECT DISTINCT f.file_path, p.module_name, p.artifact_name
                    FROM _artifact_pattern_to_file link
                    JOIN _file_path_list f ON f.id = link.file_path_id
                    JOIN _artifact_search_patterns p
                         ON p.id = link.artifact_search_pattern_id
                    WHERE p.module_name != ?""", (INVENTORY_MODULE,)):
                norm = _norm_path(fpath, input_path)
                if (norm, module, artifact) in seen:
                    continue
                seen.add((norm, module, artifact))
                app_id = _map_path_to_app(norm, tool, uuid_map)
                out_con.execute(
                    "INSERT INTO artifact_files VALUES (?,?,?,?,?,?)",
                    (extraction_id, norm, app_id, app_id, module, artifact))

        _ingest_artifact_results(out_con, con, extraction_id)
        _ingest_app_versions(out_con, con, extraction_id)
        _scan_run_errors(out_con, extraction_id, report_dir)
    finally:
        con.close()
    return True


def _resolve_owner_apps(out_con, log=print):
    """Fold shared group containers and app extensions into their owning app.

    On iOS the forensically interesting data of many apps lives in shared
    group containers (group.com.kik.chat) or extension containers
    (com.x.Messenger.NotificationServiceExtension), which carry their own
    identifier. Without folding, an app looks unparsed even though its group
    container was parsed. Owner = the installed app (bundle/data/apk
    container) whose bundle id is the longest dot-boundary prefix of the
    identifier (after stripping a leading 'group.').
    """
    aliases = 0
    for (extraction_id,) in out_con.execute(
            "SELECT extraction_id FROM extractions").fetchall():
        owners = [r[0] for r in out_con.execute(
            "SELECT DISTINCT app_id FROM installed_apps WHERE extraction_id = ?"
            " AND location_type IN ('bundle', 'data', 'apk') AND app_id != ''",
            (extraction_id,))]
        owners.sort(key=len, reverse=True)   # longest prefix wins
        owner_set = set(owners)
        candidates = {r[0] for r in out_con.execute(
            "SELECT DISTINCT app_id FROM app_files WHERE extraction_id = ?"
            " AND app_id != '' UNION SELECT DISTINCT app_id FROM installed_apps"
            " WHERE extraction_id = ? AND app_id != ''",
            (extraction_id, extraction_id))}
        for candidate in candidates:
            if candidate in owner_set:
                continue
            base = candidate[6:] if candidate.startswith("group.") else candidate
            for owner in owners:
                if base == owner or base.startswith(owner + "."):
                    out_con.execute(
                        "INSERT INTO app_aliases VALUES (?,?,?)",
                        (extraction_id, candidate, owner))
                    aliases += 1
                    break
    for table in ("app_files", "artifact_files"):
        out_con.execute(f"""
            UPDATE {table} SET owner_app_id = (
                SELECT al.owner_app_id FROM app_aliases al
                WHERE al.extraction_id = {table}.extraction_id
                  AND al.app_id = {table}.app_id)
            WHERE EXISTS (
                SELECT 1 FROM app_aliases al
                WHERE al.extraction_id = {table}.extraction_id
                  AND al.app_id = {table}.app_id)""")
    out_con.commit()
    log(f"coverage: folded {aliases} group/extension identifier(s) into "
        "their owning apps")


# --------------------------------------------------------------------------
# LAVA project sidecar (makes batch_apps.sqlite openable in LAVA)
# --------------------------------------------------------------------------

# Materialized copies of the views, enriched with the extraction name so the
# LAVA grid is useful standalone. LAVA reads concrete tables, not views.
_LAVA_MATERIALIZE = (
    ("coverage_summary", """
     SELECT e.input_name, e.tool, e.tool_version, e.os_version, e.device_name,
            (SELECT COUNT(*) FROM v_app_coverage c
              WHERE c.extraction_id = e.extraction_id) AS apps_present,
            (SELECT COUNT(*) FROM v_app_coverage c
              WHERE c.extraction_id = e.extraction_id
                AND c.files_matched_specific > 0) AS apps_parsed,
            (SELECT COUNT(*) FROM v_app_coverage c
              WHERE c.extraction_id = e.extraction_id
                AND c.files_matched_specific = 0) AS apps_not_parsed,
            ROUND(100.0 * (SELECT COUNT(*) FROM v_app_coverage c
              WHERE c.extraction_id = e.extraction_id
                AND c.files_matched_specific > 0)
              / MAX(1, (SELECT COUNT(*) FROM v_app_coverage c
              WHERE c.extraction_id = e.extraction_id)), 1) AS percent_parsed,
            (SELECT COUNT(*) FROM installed_apps i
              WHERE i.extraction_id = e.extraction_id) AS app_containers,
            (SELECT COUNT(*) FROM app_files f
              WHERE f.extraction_id = e.extraction_id) AS files_inventoried,
            (SELECT COUNT(DISTINCT m.file_path) FROM artifact_files m
              WHERE m.extraction_id = e.extraction_id) AS files_matched
     FROM extractions e ORDER BY e.input_name"""),
    ("apps_not_parsed", "SELECT * FROM v_apps_not_parsed"),
    ("apps_not_parsed_rollup", "SELECT * FROM v_apps_not_parsed_rollup"),
    ("app_coverage",
     "SELECT e.tool, e.input_name, c.* FROM v_app_coverage c"
     " JOIN extractions e ON e.extraction_id = c.extraction_id"
     " ORDER BY e.input_name, c.app_id"),
    ("module_app_spread",
     "SELECT e.tool, e.input_name, s.* FROM v_module_app_spread s"
     " JOIN extractions e ON e.extraction_id = s.extraction_id"
     " ORDER BY s.apps_matched DESC"),
    ("unknown_containers", "SELECT * FROM v_unknown_containers"),
)

# Artifacts exposed to LAVA: (table, display name, description, tabler icon,
# {column: lava type}) — 'datetime' columns render as timestamps in LAVA.
_LAVA_ARTIFACTS = (
    ("coverage_summary", "Coverage Summary",
     "One row per extraction: apps present vs apps parsed by app-specific "
     "modules, percent covered, and inventory scale. The batch scoreboard.",
     "report-analytics", {}),
    ("apps_not_parsed_rollup", "Apps Not Parsed - Rollup",
     "Apps with data on disk that no app-specific module touched, ranked "
     "across all extractions. The headline coverage view.", "flag", {}),
    ("apps_not_parsed", "Apps Not Parsed - Per Extraction",
     "Apps with data on disk that no app-specific module touched, per "
     "extraction.", "flag-off", {}),
    ("app_coverage", "App Coverage",
     "Every app per extraction with files on disk, files matched by any "
     "module, and files matched by app-specific modules only.", "chart-bar", {}),
    ("module_app_spread", "Module App Spread",
     "How many different apps each module matched files for. Modules at 10+ "
     "are classified generic and do not count as parsing an app.", "topology-star", {}),
    ("unknown_containers", "Unknown Containers",
     "App containers whose owner could not be identified (likely uninstalled "
     "apps); their bundle ID is unknown.", "zoom-question", {}),
    ("extractions", "Extractions",
     "One row per processed input: tool version, device identifiers and "
     "input archive.", "device-mobile", {}),
    ("installed_apps", "Installed Apps",
     "Installed application inventory across all extractions.", "package",
     {"install_time": "datetime", "update_time": "datetime"}),
    ("app_files", "App Files",
     "Every file in every extraction, mapped to its owning app when "
     "applicable.", "files", {}),
    ("artifact_files", "Artifact Matched Files",
     "Files matched by LEAPP module search patterns (inventory module "
     "excluded), resolved to their owning app.", "search", {}),
    ("artifact_results", "Artifact Results",
     "Per extraction and artifact: files matched by its search patterns and "
     "rows produced. An empty row count means the artifact wrote no LAVA "
     "table (0 rows, non-LAVA output types, or a run error).", "list-check", {}),
    ("app_versions", "App Versions",
     "Installed-app versions per extraction (iOS App Store metadata / "
     "Android Play-services versionCode).", "versions", {}),
    ("artifact_errors", "Artifact Run Errors",
     "Artifacts whose read crashed during the run; they leave no LAVA table, "
     "so without this a crash looks like 0 rows.", "alert-triangle", {}),
)

_COLUMN_LABEL_OVERRIDES = {
    "app_id": "App ID", "os_version": "OS Version", "sha256": "SHA-256",
    "container_uuid": "Container UUID", "input_name": "Input Name",
    "extraction_id": "Extraction ID", "in_inventory": "In Inventory",
    "is_generic": "Is Generic", "tool": "Tool",
}


def _column_label(column):
    return _COLUMN_LABEL_OVERRIDES.get(column, column.replace("_", " ").title())


def _write_lava_sidecar(out_con, output_dir, log=print):
    """Materialize the coverage views and write batch_apps.lava so the
    aggregate database opens as a project in LAVA."""
    for table, select_sql in _LAVA_MATERIALIZE:
        out_con.execute(f"DROP TABLE IF EXISTS {table}")
        out_con.execute(f"CREATE TABLE {table} AS {select_sql}")
    out_con.commit()

    artifacts = OrderedDict()
    module_meta = {"module_name": "batch_coverage",
                   "module_filename": "batch_coverage.py", "artifacts": []}
    category = "Batch Coverage"
    artifacts[category] = []
    for table, name, description, icon, special in _LAVA_ARTIFACTS:
        columns = _columns(out_con, table)
        record_count = out_con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        artifacts[category].append({
            "name": name,
            "tablename": table,
            "module": "batch_coverage",
            "column_map": {c: _column_label(c) for c in columns},
            "artifact_icon": icon,
            "record_count": record_count,
            "object_columns": [{"name": c, "type": t} for c, t in special.items()],
        })
        module_meta["artifacts"].append({
            "artifact_key": table, "tablename": table, "name": name,
            "description": description, "author": "batch-leapp",
            "created_date": "", "last_updated_date": "", "notes": "",
            "category": category,
        })

    lava_data = {
        "parser_info": {
            "leapp_name": "batch-leapp",
            "leapp_version": COVERAGE_VERSION,
            "leapp_mode": "CLI",
            "package": "Source code",
            "OS": platform.platform(),
            "start_timestamp": int(time.time()),
        },
        "param_input": str(output_dir),
        "param_output": str(output_dir),
        "param_type": "coverage-aggregate",
        "processing_status": "Complete",
        "lava_db_name": COVERAGE_DB_NAME,
        "modules": [{"module_name": "batch_coverage", "module_status": "completed"}],
        "artifacts": artifacts,
        "meta": {"modules": [module_meta]},
    }
    sidecar = output_dir / LAVA_SIDECAR_NAME
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(lava_data, f, indent=2)
    log(f"coverage: wrote LAVA project file {sidecar} (open it in LAVA)")
    return sidecar


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
                           manifest.get(dest_name, {}))
            count += 1
        except sqlite3.Error as ex:
            log(f"coverage: skipped {report_dir} ({ex})")
    out_con.commit()
    _resolve_owner_apps(out_con, log)

    apps = out_con.execute("SELECT COUNT(DISTINCT app_id) FROM v_app_coverage").fetchone()[0]
    unparsed = out_con.execute("SELECT COUNT(*) FROM v_apps_not_parsed").fetchone()[0]
    with_inventory = out_con.execute(
        "SELECT COUNT(*) FROM extractions WHERE has_inventory = 1").fetchone()[0]
    _write_lava_sidecar(out_con, output_dir, log)
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
