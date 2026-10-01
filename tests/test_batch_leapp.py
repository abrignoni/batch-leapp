"""Tests for what batch_leapp finds and how it starts a LEAPP tool.

Run from the repo root:  python -m unittest discover -s tests

Everything is built in a temporary folder. The "LEAPP tool" is a small script
written by the test, so no LEAPP checkout is needed and nothing outside the
temporary folder is read or written.
"""

import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import batch_leapp as core  # noqa: E402  pylint: disable=wrong-import-position


def touch(path: Path, data: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_zip(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("private/var/a.txt", "hi")
    return path


def make_tar(path: Path, mode: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    src = path.parent / "_src.txt"
    src.write_text("hi")
    with tarfile.open(path, mode) as t:
        t.add(src, arcname="private/var/a.txt")
    src.unlink()
    return path


def found(root: Path, **kw):
    """{relative path: -t value} for what find_archives returns."""
    return {p.relative_to(root).as_posix(): kind
            for p, kind, _ in core.find_archives(root, **kw)}


class Discovery(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_archives_and_their_types(self):
        make_zip(self.root / "a.zip")
        make_tar(self.root / "b.tar", "w")
        make_tar(self.root / "c.tar.gz", "w:gz")
        make_tar(self.root / "d.tgz", "w:gz")
        make_tar(self.root / "e.tar.xz", "w:xz")
        self.assertEqual(found(self.root), {
            "a.zip": "zip", "b.tar": "tar", "c.tar.gz": "gz", "d.tgz": "gz",
            "e.tar.xz": "tar"})
        self.assertTrue(core.is_valid_archive(self.root / "e.tar.xz", "tar"))

    def test_disk_images_are_raw_whatever_the_case_of_the_extension(self):
        for name in ("one.E01", "two.dd", "three.dmg", "four.Ex01", "five.aff4",
                     "six.vhdx", "seven.qcow2", "eight.L01", "nine.ad1"):
            touch(self.root / name)
        self.assertEqual(set(found(self.root).values()), {"raw"})
        self.assertEqual(len(found(self.root)), 9)

    def test_a_segmented_set_is_listed_once(self):
        for name in ("disk.E01", "disk.E02", "disk.E03",
                     "split.001", "split.002", "split.003",
                     "logical.L01", "logical.L02", "ftk.ad1", "ftk.ad2"):
            touch(self.root / name)
        self.assertEqual(sorted(found(self.root)),
                         ["disk.E01", "ftk.ad1", "logical.L01", "split.001"])

    def test_a_split_archive_is_not_a_disk_image(self):
        touch(self.root / "backup.zip.001")
        touch(self.root / "backup.7z.001")
        touch(self.root / "image.001")
        self.assertEqual(found(self.root), {"image.001": "raw"})

    def test_an_afm_stands_for_its_numbered_data_files(self):
        for name in ("case.afm", "case.000", "case.001", "other.001"):
            touch(self.root / name)
        self.assertEqual(sorted(found(self.root)), ["case.afm", "other.001"])

    def test_an_afd_folder_is_listed_once(self):
        for name in ("file_000.aff", "file_001.aff", "file_002.aff"):
            touch(self.root / "case.afd" / name)
        touch(self.root / "single.aff")
        self.assertEqual(sorted(found(self.root)),
                         ["case.afd/file_000.aff", "single.aff"])

    def test_a_split_vmdk_is_listed_by_its_descriptor(self):
        for name in ("vm.vmdk", "vm-s001.vmdk", "vm-s002.vmdk", "vm-flat.vmdk",
                     "orphan-s001.vmdk"):
            touch(self.root / name)
        # an extent with no descriptor beside it is still offered to the tool
        self.assertEqual(sorted(found(self.root)), ["orphan-s001.vmdk", "vm.vmdk"])

    def test_img_and_bin_only_when_raw_is_asked_for(self):
        touch(self.root / "userdata.img")
        touch(self.root / "firmware.bin")
        make_zip(self.root / "a.zip")
        self.assertEqual(found(self.root), {"a.zip": "zip"})
        self.assertEqual(found(self.root, kinds={"raw"}, generic_images=True),
                         {"userdata.img": "raw", "firmware.bin": "raw"})

    def test_an_empty_image_is_invalid(self):
        self.assertFalse(core.is_valid_archive(touch(self.root / "e.E01", b""), "raw"))
        self.assertTrue(core.is_valid_archive(touch(self.root / "f.E01"), "raw"))

    def test_leapp_output_folders_are_not_searched(self):
        make_zip(self.root / "real.zip")
        # named the way the tools name them, now and before
        make_zip(self.root / "iLEAPP_Output_2026-10-01_Thursday_151636" / "data" / "x.zip")
        make_zip(self.root / "ALEAPP_Reports_2025-01-01_Monday_000000" / "data" / "x.zip")
        # given a custom name: known by the files a run writes at its top
        custom = self.root / "MyReports"
        touch(custom / "_lava_artifacts.db")
        make_zip(custom / "data" / "x.zip")
        make_zip(custom / "top.zip")
        self.assertEqual(found(self.root), {"real.zip": "zip"})

    def test_appledouble_and_lone_gz_are_skipped(self):
        touch(self.root / "._disk.E01")
        touch(self.root / "log.gz", b"\x1f\x8bnot a tar")
        self.assertEqual(found(self.root), {})


class ToolNames(unittest.TestCase):
    def test_every_tool_has_a_name_and_a_colour(self):
        for stem, name in (("ileapp", "iLEAPP"), ("aleapp", "ALEAPP"),
                           ("rleapp", "RLEAPP"), ("vleapp", "VLEAPP"),
                           ("dleapp", "DLEAPP")):
            self.assertEqual(core.tool_name_from(Path(f"/x/{stem}.py")), name)
            self.assertEqual(core.tool_name_from(Path(f"/Applications/{name}.app")), name)
            self.assertIn(name, core.TOOL_ACCENT)

    def test_a_release_app_is_accepted_and_the_old_gui_build_refused(self):
        self.assertFalse(core.is_gui_build(Path("/Applications/iLEAPP.app")))
        self.assertTrue(core.is_gui_build(Path("/Applications/ileappGUI.app")))


# The stand-in LEAPP tool: writes what it was given, and what it could see of
# the examiner's environment, into its output folder.
FAKE_TOOL = r'''
import json, os, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
with open(os.path.join(out, "seen.json"), "w") as f:
    json.dump({"argv": args,
               "home": os.environ.get("HOME"),
               "appdata": os.environ.get("APPDATA"),
               "xdg": os.environ.get("XDG_CONFIG_HOME"),
               "userbase": os.environ.get("PYTHONUSERBASE"),
               "stdin_tty": sys.stdin is not None and sys.stdin.isatty()}, f)
'''


class Running(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.inp, self.out = base / "in", base / "out"
        self.tool = base / "tool" / "ileapp.py"
        self.tool.parent.mkdir()
        self.tool.write_text(FAKE_TOOL)
        make_zip(self.inp / "phone.zip")
        make_tar(self.inp / "phone2.tar.xz", "w:xz")
        touch(self.inp / "disk.E01")
        touch(self.inp / "disk.E02")
        touch(self.inp / "userdata.img")

    def run_batch(self, **kw):
        lines = []
        result = core.run_batch(self.inp, self.out, self.tool, hashes=False,
                                log=lines.append, **kw)
        seen = {p.parent.name: json.loads(p.read_text())
                for p in self.out.glob("*/seen.json")}
        return result, seen

    def check_isolated(self, seen):
        for folder, info in seen.items():
            private = str(self.out / folder / ".leapp_home")
            self.assertEqual(info["home"], private)
            self.assertEqual(info["appdata"], private)
            self.assertEqual(info["xdg"], private)
            self.assertTrue(info["userbase"])
            self.assertFalse(info["userbase"].startswith(private))
            self.assertFalse(info["stdin_tty"])

    def test_each_input_gets_its_type_and_a_private_home_when_sequential(self):
        result, seen = self.run_batch(jobs=1)
        self.assertEqual(len(result["ok"]), 3)
        types = {folder: info["argv"][info["argv"].index("-t") + 1]
                 for folder, info in seen.items()}
        self.assertEqual(types, {"phone": "zip", "phone2": "tar", "disk": "raw"})
        self.check_isolated(seen)

    def test_parallel_runs_are_isolated_too(self):
        result, seen = self.run_batch(jobs=2)
        self.assertEqual(len(result["ok"]), 3)
        self.check_isolated(seen)

    def test_forcing_raw_runs_only_the_images(self):
        result, seen = self.run_batch(type="raw")
        self.assertEqual(sorted(seen), ["disk", "userdata"])
        self.assertEqual(result["total"], 2)

    def test_a_set_pythonuserbase_is_left_as_given(self):
        with mock.patch.dict(os.environ, {"PYTHONUSERBASE": "/given/base"}):
            _, seen = self.run_batch(jobs=1)
        self.assertEqual({info["userbase"] for info in seen.values()}, {"/given/base"})


class GuiDetection(unittest.TestCase):
    def setUp(self):
        try:
            import batch_leapp_gui  # pylint: disable=import-outside-toplevel
        except ImportError as exc:                       # no tkinter here
            self.skipTest(f"GUI module not importable: {exc}")
        self.gui = batch_leapp_gui

    def detect(self, apps):
        with mock.patch("shutil.which", return_value=None), \
             mock.patch.object(self.gui.sys, "platform", "darwin"), \
             mock.patch.object(self.gui.Path, "glob",
                               return_value=[Path("/Applications") / a for a in apps]):
            return self.gui.detect_leapp()

    def test_it_never_offers_itself_or_a_tool_it_cannot_drive(self):
        self.assertEqual(self.detect(["Batch LEAPP.app", "GLEAPP Map Downloader.app",
                                      "GLEAPP.app", "ileappGUI.app"]), "")

    def test_it_offers_a_release_app(self):
        apps = Path("/Applications")
        self.assertEqual(self.detect(["Batch LEAPP.app", "GLEAPP.app", "iLEAPP.app"]),
                         str(apps / "iLEAPP.app"))
        self.assertEqual(self.detect(["DLEAPP.app"]), str(apps / "DLEAPP.app"))


if __name__ == "__main__":
    unittest.main()
