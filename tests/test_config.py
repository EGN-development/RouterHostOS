import tempfile
import unittest
from unittest.mock import patch
from rhos.config import ConfigError, Conflict, DEFAULT, Store, validate
from rhos.cli import execute
from rhos.linux import apply_config, firewall_script
from rhos import linux


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
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        self.store.commit(0)
        with self.assertRaises(Conflict):
            self.store.commit(0)
        self.assertEqual(self.store.snapshot()["revision"], 1)

    def test_invalid_candidate_rejected(self):
        config = dict(DEFAULT)
        config["addresses"] = [{"address": "not-an-ip", "interface": "eth1"}]
        with self.assertRaises(ConfigError):
            validate(config)

    def test_firewall_default_drops_forward_and_allows_established(self):
        script = firewall_script(DEFAULT)
        self.assertIn("policy drop", script)
        self.assertIn("ct state established,related accept", script)

    def test_firewall_rule_uses_validated_interface_tokens(self):
        config = {
            "system": {"hostname": "router01"}, "interfaces": [], "addresses": [], "routes": [],
            "firewall": {"forward": [{"in-interface": "lan0", "out-interface": "wan0", "action": "accept"}], "nat": []},
        }
        validate(config)
        self.assertIn('iifname "lan0" oifname "wan0" accept', firewall_script(config))
        config["firewall"]["forward"][0]["in-interface"] = 'lan0"; flush ruleset; #'
        with self.assertRaises(ConfigError):
            validate(config)

    def test_apply_config_diffs_owned_addresses_and_routes(self):
        config = {
            "system": {"hostname": "router01"}, "interfaces": [],
            "addresses": [{"address": "192.0.2.1/24", "interface": "lan0"}],
            "routes": [{"dst-address": "198.51.100.0/24", "gateway": "192.0.2.254", "metric": 10}],
            "firewall": {"forward": [], "nat": []},
        }
        responses = [
            '{"nftables":[]}',
            '[{"ifname":"lan0","addr_info":[]}]',
            '[]',
        ]
        commands = []

        def fake_run(argv, input_text=None):
            commands.append((argv, input_text))
            if argv[:3] == ["nft", "-j", "list"]:
                return responses[0]
            if argv[:3] == ["ip", "-j", "address"]:
                return responses[1]
            if argv[:4] == ["ip", "-j", "route", "show"]:
                return responses[2]
            if argv[:2] == ["nft", "-c"]:
                return ""
            return ""

        with patch("rhos.linux.run", side_effect=fake_run), patch("builtins.open", unittest.mock.mock_open(read_data="0")):
            apply_config(config)
        argv = [item[0] for item in commands]
        self.assertIn(["ip", "address", "replace", "192.0.2.1/24", "dev", "lan0"], argv)
        self.assertIn(["ip", "route", "replace", "198.51.100.0/24", "via", "192.0.2.254", "metric", "10"], argv)
        self.assertIn(["sysctl", "-w", "net.ipv4.ip_forward=1"], argv)

    def test_interface_inventory_joins_live_addresses_and_counters(self):
        link_json = '[{"ifname":"eth0","operstate":"UP","mtu":1500,"address":"02:00:00:00:00:01","stats64":{"rx":{"bytes":123,"packets":4},"tx":{"bytes":456,"packets":5}},"linkinfo":{"info_kind":"vlan"}}]'
        address_json = '[{"ifname":"eth0","addr_info":[{"family":"inet","local":"192.0.2.1","prefixlen":24,"scope":"global"}]}]'
        with patch("rhos.linux.run", side_effect=[link_json, address_json]):
            inventory = linux.interfaces()
        self.assertEqual(inventory[0]["kind"], "vlan")
        self.assertEqual(inventory[0]["addresses"][0]["address"], "192.0.2.1")
        self.assertEqual(inventory[0]["rx"]["bytes"], 123)

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

    def test_network_apply_requires_service_profile_opt_in(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "0"}):
            with self.assertRaises(ConfigError):
                self.store.commit(0, apply=True)
        self.assertEqual(self.store.snapshot()["revision"], 0)

    def test_failed_apply_reapplies_previous_desired_state(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch("rhos.linux.apply_via_worker", side_effect=[None, RuntimeError("new apply failed"), None]) as apply:
            with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}):
                with self.assertRaises(RuntimeError):
                    self.store.commit(0, apply=True)
        self.assertEqual(apply.call_count, 3)
        self.assertEqual(apply.call_args_list[0].args[0]["addresses"], [])
        self.assertEqual(apply.call_args_list[1].args[0]["addresses"][0]["address"], "192.0.2.1/24")
        self.assertEqual(apply.call_args_list[2].args[0]["addresses"], [])
        self.assertEqual(self.store.snapshot()["revision"], 0)

    def test_persistence_failure_rolls_back_applied_state(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch("rhos.linux.apply_via_worker") as apply:
            with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}):
                with patch.object(self.store, "_persist", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        self.store.commit(0, apply=True)
        self.assertEqual(apply.call_count, 3)
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
