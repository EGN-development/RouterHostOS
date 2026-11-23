import tempfile
import unittest
from rhos.config import ConfigError, Conflict, DEFAULT, Store, validate
from rhos.cli import execute


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_commit_persists_and_loads_revision(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        committed = self.store.commit(0)
        loaded = Store(self.temp.name).snapshot()
        self.assertEqual(committed["revision"], 1)
        self.assertEqual(loaded["config"]["addresses"], [{"address": "192.0.2.1/24", "interface": "eth1"}])

    def test_conflicting_revision_does_not_overwrite(self):
        self.store.commit(0)
        with self.assertRaises(Conflict):
            self.store.commit(0)
        self.assertEqual(self.store.snapshot()["revision"], 1)

    def test_invalid_candidate_rejected(self):
        config = dict(DEFAULT)
        config["addresses"] = [{"address": "not-an-ip", "interface": "eth1"}]
        with self.assertRaises(ConfigError):
            validate(config)

    def test_cli_and_store_share_candidate(self):
        result = execute(self.store, "/ip address add address=192.0.2.1/24 interface=eth1")
        self.assertEqual(result, "candidate address added")
        self.assertEqual(self.store.snapshot()["candidate"]["addresses"][0]["address"], "192.0.2.1/24")
        self.store.commit(0)
        self.assertEqual(execute(self.store, "/ip address print"), "192.0.2.1/24         eth1")
        self.assertNotIn("candidate", self.store.snapshot())

    def test_candidate_persists_across_store_restart(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        restarted = Store(self.temp.name)
        self.assertEqual(restarted.snapshot()["candidate"]["addresses"][0]["address"], "192.0.2.1/24")

    def test_stale_store_cannot_overwrite_concurrent_revision(self):
        other = Store(self.temp.name)
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        other.mutate(0, "address_add", {"address": "198.51.100.1/24", "interface": "eth2"})
        other.commit(0)
        with self.assertRaises(Conflict):
            self.store.commit(0)

    def test_apply_disabled_preserves_revision(self):
        with self.assertRaises(ConfigError):
            self.store.commit(0, apply=True)
        self.assertEqual(self.store.snapshot()["revision"], 0)

    def test_rollback_stages_historical_candidate(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        self.store.commit(0)
        self.store.mutate(1, "address_add", {"address": "198.51.100.1/24", "interface": "eth2"})
        self.store.commit(1)
        staged = self.store.rollback(1, 2)
        self.assertEqual(len(staged["candidate"]["addresses"]), 1)


if __name__ == "__main__":
    unittest.main()
