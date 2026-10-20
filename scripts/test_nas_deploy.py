"""Isolated shell workflow tests; fake Docker, no socket or live services required."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
MOCK = '''import json, os, sys
a = sys.argv[1:]
with open(os.environ['DOCKER_LOG'], 'a') as f: f.write(json.dumps(a)+'\\n')
phase = os.environ.get('FAIL_PHASE')
if phase == 'preflight' and any('MigrationRecorder' in s and 'or 1,' not in s for s in a): sys.exit(1)
if phase == 'backup' and a[-2:] == ['python', '-']: sys.exit(1)
if phase == 'init' and 'run' in a and a[-1] == 'init': sys.exit(1)
if a[:2] == ['image', 'inspect']: print('sha256:' + 'a'*64)
elif a[0] == 'inspect': print('' if '.Mounts' in a[2] else ('true' if 'Running' in a[2] else 'old-container sha256:old'))
elif any('print(settings.BACKUP_DIR)' in s for s in a): print('/backups')
elif a[0] == 'compose' and 'ps' in a: print('container-' + a[-1])
'''


class DeployTests(unittest.TestCase):
    def exercise(self, phase='', check=False, include_rights=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            shutil.copy(ROOT/'nas-deploy.sh', path)
            (path/'compose.yaml').write_text('services: {}\n')
            (path/'scripts').mkdir()
            (path/'scripts/pre_archive_update_backup.py').write_text('# fixture\n')
            binary = path/'bin'
            binary.mkdir()
            docker = binary/'docker'
            docker.write_text('#!' + sys.executable + '\n' + MOCK)
            docker.chmod(0o700)
            env = dict(os.environ, PATH=str(binary)+':'+os.environ['PATH'],
                       DOCKER_LOG=str(path/'calls.jsonl'), FAIL_PHASE=phase)
            result = subprocess.run(['bash', str(path/'nas-deploy.sh'), '--project', 'existing-eta'] +
                                    (['--check'] if check else []) + (['--include-rights-update'] if include_rights else []), env=env, capture_output=True, text=True)
            calls = [json.loads(line) for line in (path/'calls.jsonl').read_text().splitlines()]
            override = (path/'compose.deployed.yaml').read_text() if (path/'compose.deployed.yaml').exists() else None
            self.assertFalse((path/'.nas-deploy.lock').exists())
            return result, calls, override

    def test_check_does_not_stop_or_migrate(self):
        result, calls, override = self.exercise(check=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(override)
        self.assertFalse(any('stop' in a or a[-1] == 'init' for a in calls))

    def test_success_backs_up_before_stop_and_pins_image(self):
        result, calls, override = self.exercise()
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next(i for i, a in enumerate(calls) if a[-2:] == ['python', '-'])
        stop = next(i for i, a in enumerate(calls) if 'stop' in a)
        init = next(i for i, a in enumerate(calls) if a[-1] == 'init')
        self.assertLess(backup, stop)
        self.assertLess(stop, init)
        self.assertEqual(override.count('sha256:' + 'a'*64), 3)
        self.assertEqual(override.count('./backups:/backups'), 3)
        copied = next(i for i, a in enumerate(calls) if a[0] == 'cp')
        self.assertLess(backup, copied)
        self.assertLess(copied, stop)
        self.assertFalse(any('down' in a or '-v' in a for a in calls))

    def test_older_schema_does_not_stop_services(self):
        result, calls, override = self.exercise(phase='preflight')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in a for a in calls))
        self.assertIsNone(override)

    def test_failed_backup_does_not_stop_services(self):
        result, calls, override = self.exercise(phase='backup')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('stop' in a for a in calls))
        self.assertIsNone(override)

    def test_failed_migration_does_not_start_services_or_publish_override(self):
        result, calls, override = self.exercise(phase='init')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('up' in a for a in calls))
        self.assertIsNone(override)
        self.assertIn('web and worker are stopped', result.stderr)

    def test_explicit_rights_upgrade_allows_old_schema(self):
        result, calls, override = self.exercise(phase='preflight', include_rights=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(override)


if __name__ == '__main__':
    unittest.main()
