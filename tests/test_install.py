"""Behavior checks for materialized personal installs, using temporary homes only."""
import json
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
        files = [
            'install.py', 'glitch-idea/SKILL.md',
            'glitch-idea/references/commands.md', 'glitch-idea/references/methods.md',
            'glitch-idea/agents/openai.yaml', 'glitch-idea/scripts/idea.py',
            'glitch-idea/scripts/idea_domain.py', 'glitch-idea/scripts/idea_store.py',
        ]
        for name in files:
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(INSTALLER.parent / name, target)
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
        self.assertTrue((package / 'ideas/state.json').is_file())
        self.assertTrue(invoke(helper, 'doctor')['healthy'])


if __name__ == "__main__":
    unittest.main()
