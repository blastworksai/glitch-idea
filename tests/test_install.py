"""Behavior checks for materialized personal installs, using temporary homes only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.py"


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.local = self.root / "local"
        self.source = self.local / "glitch-idea"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: glitch-idea\ndescription: Shape new ideas.\n---\nCapture first.\n")
        (self.source / "scripts" / "idea.py").write_text("print('fixture')\n")
        self.store = self.local / "ideas"
        self.store.mkdir()
        self.saved = self.store / "state.json"
        self.saved.write_text('{"record":"untouched"}\n')
        (self.local / "config.json").write_text(json.dumps({
            "store_path": "ideas", "plan_validator_argv": None,
            "validator_timeout_seconds": 30,
        }))
        self.skills = self.root / "personal" / "skills"
        self.destination = self.skills / "glitch-idea"

    def files(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} if root.exists() else None

    def pin(self, version="6.0.3"):
        (self.source / "requirements.txt").write_text(f"# pinned\nPyYAML=={version}\n")

    def bare_venv(self, name="bare"):
        """A real interpreter without PyYAML: a stdlib venv with no site packages."""
        path = self.root / name
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(path)], check=True)
        return path

    def run_install(self, *args, success=True):
        result = subprocess.run([
            sys.executable, str(INSTALLER), "--source", str(self.source),
            "--skills-root", str(self.skills), *args,
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["ok"], success)
        return payload

    def test_materializes_and_keeps_data_outside_skill(self):
        result = self.run_install()
        self.assertEqual(result["action"], "installed")
        self.assertEqual((self.destination / "SKILL.md").read_bytes(), (self.source / "SKILL.md").read_bytes())
        self.assertFalse(any(p.is_symlink() for p in self.destination.rglob("*")))
        config = json.loads((self.destination / "config.json").read_text())
        self.assertEqual(config["store_path"], str(self.store))
        self.assertEqual(self.saved.read_text(), '{"record":"untouched"}\n')

    def test_repeat_is_idempotent(self):
        self.run_install()
        before = {str(p.relative_to(self.destination)): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.destination.rglob("*") if p.is_file()}
        result = self.run_install()
        after = {str(p.relative_to(self.destination)): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.destination.rglob("*") if p.is_file()}
        self.assertEqual(result["action"], "unchanged")
        self.assertEqual(before, after)
        self.assertFalse((self.skills / ".glitch-idea-backups").exists())

    def test_unrelated_collision_is_unchanged(self):
        self.destination.mkdir(parents=True)
        stranger = self.destination / "SKILL.md"
        stranger.write_text("Someone else's skill\n")
        self.run_install(success=False)
        self.assertEqual(stranger.read_text(), "Someone else's skill\n")
        self.assertEqual(list(self.destination.iterdir()), [stranger])

    def test_upgrade_preserves_backup_and_durable_data(self):
        self.run_install()
        old = (self.destination / "SKILL.md").read_bytes()
        (self.source / "SKILL.md").write_text("---\nname: glitch-idea\ndescription: Updated.\n---\nNew instructions.\n")
        result = self.run_install()
        self.assertEqual(result["action"], "upgraded")
        backup = Path(result["backup"])
        self.assertEqual((backup / "SKILL.md").read_bytes(), old)
        self.assertTrue((backup / ".glitch-idea-install.json").exists())
        self.assertEqual(self.saved.read_text(), '{"record":"untouched"}\n')

    def test_upgrade_keeps_explicit_store_and_local_modifications(self):
        other_store = self.root / "durable"
        self.run_install("--store", str(other_store))
        (self.destination / "personal-note.txt").write_text("Keep me\n")
        (self.source / "scripts" / "idea.py").write_text("print('upgraded')\n")
        result = self.run_install()
        config = json.loads((self.destination / "config.json").read_text())
        self.assertEqual(config["store_path"], str(other_store))
        self.assertEqual((Path(result["backup"]) / "personal-note.txt").read_text(), "Keep me\n")

    def test_dry_run_has_no_filesystem_effect(self):
        result = self.run_install("--dry-run")
        self.assertTrue(result["dry_run"])
        self.assertFalse(self.skills.exists())
        self.assertEqual(self.saved.read_text(), '{"record":"untouched"}\n')

    def test_missing_installed_config_refuses_without_repointing_store(self):
        self.run_install("--store", str(self.root / "custom-store"))
        (self.destination / "config.json").unlink()
        before = {str(p.relative_to(self.destination)): p.read_bytes()
                  for p in self.destination.rglob("*") if p.is_file()}
        self.run_install(success=False)
        after = {str(p.relative_to(self.destination)): p.read_bytes()
                 for p in self.destination.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((self.skills / ".glitch-idea-backups").exists())

    def test_incomplete_installed_config_refuses_without_repointing_store(self):
        self.run_install("--store", str(self.root / "custom-store"))
        config_file = self.destination / "config.json"
        config_file.write_text('{}\n')
        self.run_install(success=False)
        self.assertEqual(config_file.read_text(), '{}\n')
        self.assertFalse((self.skills / ".glitch-idea-backups").exists())

    def test_rejects_store_inside_installation(self):
        self.run_install("--store", str(self.destination / "ideas"), success=False)
        self.assertFalse(self.destination.exists())

    def test_rejects_source_and_destination_symlinks(self):
        (self.source / "scripts" / "elsewhere.py").symlink_to(self.saved)
        self.run_install(success=False)
        self.assertFalse(self.destination.exists())
        (self.source / "scripts" / "elsewhere.py").unlink()
        self.skills.mkdir(parents=True)
        self.destination.symlink_to(self.source, target_is_directory=True)
        self.run_install(success=False)
        self.assertTrue(self.destination.is_symlink())

    def test_real_source_package_installs_and_runs_without_private_config(self):
        package = self.root / 'clean-package'
        public_source = INSTALLER.parent / 'glitch-idea'
        public_package = package / 'glitch-idea'
        public_package.mkdir(parents=True)
        shutil.copy2(INSTALLER, package / 'install.py')
        for name in ('SKILL.md', 'requirements.txt', 'PyYAML-MIT-NOTICE.txt'):
            shutil.copy2(public_source / name, public_package / name)

        # package the current public modules, without local config or caches.
        def exclude_private(directory, names):
            return [name for name in names
                    if name.startswith('.') or name == '__pycache__'
                    or name == 'config.json' or name.endswith(('.pyc', '.pyo'))
                    or (Path(directory) / name).is_symlink()]

        for name in ('scripts', 'web', 'references', 'agents'):
            shutil.copytree(public_source / name, public_package / name,
                            ignore=exclude_private)
        self.assertFalse((package / 'config.json').exists())
        self.assertFalse((package / 'glitch-idea/config.json').exists())

        def invoke(script, *args):
            result = subprocess.run([sys.executable, str(script), *map(str, args)],
                                    capture_output=True, text=True, cwd=self.root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(data['ok'], data)
            return data

        installed = invoke(package / 'install.py', '--skills-root', self.skills)
        self.assertEqual(installed['store_path'], str(package / 'ideas'))
        config = json.loads((self.destination / 'config.json').read_text())
        self.assertIsNone(config['plan_validator_argv'])
        raw = self.root / 'capture.txt'
        wording = '  A new idea — preserve the original wording.\n'
        raw.write_text(wording, encoding='utf-8')
        helper = self.destination / 'scripts/idea.py'
        idea = invoke(helper, 'capture', '--text-file', raw, '--actor', 'operator')['idea']
        self.assertEqual(idea['origin']['text'], wording)
        self.assertIsNone(idea['ratings'])
        authority = package / 'ideas'
        self.assertTrue((authority / 'IDEAS.md').is_file())
        self.assertTrue((authority / (idea['idea_id'] + '.md')).is_file())
        self.assertFalse((authority / 'state.json').exists())
        self.assertEqual(invoke(helper, 'show', idea['idea_id'])['idea']['origin'], idea['origin'])
        self.assertTrue(invoke(helper, 'doctor')['healthy'])


    # CP5/J12a: supported keys, runtime selection and the pinned dependency check.

    def test_unknown_config_key_refuses_instead_of_dropping(self):
        (self.local / "config.json").write_text(json.dumps({"store_path": "ideas", "store_paht": "typo"}))
        error = self.run_install(success=False)["error"]
        self.assertIn("store_paht", error["message"])
        self.assertFalse(self.skills.exists())

    def test_default_workspace_is_a_supported_config_key_and_is_preserved(self):
        self.pin()
        folder = self.root / "project"; folder.mkdir()
        value = {"name": "Atlas", "path": str(folder)}
        (self.local / "config.json").write_text(json.dumps({"store_path": "ideas", "default_workspace": value}))
        self.run_install("--runtime-python", sys.executable, "--runtime-root", str(self.root / "state"))
        self.assertEqual(json.loads((self.destination / "config.json").read_text())["default_workspace"], value)

    def test_malformed_default_workspace_refuses(self):
        for bad in ({"name": "A"}, {"name": "A", "path": "relative"}, {"name": "", "path": "/x"}, "x", {"name": "A", "path": "/x", "extra": 1}):
            with self.subTest(bad=bad):
                (self.local / "config.json").write_text(json.dumps({"store_path": "ideas", "default_workspace": bad}))
                self.run_install(success=False)
                self.assertFalse(self.skills.exists())

    def test_malformed_runtime_and_validator_values_refuse(self):
        for bad in ({"runtime_root": "relative/state"}, {"runtime_root": None}, {"runtime_python": ""},
                    {"plan_validator_argv": ["x"] * 33}, {"plan_validator_argv": ["ok", "nul\x00"]}):
            with self.subTest(bad=bad):
                (self.local / "config.json").write_text(json.dumps({"store_path": "ideas", **bad}))
                self.run_install(success=False)
                self.assertFalse(self.skills.exists())

    def test_runtime_settings_are_written_and_preserved_on_upgrade(self):
        self.pin()
        state = self.root / "state"
        result = self.run_install("--runtime-python", sys.executable, "--runtime-root", str(state))
        self.assertEqual(result["runtime"]["status"], "ready")
        self.assertEqual(result["runtime"]["pyyaml"], "6.0.3")
        config = json.loads((self.destination / "config.json").read_text())
        self.assertEqual(config["runtime_python"], sys.executable)
        self.assertEqual(config["runtime_root"], str(state))
        (self.source / "SKILL.md").write_text("---\nname: glitch-idea\ndescription: Updated.\n---\n")
        self.assertEqual(self.run_install()["action"], "upgraded")
        upgraded = json.loads((self.destination / "config.json").read_text())
        self.assertEqual((upgraded["runtime_python"], upgraded["runtime_root"]), (sys.executable, str(state)))

    def test_fresh_install_without_runtime_says_launch_is_not_configured(self):
        result = self.run_install()
        self.assertEqual(result["runtime"]["status"], "not_configured")
        self.assertNotIn("runtime_python", json.loads((self.destination / "config.json").read_text()))

    def test_runtime_without_pyyaml_names_the_exact_prerequisite_and_changes_nothing(self):
        self.pin()
        python = self.bare_venv() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        error = self.run_install("--runtime-python", str(python), success=False)["error"]
        self.assertEqual(error["code"], "runtime_prerequisite")
        self.assertIn("missing PyYAML", error["message"])
        self.assertIn(f'"{python}" -m pip install -r "{self.source / "requirements.txt"}"', error["message"])
        self.assertFalse(self.skills.exists())

    def test_pin_mismatch_is_a_prerequisite_not_a_silent_pass(self):
        self.pin("0.0.1")
        error = self.run_install("--runtime-python", sys.executable, success=False)["error"]
        self.assertEqual(error["code"], "runtime_prerequisite")
        self.assertIn("has PyYAML 6.0.3; PyYAML 0.0.1 is required", error["message"])
        self.assertFalse(self.skills.exists())

    def test_venv_is_created_offline_then_selected_by_its_own_path(self):
        self.pin()
        venv = self.root / "runtime-venv"
        error = self.run_install("--venv", str(venv), success=False)["error"]
        self.assertEqual(error["code"], "runtime_prerequisite")
        self.assertIn("was created and kept", error["message"])
        self.assertTrue((venv / "pyvenv.cfg").is_file())
        self.assertFalse(self.skills.exists())
        # Stand in for the user's own pip step without a download: copy the local PyYAML in.
        import yaml
        site = next(venv.glob("lib/python*/site-packages")) if os.name != "nt" else venv / "Lib/site-packages"
        shutil.copytree(Path(yaml.__file__).parent, site / "yaml")
        result = self.run_install("--venv", str(venv))
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.assertEqual(result["runtime"], {"python": str(python), "python_version": result["runtime"]["python_version"],
                                             "pyyaml": "6.0.3", "status": "ready"})
        self.assertNotIn("venv_created", result)
        # The unresolved venv spelling is kept: resolving it would select the base interpreter.
        self.assertEqual(json.loads((self.destination / "config.json").read_text())["runtime_python"], str(python))

    def test_dry_run_with_venv_creates_nothing(self):
        venv = self.root / "planned-venv"
        result = self.run_install("--dry-run", "--venv", str(venv))
        self.assertEqual(result["runtime"]["status"], "would_create_venv")
        self.assertFalse(venv.exists())
        self.assertFalse(self.skills.exists())

    def test_runtime_choices_are_validated_before_any_write(self):
        not_venv = self.root / "occupied"
        not_venv.mkdir()
        (not_venv / "notes.txt").write_text("mine\n")
        cases = (("--venv", str(self.root / "a"), "--runtime-python", sys.executable),
                 ("--runtime-python", "python3"),
                 ("--venv", str(not_venv)),
                 ("--venv", str(self.store / "venv")),
                 ("--runtime-root", str(self.destination / "state")))
        for args in cases:
            with self.subTest(args=args):
                self.run_install(*args, success=False)
                self.assertFalse(self.skills.exists())
                self.assertFalse((self.root / "a").exists())
        self.assertEqual((not_venv / "notes.txt").read_text(), "mine\n")

    def test_refuses_to_install_inside_a_managed_glitch_brain(self):
        brain = self.root / "brain"
        (brain / ".claude" / "scripts").mkdir(parents=True)
        (brain / ".claude" / "scripts" / "update.py").write_text("# engine\n")
        (brain / "operations-reference.md").write_text("# manual\n")
        self.skills = brain / ".claude" / "skills"
        before = self.files(brain)
        error = self.run_install(success=False)["error"]
        self.assertIn("managed Glitch Brain", error["message"])
        self.assertEqual(self.files(brain), before)


    # Review CP5 round-1 regressions.

    def make_brain(self):
        brain = self.root / "brain"
        (brain / ".claude" / "scripts").mkdir(parents=True)
        (brain / ".claude" / "scripts" / "update.py").write_text("# engine\n")
        (brain / "operations-reference.md").write_text("# manual\n")
        return brain

    def test_runtime_root_with_a_symlink_component_is_refused_as_the_helper_would(self):
        target = self.root / "real-state"
        target.mkdir()
        (self.root / "alias").symlink_to(target, target_is_directory=True)
        error = self.run_install("--runtime-root", str(self.root / "alias" / "glitch"), success=False)["error"]
        self.assertIn("symlink", error["message"])
        (self.local / "config.json").write_text(json.dumps({"store_path": "ideas",
                                                            "runtime_root": str(self.root / "alias" / "x" / ".." / "y")}))
        self.assertIn("symlink", self.run_install(success=False)["error"]["message"])
        self.assertFalse(self.skills.exists())

    def test_null_runtime_python_is_refused_not_dropped(self):
        (self.local / "config.json").write_text(json.dumps({"store_path": "ideas", "runtime_python": None}))
        self.assertIn("runtime_python", self.run_install(success=False)["error"]["message"])
        self.assertFalse(self.skills.exists())

    def test_venv_reaching_the_store_through_a_symlink_is_refused(self):
        (self.root / "venvs").symlink_to(self.store, target_is_directory=True)
        venv = self.root / "venvs" / "glitch"
        self.run_install("--venv", str(venv), success=False)
        self.assertFalse((self.store / "glitch").exists())
        self.assertFalse(self.skills.exists())

    def test_runtime_paths_and_store_inside_brain_engine_are_refused(self):
        brain = self.make_brain()
        for args in (("--runtime-root", str(brain / ".claude" / "state")),
                     ("--venv", str(brain / ".claude" / "venv")),
                     ("--store", str(brain / "ideas"))):
            with self.subTest(args=args):
                self.assertIn("managed Glitch Brain", self.run_install(*args, success=False)["error"]["message"])
                self.assertFalse((brain / ".claude" / "venv").exists())
                self.assertFalse(self.skills.exists())

    def test_existing_venv_inside_brain_engine_is_refused(self):
        brain = self.make_brain()
        venv = brain / ".claude" / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
        self.assertTrue((venv / "bin" / "python").is_symlink() or os.name == "nt")
        error = self.run_install("--venv", str(venv), success=False)["error"]
        self.assertIn("managed Glitch Brain", error["message"])
        self.assertFalse(self.skills.exists())

    def test_short_paths_that_expand_beyond_the_bound_are_refused(self):
        # "~/s" is three characters; the helper reads its expanded absolute form.
        env = dict(os.environ, HOME="/" + "h" * 4100)
        for flag in ("--store", "--runtime-root"):
            with self.subTest(flag=flag):
                result = subprocess.run([sys.executable, str(INSTALLER), "--source", str(self.source),
                                         "--skills-root", str(self.skills), flag, "~/s"],
                                        capture_output=True, text=True, env=env)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("4096", json.loads(result.stdout)["error"]["message"])
                self.assertFalse(self.skills.exists())

    def test_store_that_contains_the_source_is_refused(self):
        error = self.run_install("--store", str(self.local), success=False)["error"]
        self.assertIn("durable store", error["message"])
        self.assertFalse(self.skills.exists())

    def test_venv_inside_the_default_runtime_root_is_refused(self):
        import pwd
        default = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local" / "state" / "glitch-idea"
        venv = default / "venv-install-test-never-created"
        error = self.run_install("--dry-run", "--venv", str(venv), success=False)["error"]
        self.assertIn("runtime root", error["message"])
        self.assertFalse(venv.exists())

    def test_brain_member_folders_are_not_managed(self):
        brain = self.make_brain()
        result = self.run_install("--store", str(brain / "workspaces" / "ideas"),
                                  "--runtime-root", str(brain / "_local" / "state"))
        self.assertEqual(result["action"], "installed")

    def test_no_venv_is_created_when_the_package_is_refused(self):
        (self.source / "scripts" / "elsewhere.py").symlink_to(self.saved)
        venv = self.root / "never"
        self.assertIn("Symlinks", self.run_install("--venv", str(venv), success=False)["error"]["message"])
        self.assertFalse(venv.exists())

    def test_pythonpath_cannot_lend_the_runtime_a_pyyaml(self):
        self.pin()
        python = self.bare_venv() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        import yaml
        env = dict(os.environ, PYTHONPATH=str(Path(yaml.__file__).parent.parent))
        result = subprocess.run([sys.executable, str(INSTALLER), "--source", str(self.source),
                                 "--skills-root", str(self.skills), "--runtime-python", str(python)],
                                capture_output=True, text=True, env=env)
        error = json.loads(result.stdout)["error"]
        self.assertEqual(error["code"], "runtime_prerequisite", result.stdout)
        # Missing, not a mismatch: the lent PyYAML must not have answered at all.
        self.assertIn("is missing PyYAML", error["message"])
        self.assertFalse(self.skills.exists())

    def test_nul_store_path_is_a_json_refusal(self):
        (self.local / "config.json").write_text(json.dumps({"store_path": "ide\u0000as"}))
        self.assertEqual(self.run_install(success=False)["error"]["code"], "install_error")

    def test_validator_beyond_the_launch_limit_is_refused_at_install(self):
        (self.local / "config.json").write_text(json.dumps({"store_path": "ideas",
                                                            "plan_validator_argv": ["a" * 600] * 30}))
        self.assertIn("launch limit", self.run_install(success=False)["error"]["message"])


if __name__ == "__main__":
    unittest.main()
