"""Unit tests for the ``pvt measure`` CLI (simkit.cli.measure).

Covers every subcommand happy path plus at least one error case per
command. Live verbs (apply / pull / restore) are exercised with the
skill_bridge wrappers mocked out so the suite stays offline.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from simkit.cli.__main__ import main as cli_main  # noqa: E402
from simkit.skill_bridge import (  # noqa: E402
    PvtMeasurePullReport,
    PvtMeasurePushReport,
    PvtMeasurePushRow,
    SkillBridgeError,
)


def _run(*args: str) -> tuple:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli_main(list(args))
        except SystemExit as exc:
            rc = exc.code if isinstance(exc.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


_RTIME_RAW = (
    'average(riseTime(vtime(\'tran "/Vout") 0 nil VAR("VDD") nil 10 90 t "time"))'
)


def _make_project(tmp: Path, project_name: str = "my_block") -> Path:
    """Create a minimal `.pvtproject` plus templates/signal_groups/measurements
    directories. Returns the .pvtproject path."""
    p = tmp / ".pvtproject"
    p.write_text(json.dumps({
        "project": project_name,
        "dbRoot": "./db",
        "schema_version": 1,
    }), encoding="utf-8")
    (tmp / "db").mkdir(exist_ok=True)
    (tmp / "templates").mkdir(exist_ok=True)
    (tmp / "signal_groups").mkdir(exist_ok=True)
    (tmp / "measurements").mkdir(exist_ok=True)
    return p


def _seed_rise_template(tmp: Path, name: str = "rise_time_threshold") -> Path:
    """Drop the worked-example rise_time template into <tmp>/templates/."""
    body = {
        "template_schema_version": 1,
        "name": name,
        "short_alias": "Rtime",
        "expression": (
            "average(riseTime(vtime('tran \"$SIG\") 0 nil VAR(\"VDD\") nil "
            "$V_LOW $V_HIGH t \"time\"))"
        ),
        "params": [
            {"key": "SIG", "kind": "signal"},
            {"key": "V_LOW", "kind": "number", "default": "10"},
            {"key": "V_HIGH", "kind": "number", "default": "90"},
        ],
        "eval_type": "point",
        "plot": True,
        "save": False,
    }
    p = tmp / "templates" / f"{name}.template.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _seed_signal_group(
    tmp: Path, name: str = "voltage_outs", signals=("/Vout",)
) -> Path:
    body = {
        "signal_group_schema_version": 1,
        "name": name,
        "signals": list(signals),
    }
    p = tmp / "signal_groups" / f"{name}.siggroup.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


def _seed_bundle(
    tmp: Path,
    name: str = "voltage_outs_rise",
    project: str = "my_block",
    apply_entries=None,
    test_name: str = "Test",
) -> Path:
    if apply_entries is None:
        apply_entries = [
            {"template": "rise_time_threshold",
             "signal_group": "voltage_outs"},
        ]
    body = {
        "measure_schema_version": 1,
        "name": name,
        "project": project,
        "testbench_id": "fnxLib/my_block_tb/schematic",
        "test_name": test_name,
        "apply": apply_entries,
    }
    p = tmp / "measurements" / f"{name}.measure.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# new-template
# --------------------------------------------------------------------------


class NewTemplateCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_nt_"))
        self.pvtproject = _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_template_default_dest(self):
        rc, out, err = _run(
            "measure", "new-template", "rise_time_threshold",
            "--from-expr", _RTIME_RAW,
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        path = self.tmp / "templates" / "rise_time_threshold.template.json"
        self.assertTrue(path.is_file())
        body = json.loads(path.read_text())
        self.assertEqual(body["name"], "rise_time_threshold")
        # Non-interactive default retains numerics.
        self.assertEqual(len(body["params"]), 1)
        self.assertEqual(body["params"][0]["key"], "SIG")
        self.assertIn("_pasted_from", body)

    def test_new_template_short_alias_override(self):
        rc, out, err = _run(
            "measure", "new-template", "r2",
            "--from-expr", _RTIME_RAW,
            "--short-alias", "Rtime",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        body = json.loads(
            (self.tmp / "templates" / "r2.template.json").read_text()
        )
        self.assertEqual(body["short_alias"], "Rtime")

    def test_new_template_refuses_overwrite(self):
        _seed_rise_template(self.tmp)
        rc, out, err = _run(
            "measure", "new-template", "rise_time_threshold",
            "--from-expr", _RTIME_RAW,
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("--force", err)

    def test_new_template_force_overwrites(self):
        _seed_rise_template(self.tmp)
        rc, out, err = _run(
            "measure", "new-template", "rise_time_threshold",
            "--from-expr", _RTIME_RAW,
            "--project", str(self.pvtproject),
            "--force",
        )
        self.assertEqual(rc, 0, f"err={err}")

    def test_new_template_invalid_name_returns_2(self):
        rc, out, err = _run(
            "measure", "new-template", "BadName",
            "--from-expr", _RTIME_RAW,
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)

    def test_new_template_no_signal_in_expr_returns_2(self):
        rc, out, err = _run(
            "measure", "new-template", "no_sig",
            "--from-expr", 'average(VAR("VDD"))',
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("signal-path literal", err)

    def test_new_template_explicit_out(self):
        custom = self.tmp / "elsewhere" / "rise_time_threshold.template.json"
        rc, out, err = _run(
            "measure", "new-template", "rise_time_threshold",
            "--from-expr", _RTIME_RAW,
            "--out", str(custom),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertTrue(custom.is_file())

    def test_new_template_bad_out_extension(self):
        rc, out, err = _run(
            "measure", "new-template", "rise_time_threshold",
            "--from-expr", _RTIME_RAW,
            "--out", str(self.tmp / "nope.json"),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn(".template.json", err)


# --------------------------------------------------------------------------
# list-templates / show-template
# --------------------------------------------------------------------------


class ListShowTemplateCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_lt_"))
        self.pvtproject = _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_list_templates_empty(self):
        rc, out, err = _run(
            "measure", "list-templates",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("no .template.json files found", out)

    def test_list_templates_one_default(self):
        _seed_rise_template(self.tmp)
        rc, out, err = _run(
            "measure", "list-templates",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("rise_time_threshold", out)
        self.assertIn("Rtime", out)

    def test_list_templates_json(self):
        _seed_rise_template(self.tmp)
        rc, out, err = _run(
            "measure", "list-templates",
            "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "rise_time_threshold")
        self.assertEqual(data[0]["short_alias"], "Rtime")
        self.assertEqual(data[0]["status"], "OK")

    def test_list_templates_missing_project(self):
        rc, out, err = _run(
            "measure", "list-templates",
            "--project", "/no/such/.pvtproject",
        )
        self.assertEqual(rc, 3)

    def test_show_template_ok(self):
        _seed_rise_template(self.tmp)
        rc, out, err = _run(
            "measure", "show-template", "rise_time_threshold",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(data["name"], "rise_time_threshold")
        self.assertEqual(data["short_alias"], "Rtime")

    def test_show_template_missing_returns_2(self):
        rc, out, err = _run(
            "measure", "show-template", "no_such",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)


# --------------------------------------------------------------------------
# install-builtins
# --------------------------------------------------------------------------


class InstallBuiltinsCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_ib_"))
        self.pvtproject = _make_project(self.tmp)
        self.templates_dir = self.tmp / "templates"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _installed(self) -> set[str]:
        return {
            p.name[: -len(".template.json")]
            for p in self.templates_dir.glob("*.template.json")
        }

    def test_full_install_empty_target(self):
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        names = self._installed()
        self.assertIn("i_avg_window", names)
        self.assertIn("edge_delay_avg", names)
        self.assertIn("value_at", names)
        # All 21 builtins should land (17 from v1.1 + 4 v1.2 _full variants)
        self.assertEqual(len(names), 21, f"got: {sorted(names)}")
        self.assertIn("21 of 21 installed", out)

    def test_dry_run_list_only(self):
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
            "--list",
        )
        self.assertEqual(rc, 0, f"err={err}")
        # Nothing was actually copied
        self.assertEqual(self._installed(), set())
        # But the plan was reported
        self.assertIn("install", out)
        self.assertIn("i_avg_window", out)

    def test_names_subset(self):
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
            "--names", "i_avg_window,freq_window,value_at",
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertEqual(
            self._installed(),
            {"i_avg_window", "freq_window", "value_at"},
        )
        self.assertIn("3 of 3 installed", out)

    def test_unknown_name_rejected(self):
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
            "--names", "i_avg_window,not_a_builtin",
        )
        self.assertEqual(rc, 2)
        self.assertIn("unknown builtin name(s)", err)
        self.assertIn("not_a_builtin", err)
        # Nothing should have been installed
        self.assertEqual(self._installed(), set())

    def test_collision_refused_without_force(self):
        # Seed one of the builtins
        rc, _, _ = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
            "--names", "i_avg_window",
        )
        self.assertEqual(rc, 0)
        # Now a full install should refuse
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("refusing to overwrite", err)
        self.assertIn("i_avg_window", err)
        # Only the pre-seeded one is still present
        self.assertEqual(self._installed(), {"i_avg_window"})

    def test_force_overwrites_partial(self):
        # Seed two; mutate one so we can verify --force replaces it
        rc, _, _ = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
            "--names", "i_avg_window,freq_window",
        )
        self.assertEqual(rc, 0)
        seeded = self.templates_dir / "i_avg_window.template.json"
        original_bytes = seeded.read_bytes()
        seeded.write_text("{}", encoding="utf-8")  # corrupt one on purpose
        self.assertNotEqual(seeded.read_bytes(), original_bytes)
        # Full install with --force should restore + add the rest
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
            "--force",
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertEqual(seeded.read_bytes(), original_bytes)
        self.assertEqual(len(self._installed()), 21)
        self.assertIn("overwrite", out)
        self.assertIn("21 of 21 installed", out)

    def test_missing_project_returns_3(self):
        rc, out, err = _run(
            "measure", "install-builtins",
            "--project", "/no/such/.pvtproject",
        )
        self.assertEqual(rc, 3)

    def test_installed_builtins_are_listable(self):
        # Closes the loop: after install-builtins, list-templates sees them.
        rc, _, _ = _run(
            "measure", "install-builtins",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0)
        rc, out, err = _run(
            "measure", "list-templates",
            "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        # Every entry parsed cleanly
        for row in data:
            self.assertEqual(row["status"], "OK", f"bad row: {row}")
        names = {row["name"] for row in data}
        self.assertIn("edge_delay_avg", names)
        self.assertEqual(len(names), 21)


# --------------------------------------------------------------------------
# new-signal-group / list-signal-groups
# --------------------------------------------------------------------------


class SignalGroupCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_sg_"))
        self.pvtproject = _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_signal_group_default(self):
        rc, out, err = _run(
            "measure", "new-signal-group", "voltage_outs",
            "--signals", "/Vout,/Vout2",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        p = self.tmp / "signal_groups" / "voltage_outs.siggroup.json"
        body = json.loads(p.read_text())
        self.assertEqual(body["signals"], ["/Vout", "/Vout2"])

    def test_new_signal_group_rejects_unprefixed_path(self):
        rc, out, err = _run(
            "measure", "new-signal-group", "bad",
            "--signals", "Vout",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("must start with '/'", err)

    def test_new_signal_group_rejects_duplicate(self):
        rc, out, err = _run(
            "measure", "new-signal-group", "dup",
            "--signals", "/Vout,/Vout",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("duplicate signal", err)

    def test_new_signal_group_refuses_overwrite(self):
        _seed_signal_group(self.tmp)
        rc, out, err = _run(
            "measure", "new-signal-group", "voltage_outs",
            "--signals", "/Vout2",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("--force", err)

    def test_list_signal_groups_empty(self):
        rc, out, err = _run(
            "measure", "list-signal-groups",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("no .siggroup.json files found", out)

    def test_list_signal_groups_json(self):
        _seed_signal_group(self.tmp)
        rc, out, err = _run(
            "measure", "list-signal-groups",
            "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "voltage_outs")
        self.assertEqual(data[0]["signals"], 1)


# --------------------------------------------------------------------------
# new-bundle / list-bundles
# --------------------------------------------------------------------------


class BundleCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_b_"))
        self.pvtproject = _make_project(self.tmp)
        _seed_rise_template(self.tmp)
        _seed_signal_group(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_bundle_with_explicit_testbench(self):
        rc, out, err = _run(
            "measure", "new-bundle", "voltage_outs_rise",
            "--templates", "rise_time_threshold",
            "--signal-group", "voltage_outs",
            "--test", "Test",
            "--testbench-id", "fnxLib/my_block_tb/schematic",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        p = self.tmp / "measurements" / "voltage_outs_rise.measure.json"
        body = json.loads(p.read_text())
        self.assertEqual(body["test_name"], "Test")
        self.assertEqual(len(body["apply"]), 1)

    def test_new_bundle_missing_template_returns_3(self):
        rc, out, err = _run(
            "measure", "new-bundle", "x",
            "--templates", "no_such_template",
            "--signal-group", "voltage_outs",
            "--test", "Test",
            "--testbench-id", "L/C/V",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 3)
        self.assertIn("not found", err)

    def test_new_bundle_missing_signal_group_when_required(self):
        rc, out, err = _run(
            "measure", "new-bundle", "x",
            "--templates", "rise_time_threshold",
            "--test", "Test",
            "--testbench-id", "L/C/V",
            "--project", str(self.pvtproject),
        )
        # Template needs signal_group; no --signal-group => exit 2.
        self.assertEqual(rc, 2)

    def test_new_bundle_resolves_testbench_via_skill_bridge(self):
        with patch(
            "simkit.skill_bridge.resolve_live_testbench_id",
            return_value="fnxLib/my_block_tb/schematic",
        ):
            rc, out, err = _run(
                "measure", "new-bundle", "via_live",
                "--templates", "rise_time_threshold",
                "--signal-group", "voltage_outs",
                "--test", "Test",
                "--session", "fnxSession0",
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        body = json.loads(
            (self.tmp / "measurements" / "via_live.measure.json").read_text()
        )
        self.assertEqual(body["testbench_id"], "fnxLib/my_block_tb/schematic")

    def test_new_bundle_live_resolve_fail_returns_3(self):
        with patch(
            "simkit.skill_bridge.resolve_live_testbench_id",
            side_effect=SkillBridgeError("pvt_validation", "no session", None),
        ):
            rc, out, err = _run(
                "measure", "new-bundle", "x",
                "--templates", "rise_time_threshold",
                "--signal-group", "voltage_outs",
                "--test", "Test",
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 3)

    def test_list_bundles_empty(self):
        rc, out, err = _run(
            "measure", "list-bundles",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("no .measure.json files found", out)

    def test_list_bundles_one_json(self):
        _seed_bundle(self.tmp)
        rc, out, err = _run(
            "measure", "list-bundles",
            "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["name"], "voltage_outs_rise")
        self.assertEqual(data[0]["test"], "Test")
        self.assertEqual(data[0]["apply"], 1)
        self.assertEqual(data[0]["status"], "OK")

    def test_list_bundles_missing_template_marks_error(self):
        _seed_bundle(
            self.tmp, apply_entries=[
                {"template": "no_such", "signal_group": "voltage_outs"},
            ],
        )
        rc, out, err = _run(
            "measure", "list-bundles",
            "--project", str(self.pvtproject), "--json",
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        # v1.2 (d): status starts with explicit ERR: marker rather than
        # a leading path that truncates the actually-useful message.
        self.assertTrue(
            data[0]["status"].startswith("ERR: "),
            f"expected 'ERR: ' prefix, got {data[0]['status']!r}",
        )
        # And the leading bundle-path prefix must be stripped — STATUS must
        # not start with the .measure.json file's own path.
        bundle_path = str(data[0]["path"])
        self.assertFalse(
            data[0]["status"].startswith(f"ERR: {bundle_path}"),
            f"status leaks bundle path: {data[0]['status']!r}",
        )


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------


class RenderCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_render_"))
        self.pvtproject = _make_project(self.tmp)
        _seed_rise_template(self.tmp)
        _seed_signal_group(self.tmp)
        self.bundle = _seed_bundle(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_render_default_emits_sibling_csv(self):
        rc, out, err = _run(
            "measure", "render", str(self.bundle),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        csv_path = self.tmp / "measurements" / "voltage_outs_rise.rendered.csv"
        self.assertTrue(csv_path.is_file())
        text = csv_path.read_text()
        self.assertIn("test,output_name,expression,eval_type,plot,save", text)
        self.assertIn("Rtime_Vout", text)

    def test_render_json(self):
        rc, out, err = _run(
            "measure", "render", str(self.bundle), "--json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["test"], "Test")
        self.assertEqual(data[0]["output_name"], "Rtime_Vout")
        self.assertIn("/Vout", data[0]["expression"])

    def test_render_explicit_out(self):
        custom = self.tmp / "render.csv"
        rc, out, err = _run(
            "measure", "render", str(self.bundle),
            "--out", str(custom),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertTrue(custom.is_file())

    def test_render_missing_bundle_returns_2(self):
        rc, out, err = _run(
            "measure", "render", "/no/such.measure.json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)


# --------------------------------------------------------------------------
# apply (mocked)
# --------------------------------------------------------------------------


class ApplyCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_apply_"))
        self.pvtproject = _make_project(self.tmp)
        _seed_rise_template(self.tmp)
        _seed_signal_group(self.tmp)
        self.bundle = _seed_bundle(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_ok(self):
        report = PvtMeasurePushReport(
            n_pushed=1,
            rows=(PvtMeasurePushRow(name="Rtime_Vout", status="added"),),
        )
        with patch(
            "simkit.skill_bridge.pvt_measure_push",
            return_value=report,
        ) as push:
            rc, out, err = _run(
                "measure", "apply", str(self.bundle),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("pushed 1 row", out)
        self.assertIn("Rtime_Vout", out)
        self.assertFalse(push.call_args.kwargs.get("dry_run"))
        self.assertFalse(push.call_args.kwargs.get("replace"))

    def test_apply_dry_run_passes_flag(self):
        report = PvtMeasurePushReport(
            n_pushed=1,
            rows=(PvtMeasurePushRow(name="Rtime_Vout", status="would_add"),),
        )
        with patch(
            "simkit.skill_bridge.pvt_measure_push", return_value=report,
        ) as push:
            rc, out, err = _run(
                "measure", "apply", str(self.bundle),
                "--dry-run",
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertTrue(push.call_args.kwargs.get("dry_run"))
        self.assertIn("(dry-run)", out)

    def test_apply_replace_passes_flag(self):
        report = PvtMeasurePushReport(n_pushed=0, rows=())
        with patch(
            "simkit.skill_bridge.pvt_measure_push", return_value=report,
        ) as push:
            _run(
                "measure", "apply", str(self.bundle),
                "--replace",
                "--project", str(self.pvtproject),
            )
        self.assertTrue(push.call_args.kwargs.get("replace"))

    def test_apply_skillbridge_error_returns_4(self):
        with patch(
            "simkit.skill_bridge.pvt_measure_push",
            side_effect=SkillBridgeError(
                "pvt_validation", "row failed: bad expr", None,
            ),
        ):
            rc, out, err = _run(
                "measure", "apply", str(self.bundle),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 4)
        self.assertIn("bad expr", err)

    def test_apply_missing_bundle_returns_2(self):
        rc, out, err = _run(
            "measure", "apply", "/no/such.measure.json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)


# --------------------------------------------------------------------------
# pull (mocked)
# --------------------------------------------------------------------------


class PullCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_pull_"))
        self.pvtproject = _make_project(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pull_ok(self):
        out_path = self.tmp / "x.snapshot.json"
        report = PvtMeasurePullReport(n_rows=3, path=str(out_path))
        with patch(
            "simkit.skill_bridge.pvt_measure_pull", return_value=report,
        ) as pull:
            rc, out, err = _run(
                "measure", "pull", str(out_path),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("pulled 3 rows", out)
        kwargs = pull.call_args.kwargs
        self.assertEqual(kwargs["test_name"], "Test")
        self.assertFalse(kwargs["include_signals"])

    def test_pull_include_signals(self):
        report = PvtMeasurePullReport(n_rows=11, path="/tmp/x.snapshot.json")
        with patch(
            "simkit.skill_bridge.pvt_measure_pull", return_value=report,
        ) as pull:
            _run(
                "measure", "pull", "/tmp/x.snapshot.json",
                "--include-signals",
                "--project", str(self.pvtproject),
            )
        self.assertTrue(pull.call_args.kwargs["include_signals"])

    def test_pull_bad_extension_returns_2(self):
        rc, out, err = _run(
            "measure", "pull", "/tmp/whatever.json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn(".snapshot.json", err)

    def test_pull_skillbridge_error_returns_4(self):
        with patch(
            "simkit.skill_bridge.pvt_measure_pull",
            side_effect=SkillBridgeError("pvt_io", "cannot export", None),
        ):
            rc, out, err = _run(
                "measure", "pull", str(self.tmp / "x.snapshot.json"),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 4)
        self.assertIn("cannot export", err)


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


class DiffCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_diff_"))
        self.pvtproject = _make_project(self.tmp)
        _seed_rise_template(self.tmp)
        _seed_signal_group(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_snapshot(self, name: str, rows: list[dict]) -> Path:
        body = {
            "snapshot_schema_version": 1,
            "session": "fnxSession0",
            "test": "Test",
            "rows": rows,
        }
        p = self.tmp / f"{name}.snapshot.json"
        p.write_text(json.dumps(body), encoding="utf-8")
        return p

    def test_diff_identical_bundles_returns_0(self):
        b = _seed_bundle(self.tmp)
        rc, out, err = _run(
            "measure", "diff", str(b), str(b),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("identical: 1", out)

    def test_diff_bundles_changed_returns_1(self):
        b1 = _seed_bundle(self.tmp, name="b1")
        b2 = _seed_bundle(
            self.tmp, name="b2",
            apply_entries=[
                {"template": "rise_time_threshold",
                 "signal_group": "voltage_outs",
                 "param_overrides": {"V_LOW": "20"}},
            ],
        )
        rc, out, err = _run(
            "measure", "diff", str(b1), str(b2),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 1, f"err={err}")

    def test_diff_snapshots_identical(self):
        rows = [{"name": "Rtime_Vout", "type": "expr",
                 "expression": "x", "plot": True, "save": False,
                 "spec": ""}]
        a = self._write_snapshot("a", rows)
        b = self._write_snapshot("b", rows)
        rc, out, err = _run(
            "measure", "diff", str(a), str(b),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("identical: 1", out)

    def test_diff_snapshots_changed_returns_1(self):
        a = self._write_snapshot("a", [
            {"name": "Rtime_Vout", "type": "expr",
             "expression": "x", "plot": True, "save": False, "spec": ""},
        ])
        b = self._write_snapshot("b", [
            {"name": "Rtime_Vout", "type": "expr",
             "expression": "y", "plot": True, "save": False, "spec": ""},
        ])
        rc, out, err = _run(
            "measure", "diff", str(a), str(b), "--json",
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 1, f"err={err}")
        data = json.loads(out)
        self.assertEqual(data["changed"][0]["name"], "Rtime_Vout")
        self.assertEqual(data["kind"], "rows")

    def test_diff_mixed_bundle_vs_snapshot(self):
        bundle = _seed_bundle(self.tmp)
        # Render expression that matches what the bundle would produce.
        expected_expr = (
            "average(riseTime(vtime('tran \"/Vout\") 0 nil "
            "VAR(\"VDD\") nil 10 90 t \"time\"))"
        )
        snap = self._write_snapshot("matching", [
            {"name": "Rtime_Vout", "type": "expr",
             "expression": expected_expr, "plot": True, "save": False,
             "spec": ""},
        ])
        rc, out, err = _run(
            "measure", "diff", str(bundle), str(snap),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("identical: 1", out)

    def test_diff_unknown_extension_returns_2(self):
        bad = self.tmp / "x.txt"
        bad.write_text("ignored", encoding="utf-8")
        rc, out, err = _run(
            "measure", "diff", str(bad), str(bad),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("unrecognised file type", err)


# --------------------------------------------------------------------------
# restore (mocked)
# --------------------------------------------------------------------------


class RestoreCliTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="simkit_cli_meas_restore_"))
        self.pvtproject = _make_project(self.tmp)
        self.csv = self.tmp / "snap.csv"
        self.csv.write_text(
            "Test,Name,Type,Output,Plot,Save,Spec\n"
            "Test,Rtime,expr,average(VT(\"/Vout\")),t,,\n",
            encoding="utf-8",
        )
        self.snapshot_json = self.tmp / "snap.snapshot.json"
        self.snapshot_json.write_text(json.dumps({
            "snapshot_schema_version": 1,
            "session": "fnxSession0",
            "test": "Test",
            "rows": [
                {"name": "Rtime", "type": "expr",
                 "expression": "average(VT(\"/Vout\"))",
                 "plot": True, "save": False, "spec": ""},
            ],
        }), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_restore_csv_ok(self):
        with patch(
            "simkit.skill_bridge.pvt_measure_restore", return_value=None,
        ) as restore:
            rc, out, err = _run(
                "measure", "restore", str(self.csv),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        self.assertIn("restored", out)
        self.assertEqual(restore.call_args.kwargs["operation"], "merge")

    def test_restore_json_rematerialises_csv(self):
        captured = {}

        def _fake(csv_path, **kwargs):
            captured["csv_path"] = csv_path
            captured["kwargs"] = kwargs

        with patch(
            "simkit.skill_bridge.pvt_measure_restore", side_effect=_fake,
        ):
            rc, out, err = _run(
                "measure", "restore", str(self.snapshot_json),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 0, f"err={err}")
        # The temp CSV should be a real file under /tmp at call time, but is
        # cleaned up after restore returns. We can verify the call shape.
        self.assertTrue(str(captured["csv_path"]).endswith(".csv"))

    def test_restore_explicit_operation(self):
        with patch(
            "simkit.skill_bridge.pvt_measure_restore", return_value=None,
        ) as restore:
            _run(
                "measure", "restore", str(self.csv),
                "--operation", "merge",
                "--project", str(self.pvtproject),
            )
        self.assertEqual(restore.call_args.kwargs["operation"], "merge")

    def test_restore_missing_file_returns_2(self):
        rc, out, err = _run(
            "measure", "restore", str(self.tmp / "missing.csv"),
            "--project", str(self.pvtproject),
        )
        self.assertEqual(rc, 2)
        self.assertIn("not found", err)

    def test_restore_skillbridge_error_returns_4(self):
        with patch(
            "simkit.skill_bridge.pvt_measure_restore",
            side_effect=SkillBridgeError(
                "pvt_io", "import returned nil", None,
            ),
        ):
            rc, out, err = _run(
                "measure", "restore", str(self.csv),
                "--project", str(self.pvtproject),
            )
        self.assertEqual(rc, 4)
        self.assertIn("import returned nil", err)


# --------------------------------------------------------------------------
# top-level help / dispatcher sanity
# --------------------------------------------------------------------------


class DispatcherTests(unittest.TestCase):

    def test_measure_root_help_exits_with_subcommand_required(self):
        # argparse exits 2 when required subparser is omitted.
        rc, out, err = _run("measure")
        self.assertEqual(rc, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
