import inspect
import unittest
import runner_v2 as r


class HealthProviderContractTest(unittest.TestCase):
    def test_provider_matches_frozen_public_health(self):
        frozen = (r.SNAPSHOT / 'scripts/search_api.py').read_text()
        self.assertIn('"provider": "frontier"', frozen)
        self.assertIn("expected_generation_provider='frontier'", inspect.getsource(r.start_workers))
        self.assertNotIn("expected_generation_provider='gemini'", inspect.getsource(r.start_workers))

    def test_versions_preserve_prior_run_and_launch_matching_worker(self):
        self.assertEqual(r.RUN.name, 'live-v2')
        self.assertEqual(r.PLAN.name, 'execution-plan-v2.json')
        self.assertIn("runner_v2.py", inspect.getsource(r.start_workers))


if __name__ == '__main__':
    unittest.main()
