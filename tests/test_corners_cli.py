"""Unit tests for the ``pvt corners`` CLI (simkit.cli.corners)."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402


_EXAMPLE_FILE = _REPO_ROOT / "config" / "pvt_union_example.union.json"


def _run(*args: str) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def _min_doc(name: str) -> dict:
    return {
        "union_schema_version": 1,
        "name": name,
        "project": "my_ldo",
        "testbench_id": "MY_LIB/ldo_top_tb/schematic",
        "rows": [
            {
                "row_name": "TT",
                "vars": {"temperature": "55"},
                "models": [
                    {
                        "file": "rf018.scs",
                        "section": "tt",
                    }
                ],
            }
        ],
    }


class ExplodeCliTests(unittest.TestCase):

    def test_explode_default_seven_lines(self):
        rc, out, err = _run("corners", "explode", str(_EXAMPLE_FILE))
        self.assertEqual(rc, 0, f"err={err}")
        lines = [l for l in out.splitlines() if l.strip()]
        self.assertEqual(len(lines), 7)
        self.assertTrue(lines[0].startswith("TT "))
        # Row 1 is the TT row; rows 2-7 are TT_pvt_0..TT_pvt_5.
        self.assertIn("TT_pvt_0", lines[1])
        self.assertIn("TT_pvt_5", lines[6])

    def test_explode_json(self):
        rc, out, err = _run("corners", "explode", str(_EXAMPLE_FILE), "--json")
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 7)
        self.assertEqual(data[0]["sub_corner_name"], "TT")
        self.assertEqual(data[0]["models"][0]["section"], "tt")
        self.assertEqual(data[1]["sub_corner_name"], "TT_pvt_0")
        # Post-explode every model.section is a plain string.
        for sc in data:
            for m in sc["models"]:
                self.assertIsInstance(m["section"], str)

    def test_explode_bad_path_exits_2(self):
        rc, out, err = _run("corners", "explode", "/nope/missing.union.json")
        self.assertEqual(rc, 2)
        self.assertIn("pvt corners explode", err)


class ListCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_corners_list_"))
        (self.tmp / "db").mkdir()
        self.pvtproject = self.tmp / ".pvtproject"
        self.pvtproject.write_text(
            json.dumps({"project": "my_ldo", "dbRoot": "./db"}),
            encoding="utf-8",
        )
        self.unions_dir = self.tmp / "unions"
        self.unions_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_union(self, name: str) -> Path:
        path = self.unions_dir / f"{name}.union.json"
        path.write_text(json.dumps(_min_doc(name)), encoding="utf-8")
        return path

    def test_list_empty_dir_default(self):
        rc, out, err = _run("corners", "list", "--project", str(self.pvtproject))
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("(no unions found)", out)

    def test_list_empty_dir_json(self):
        rc, out, err = _run(
            "corners", "list", "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertEqual(json.loads(out), [])

    def test_list_one_union_default(self):
        self._write_union("alpha")
        rc, out, err = _run("corners", "list", "--project", str(self.pvtproject))
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("alpha", out)
        self.assertIn("OK", out)

    def test_list_one_union_json(self):
        self._write_union("alpha")
        rc, out, err = _run(
            "corners", "list", "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "alpha")
        self.assertEqual(data[0]["status"], "OK")
        self.assertEqual(data[0]["row_count"], 1)
        self.assertEqual(data[0]["sub_corner_count"], 1)

    def test_list_malformed_yields_status_error(self):
        bad = self.unions_dir / "broken.union.json"
        bad.write_text("{not json", encoding="utf-8")
        rc, out, err = _run(
            "corners", "list", "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "broken")
        self.assertNotEqual(data[0]["status"], "OK")

    def test_list_missing_project_exits_3(self):
        rc, out, err = _run("corners", "list", "--project", "/nope.pvtproject")
        self.assertEqual(rc, 3)


class DiffCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_corners_diff_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_union(self, doc: dict) -> Path:
        path = self.tmp / f"{doc['name']}.union.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_identical_exits_0(self):
        rc, out, err = _run(
            "corners", "diff", str(_EXAMPLE_FILE), str(_EXAMPLE_FILE),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("Rows identical: 2", out)

    def test_identical_json_exits_0(self):
        rc, out, err = _run(
            "corners", "diff", str(_EXAMPLE_FILE), str(_EXAMPLE_FILE), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(data["added"], [])
        self.assertEqual(data["removed"], [])
        self.assertEqual(data["changed"], [])
        self.assertEqual(data["identical_count"], 2)

    def test_changed_exits_1(self):
        a_doc = _min_doc("u_a")
        b_doc = _min_doc("u_b")
        b_doc["rows"][0]["vars"]["VDD"] = ["3", "2.8"]
        pa = self._write_union(a_doc)
        pb = self._write_union(b_doc)
        rc, out, err = _run("corners", "diff", str(pa), str(pb))
        self.assertEqual(rc, 1, f"err={err}")
        self.assertIn("vars.VDD", out)

    def test_changed_json(self):
        a_doc = _min_doc("u_a")
        b_doc = _min_doc("u_b")
        b_doc["rows"].append({
            "row_name": "FF",
            "vars": {"temperature": "85"},
        })
        pa = self._write_union(a_doc)
        pb = self._write_union(b_doc)
        rc, out, err = _run("corners", "diff", str(pa), str(pb), "--json")
        self.assertEqual(rc, 1, f"err={err}")
        data = json.loads(out)
        self.assertEqual(data["added"], ["FF"])
        self.assertEqual(data["removed"], [])
        self.assertEqual(data["identical_count"], 1)

    def test_bad_path_exits_2(self):
        rc, out, err = _run(
            "corners", "diff", "/nope/a.union.json", str(_EXAMPLE_FILE),
        )
        self.assertEqual(rc, 2)


# ----------------------------------------------------------------------------
# pull / push — exercised with simkit.skill_bridge patched out so tests don't
# need a live Virtuoso session. Live verification is a separate runtime probe.
# ----------------------------------------------------------------------------


from unittest.mock import patch  # noqa: E402

from simkit.skill_bridge import SkillBridgeError  # noqa: E402


class PullCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_pull_"))
        self.pvtproject = self.tmp / ".pvtproject"
        self.pvtproject.write_text(
            json.dumps({"project": "test_pull", "dbRoot": "./db", "schema_version": 1}),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pull_ok_prints_written_path(self):
        out_path = self.tmp / "x.union.json"
        with patch("simkit.skill_bridge.pvt_corners_pull",
                   return_value=str(out_path.resolve())) as pull:
            rc, out, err = _run(
                "corners", "pull", str(out_path),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn(f"pulled -> {out_path.resolve()}", out)
        pull.assert_called_once()
        kwargs = pull.call_args.kwargs
        self.assertEqual(kwargs["pvtproject_path"], self.pvtproject.resolve())
        self.assertIsNone(kwargs["session"])
        self.assertIsNone(kwargs["union_name"])

    def test_pull_rejects_bad_extension(self):
        rc, out, err = _run(
            "corners", "pull", "/tmp/whatever.json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("must end '.union.json'", err)

    def test_pull_missing_pvtproject_returns_3(self):
        rc, out, err = _run(
            "corners", "pull", "/tmp/x.union.json",
            "--project", "/no/such/.pvtproject",
        )
        self.assertEqual(rc, 3)
        self.assertIn("pvt corners pull", err)

    def test_pull_skillbridge_error_returns_4(self):
        out_path = self.tmp / "x.union.json"
        with patch(
            "simkit.skill_bridge.pvt_corners_pull",
            side_effect=SkillBridgeError("pvt_validation", "no setup db", None),
        ):
            rc, out, err = _run(
                "corners", "pull", str(out_path),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 4)
        self.assertIn("pvt_validation: no setup db", err)

    def test_pull_session_and_union_name_forwarded(self):
        out_path = self.tmp / "x.union.json"
        with patch(
            "simkit.skill_bridge.pvt_corners_pull", return_value=str(out_path)
        ) as pull:
            rc, out, err = _run(
                "corners", "pull", str(out_path),
                "--project", str(self.pvtproject),
                "--session", "fnxSession0",
                "--union-name", "my_union",
            )
        self.assertEqual(rc, 0, f"err={err}")
        kwargs = pull.call_args.kwargs
        self.assertEqual(kwargs["session"], "fnxSession0")
        self.assertEqual(kwargs["union_name"], "my_union")


class PushCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_push_"))
        self.pvtproject = self.tmp / ".pvtproject"
        self.pvtproject.write_text(
            json.dumps({"project": "test_push", "dbRoot": "./db", "schema_version": 1}),
            encoding="utf-8",
        )
        self.union_file = self.tmp / "u.union.json"
        self.union_file.write_text(json.dumps(_min_doc("u")), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_push_ok_prints_union_name(self):
        with patch(
            "simkit.skill_bridge.pvt_corners_push", return_value="u"
        ) as push:
            rc, out, err = _run(
                "corners", "push", str(self.union_file),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("pushed -> u", out)
        kwargs = push.call_args.kwargs
        self.assertFalse(kwargs["dry_run"])

    def test_push_missing_union_file_returns_2(self):
        rc, out, err = _run(
            "corners", "push", "/tmp/no_such.union.json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn(".union.json not found", err)

    def test_push_dry_run_passes_flag_and_marks_output(self):
        with patch(
            "simkit.skill_bridge.pvt_corners_push", return_value="u"
        ) as push:
            rc, out, err = _run(
                "corners", "push", str(self.union_file),
                "--project", str(self.pvtproject),
                "--dry-run",
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("pushed (dry-run) -> u", out)
        self.assertTrue(push.call_args.kwargs["dry_run"])

    def test_push_skillbridge_error_returns_4(self):
        with patch(
            "simkit.skill_bridge.pvt_corners_push",
            side_effect=SkillBridgeError("pvt_io", "axlPutCorner failed", None),
        ):
            rc, out, err = _run(
                "corners", "push", str(self.union_file),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 4)
        self.assertIn("pvt_io: axlPutCorner failed", err)

    def test_push_session_forwarded(self):
        with patch(
            "simkit.skill_bridge.pvt_corners_push", return_value="u"
        ) as push:
            _run(
                "corners", "push", str(self.union_file),
                "--project", str(self.pvtproject),
                "--session", "fnxSession0",
            )
        self.assertEqual(push.call_args.kwargs["session"], "fnxSession0")


# --- build CLI tests -----------------------------------------------------


class BuildCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_build_"))
        doc = {
            "union_schema_version": 1,
            "name": "u",
            "project": "p",
            "testbench_id": "L/MyCell/schematic",
            "rows": [
                {
                    "row_name": "TT",
                    "enabled": True,
                    "vars": {"temperature": "55"},
                    "models": [{
                        "file": "rf018.scs", "_file_abs": "/opt/pdk/rf018.scs",
                        "section": "tt",
                    }],
                },
            ],
        }
        self.union_file = self.tmp / "u.union.json"
        self.union_file.write_text(json.dumps(doc), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_default_out_is_sibling_csv(self):
        rc, out, err = _run("corners", "build", str(self.union_file))
        self.assertEqual(rc, 0, f"err={err}")
        expected_out = self.tmp / "u.csv"
        self.assertTrue(expected_out.is_file())
        text = expected_out.read_text(encoding="utf-8")
        self.assertIn("Corner,TT", text)
        self.assertIn("Modelfile::/opt/pdk/rf018.scs,t tt", text)
        self.assertIn(f"built -> {expected_out}", out)

    def test_build_explicit_out(self):
        out_path = self.tmp / "anywhere.csv"
        rc, out, err = _run(
            "corners", "build", str(self.union_file), "--out", str(out_path),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertTrue(out_path.is_file())

    def test_build_missing_input_returns_2(self):
        rc, out, err = _run("corners", "build", "/no/such.union.json")
        self.assertEqual(rc, 2)
        self.assertIn(".union.json not found", err)

    def test_build_warns_on_missing_file_abs(self):
        bad = self.tmp / "no_abs.union.json"
        bad.write_text(json.dumps({
            "union_schema_version": 1, "name": "no_abs", "project": "p",
            "testbench_id": "L/C/schematic",
            "rows": [{
                "row_name": "TT", "vars": {"temperature": "55"},
                "models": [{"file": "rf018.scs", "section": "tt"}],
            }],
        }), encoding="utf-8")
        rc, out, err = _run("corners", "build", str(bad))
        self.assertEqual(rc, 0)
        self.assertIn("missing_file_abs", err)

    def test_build_rejects_comma_in_value(self):
        bad = self.tmp / "comma.union.json"
        bad.write_text(json.dumps({
            "union_schema_version": 1, "name": "comma", "project": "p",
            "testbench_id": "L/C/schematic",
            "rows": [{
                "row_name": "TT", "vars": {"temperature": "0,1"},
                "models": [{
                    "file": "rf018.scs", "_file_abs": "/opt/pdk/rf018.scs",
                    "section": "tt",
                }],
            }],
        }), encoding="utf-8")
        rc, out, err = _run("corners", "build", str(bad))
        self.assertEqual(rc, 4)
        self.assertIn("','", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
