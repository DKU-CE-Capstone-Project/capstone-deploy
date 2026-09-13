import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).resolve().parents[1] / 'gcp/deploy.py')
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(deploy, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.old = {'id': 'old', 'revisions': {'backend': 'old'}}
        self.new = {'id': 'new', 'revisions': {'backend': 'new'}}
        deploy.write(self.root / 'state/current.json', self.old)

    def test_failed_health_restores_previous_and_does_not_promote(self):
        with patch.object(deploy, 'activate', side_effect=[RuntimeError('unhealthy'), None]) as activate:
            with self.assertRaisesRegex(RuntimeError, 'unhealthy'):
                deploy.promote(self.new, self.old)
        self.assertEqual([call.args[0] for call in activate.call_args_list], [self.new, self.old])
        self.assertEqual(deploy.read(self.root / 'state/current.json'), self.old)
        self.assertFalse((self.root / 'state/pending.json').exists())

    def test_failed_rollback_leaves_recovery_transaction(self):
        with patch.object(deploy, 'activate', side_effect=RuntimeError('unhealthy')):
            with self.assertRaises(RuntimeError):
                deploy.promote(self.new, self.old)
        self.assertEqual(deploy.read(self.root / 'state/pending.json')['previous'], self.old)

    def test_success_commits_current_and_keeps_previous(self):
        with patch.object(deploy, 'activate'):
            deploy.promote(self.new, self.old)
        self.assertEqual(deploy.read(self.root / 'state/current.json'), self.new)
        self.assertEqual(deploy.read(self.root / 'state/previous.json'), self.old)
        self.assertFalse((self.root / 'state/pending.json').exists())

    def test_ci_requires_success_for_the_exact_main_sha(self):
        refs = {'backend': 'a' * 40}
        wrong_run = {'workflow_runs': [{'head_sha': 'b' * 40, 'status': 'completed', 'conclusion': 'success'}]}
        with patch.object(deploy, 'json_url', return_value=wrong_run):
            self.assertFalse(deploy.ci_ready(refs))
        with patch.object(deploy, 'json_url') as request:
            self.assertFalse(deploy.ci_ready(refs))
            request.assert_not_called()

    def test_successful_ci_is_cached(self):
        refs = {'backend': 'a' * 40}
        good_run = {'workflow_runs': [{'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success'}]}
        with patch.object(deploy, 'json_url', return_value=good_run) as request:
            self.assertTrue(deploy.ci_ready(refs))
            self.assertTrue(deploy.ci_ready(refs))
            self.assertEqual(request.call_count, 1)

    def test_same_failed_snapshot_is_not_redeployed(self):
        refs = {'backend': 'bad'}
        deploy.write(self.root / 'state/failed.json', {'id': deploy.release_id(refs)})
        with patch.object(deploy, 'revisions', return_value=refs), patch.object(deploy, 'ci_ready') as ci:
            deploy.deploy()
            ci.assert_not_called()

    def test_interrupted_activation_is_recovered_before_new_work(self):
        deploy.write(self.root / 'state/pending.json', {'previous': self.old, 'candidate': self.new})
        with patch.object(deploy, 'activate') as activate, patch.object(deploy, 'revisions', return_value=self.old['revisions']):
            deploy.deploy()
            activate.assert_called_once_with(self.old)
        self.assertEqual(deploy.read(self.root / 'state/current.json'), self.old)
        self.assertFalse((self.root / 'state/pending.json').exists())
