import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight


class PreflightTests(unittest.TestCase):
    def test_parser_requires_explicit_artifacts_only_in_full_execution(self):
        args = preflight.build_parser().parse_args(["--mode", "quick", "--snapshot", "head"])
        self.assertEqual(args.mode, "quick")

    def test_staged_rejects_unstaged_tracked_changes(self):
        with patch.object(preflight, "git", return_value="tree"), patch.object(
            preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
        ):
            with self.assertRaisesRegex(preflight.PreflightError, "unstaged"):
                preflight.tree_for("staged")

    def test_auto_rejects_unstaged_tracked_changes(self):
        with patch.object(preflight, "linux_safety_precheck"), patch.object(
            preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)
        ):
            self.assertEqual(preflight.main(["--mode", "quick", "--snapshot", "auto"]), 1)

    def test_native_requirement_cannot_be_skipped_in_quick_mode(self):
        self.assertEqual(preflight.main(["--mode", "quick", "--require-native"]), 1)

    def test_run_step_reports_label_and_exit_code(self):
        output = io.StringIO()
        failed = subprocess.CompletedProcess([], 7)
        with patch.object(preflight.subprocess, "run", return_value=failed):
            with contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(preflight.PreflightError, "cargo check failed with exit code 7"):
                    preflight.run_step("cargo check", ["cargo", "check"], Path("."), {})
        self.assertIn("cargo check", output.getvalue())

    def test_run_step_records_orchestration_context(self):
        seen = {}
        def fake_run(command, cwd, env):
            seen.update(command=command, cwd=cwd, env=env)
            return subprocess.CompletedProcess(command, 0)
        with patch.object(preflight.subprocess, "run", side_effect=fake_run):
            preflight.run_step("format", ["cargo", "fmt"], Path("snapshot"), {"CARGO_NET_OFFLINE": "true"})
        self.assertEqual(seen["command"], ["cargo", "fmt"])
        self.assertEqual(seen["cwd"], Path("snapshot"))
        self.assertEqual(seen["env"]["CARGO_NET_OFFLINE"], "true")

    def test_manifest_mismatch_is_actionable(self):
        with patch.object(preflight, "git", side_effect=["a\nb\n"]), patch.object(
            preflight.subprocess, "check_output", side_effect=[b"a", b"b", b"wrong"]
        ):
            with self.assertRaisesRegex(preflight.PreflightError, "manifest"):
                preflight.verify_manifest("tree")

    def test_full_main_orchestrates_isolated_bounded_release_checks(self):
        commands = []

        def fake_run(command, **kwargs):
            commands.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        def fake_step(label, command, cwd, env):
            commands.append((label, command, cwd, env))

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            preflight, "linux_safety_precheck"
        ), patch.object(preflight, "tree_for", return_value="tree"), patch.object(
            preflight, "verify_manifest"
        ), patch.object(preflight, "extract_archive"), patch.object(
            preflight.shutil, "which", return_value="/usr/bin/cargo"
        ), patch.object(preflight.subprocess, "run", side_effect=fake_run), patch.object(
            preflight, "run_step", side_effect=fake_step
        ), patch.object(
            preflight.Path, "glob", return_value=[Path(temporary) / "actinv.whl"]
        ), patch.object(preflight.venv.EnvBuilder, "create"), patch.object(
            preflight, "desktop_executable_name", return_value="actinv-gui.exe"
        ):
            self.assertEqual(
                preflight.main(
                    ["--snapshot", "head", "--mode", "full", "--require-native"]
                ),
                0,
            )

        steps = {entry[0]: entry for entry in commands if isinstance(entry[0], str)}
        wheel = steps["build wheel"]
        self.assertIn("python/Cargo.toml", wheel[1])
        wheel_env = wheel[3]
        self.assertEqual(
            {name: wheel_env[name] for name in (
                "CARGO_BUILD_JOBS", "RUST_TEST_THREADS", "RAYON_NUM_THREADS"
            )},
            {"CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1", "RAYON_NUM_THREADS": "2"},
        )
        targets = {entry[3]["CARGO_TARGET_DIR"] for entry in commands if len(entry) == 4}
        self.assertEqual(len(targets), 1)
        desktop = steps["desktop model/native smoke"]
        self.assertTrue(str(desktop[1][2]).endswith("actinv-gui.exe"))
        self.assertEqual(desktop[1][-1], "--require-render")
        self.assertTrue(any(entry[0] == "installed wheel smoke" for entry in commands))
        installed = steps["installed wheel smoke"][3]
        self.assertEqual(installed["PYTHONPATH"], "")
        self.assertEqual(installed["PYTHONHOME"], "")

    def test_full_prerequisite_failure_happens_before_cargo(self):
        with patch.object(preflight, "linux_safety_precheck"), patch.object(
            preflight, "tree_for", return_value="tree"
        ), patch.object(preflight, "verify_manifest"), patch.object(
            preflight, "extract_archive"
        ), patch.object(preflight.shutil, "which", return_value=None), patch.object(
            preflight, "run_step"
        ) as run_step:
            self.assertEqual(preflight.main(["--snapshot", "head", "--mode", "full"]), 1)
            run_step.assert_not_called()

    def test_cgroup_precheck_does_not_require_root_controller_files(self):
        def read_text(path, **kwargs):
            if path == Path("/proc/self/cgroup"):
                return "0::/worker\n"
            if path == Path("/sys/fs/cgroup/worker/memory.max"):
                return "1073741824\n"
            if path == Path("/sys/fs/cgroup/worker/pids.max"):
                return "64\n"
            raise FileNotFoundError(path)

        with patch.object(preflight.Path, "is_dir", return_value=True), patch.object(
            preflight.Path, "read_text", autospec=True, side_effect=read_text
        ), patch.object(preflight.sys, "platform", "linux"):
            preflight.linux_safety_precheck()

    def assert_cgroup_rejected(self, files):
        def read_text(path, **kwargs):
            if path == Path("/proc/self/cgroup"):
                return "0::/worker/leaf\n"
            if path in files:
                return files[path]
            raise FileNotFoundError(path)

        with patch.object(preflight.Path, "is_dir", return_value=True), patch.object(
            preflight.Path, "read_text", autospec=True, side_effect=read_text
        ), patch.object(preflight.sys, "platform", "linux"):
            with self.assertRaisesRegex(preflight.PreflightError, "cgroup"):
                preflight.linux_safety_precheck()

    def test_cgroup_precheck_rejects_unlimited_controls(self):
        self.assert_cgroup_rejected({
            Path("/sys/fs/cgroup/worker/leaf/memory.max"): "max\n",
            Path("/sys/fs/cgroup/worker/leaf/pids.max"): "128\n",
            Path("/sys/fs/cgroup/worker/memory.max"): "max\n",
            Path("/sys/fs/cgroup/worker/pids.max"): "max\n",
        })

    def test_cgroup_precheck_rejects_missing_controls(self):
        self.assert_cgroup_rejected({
            Path("/sys/fs/cgroup/worker/leaf/memory.max"): "1073741824\n",
        })

    def test_cgroup_precheck_rejects_overlimit_controls(self):
        self.assert_cgroup_rejected({
            Path("/sys/fs/cgroup/worker/leaf/memory.max"): f"{7 * 1024**3}\n",
            Path("/sys/fs/cgroup/worker/leaf/pids.max"): "128\n",
        })

    def test_cgroup_precheck_accepts_bounded_ancestor_with_unlimited_leaf(self):
        def read_text(path, **kwargs):
            if path == Path("/proc/self/cgroup"):
                return "0::/worker/leaf\n"
            values = {
                Path("/sys/fs/cgroup/worker/leaf/memory.max"): "max\n",
                Path("/sys/fs/cgroup/worker/leaf/pids.max"): "max\n",
                Path("/sys/fs/cgroup/worker/memory.max"): "1073741824\n",
                Path("/sys/fs/cgroup/worker/pids.max"): "64\n",
            }
            if path in values:
                return values[path]
            raise FileNotFoundError(path)

        with patch.object(preflight.Path, "is_dir", return_value=True), patch.object(
            preflight.Path, "read_text", autospec=True, side_effect=read_text
        ), patch.object(preflight.sys, "platform", "linux"):
            preflight.linux_safety_precheck()

    def test_maturin_prerequisite_failure_happens_before_cargo(self):
        def fail_maturin(command, **kwargs):
            return subprocess.CompletedProcess(command, 1 if command[1:3] == ["-m", "maturin"] else 0)

        with patch.object(preflight, "linux_safety_precheck"), patch.object(
            preflight, "tree_for", return_value="tree"
        ), patch.object(preflight, "verify_manifest"), patch.object(
            preflight, "extract_archive"
        ), patch.object(preflight.shutil, "which", return_value="/usr/bin/cargo"), patch.object(
            preflight.subprocess, "run", side_effect=fail_maturin
        ), patch.object(preflight, "run_step") as run_step:
            self.assertEqual(preflight.main(["--snapshot", "head", "--mode", "full"]), 1)
            run_step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
