import tempfile
import unittest
from pathlib import Path
from pathlib import Path as _PathForRead
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

    def test_apply_config_adds_desired_address_and_route(self):
        config = {
            "system": {"hostname": "router01"}, "interfaces": [],
            "addresses": [{"address": "192.0.2.1/24", "interface": "lan0"}],
            "routes": [{"dst-address": "198.51.100.0/24", "gateway": "192.0.2.254", "metric": 10}],
            "firewall": {"forward": [], "nat": []},
        }
        commands = []

        def fake_run(argv, input_text=None):
            commands.append((argv, input_text))
            if argv[:3] == ["ip", "-j", "address"]:
                return '[{"ifname":"lan0","addr_info":[]}]'
            if argv[:4] == ["ip", "-j", "route", "show"]:
                return '[]'
            if argv[:5] == ["ip", "-j", "-details", "link", "show"]:
                return '[{"ifname":"lan0","mtu":1500,"operstate":"UP"}]'
            if argv[:2] == ["nft", "-c"]:
                return ""
            if argv[:3] == ["nft", "-j", "list"]:
                return '{"nftables":[]}'
            return ""

        original_read_text = Path.read_text
        with tempfile.TemporaryDirectory() as state_dir, patch("rhos.linux.run", side_effect=fake_run), patch("rhos.linux.Path.read_text", autospec=True, side_effect=lambda path, *args, **kwargs: "0" if str(path) == "/proc/sys/net/ipv4/ip_forward" else original_read_text(path, *args, **kwargs)):
            apply_config(config, state_path=Path(state_dir) / "applied.json")
        argv = [item[0] for item in commands]
        self.assertIn(["ip", "address", "add", "192.0.2.1/24", "dev", "lan0"], argv)
        self.assertIn(["ip", "route", "add", "198.51.100.0/24", "via", "192.0.2.254", "metric", "10"], argv)
        self.assertIn(["sysctl", "-w", "net.ipv4.ip_forward=1"], argv)

    def test_apply_config_does_not_claim_or_delete_unmanaged_host_network(self):
        config = {"system": {"hostname": "router01"}, "interfaces": [], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        commands = []

        def fake_run(argv, input_text=None):
            commands.append(argv)
            if argv[:2] == ["nft", "-c"]:
                return ""
            if argv[:3] == ["ip", "-j", "address"]:
                return '[{"ifname":"eth0","addr_info":[{"family":"inet","local":"10.0.0.5","prefixlen":24,"scope":"global"}]}]'
            if argv[:4] == ["ip", "-j", "route", "show"]:
                return '[{"dst":"default","gateway":"10.0.0.1","metric":100}]'
            if argv[:5] == ["ip", "-j", "-details", "link", "show"]:
                return '[{"ifname":"eth0","mtu":1500,"operstate":"UP"}]'
            if argv[:3] == ["nft", "-j", "list"]:
                return '{"nftables":[]}'
            return ""

        original_read_text = Path.read_text
        with tempfile.TemporaryDirectory() as state_dir, patch("rhos.linux.run", side_effect=fake_run), patch("rhos.linux.Path.read_text", autospec=True, side_effect=lambda path, *args, **kwargs: "0" if str(path) == "/proc/sys/net/ipv4/ip_forward" else original_read_text(path, *args, **kwargs)):
            apply_config(config, state_path=Path(state_dir) / "applied.json")
        self.assertFalse(any(argv[:3] in (["ip", "address", "del"], ["ip", "route", "del"]) for argv in commands))

    def test_apply_config_only_removes_resources_recorded_as_owned(self):
        config = {"system": {"hostname": "router01"}, "interfaces": [], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        commands = []

        def fake_run(argv, input_text=None):
            commands.append(argv)
            if argv[:2] == ["nft", "-c"]:
                return ""
            if argv[:3] == ["ip", "-j", "address"]:
                return '[{"ifname":"lan0","addr_info":[{"family":"inet","local":"192.0.2.1","prefixlen":24,"scope":"global"}]}]'
            if argv[:4] == ["ip", "-j", "route", "show"]:
                return '[{"dst":"198.51.100.0/24","gateway":"192.0.2.254","metric":10}]'
            if argv[:5] == ["ip", "-j", "-details", "link", "show"]:
                return '[{"ifname":"lan0","mtu":1500,"operstate":"UP"}]'
            if argv[:3] == ["nft", "-j", "list"]:
                return '{"nftables":[]}'
            return ""

        original_read_text = Path.read_text
        with tempfile.TemporaryDirectory() as state_dir, patch("rhos.linux.run", side_effect=fake_run), patch("rhos.linux.Path.read_text", autospec=True, side_effect=lambda path, *args, **kwargs: "0" if str(path) == "/proc/sys/net/ipv4/ip_forward" else original_read_text(path, *args, **kwargs)):
            state_path = Path(state_dir) / "applied.json"
            original_read_text  # keep a reference to the real Path method for state file assertions
            Path.write_text(state_path, '{"addresses":[["lan0","192.0.2.1/24"]],"routes":[["198.51.100.0/24","192.0.2.254",10]]}')
            with patch("rhos.linux._load_applied_state", return_value={"addresses": [["lan0", "192.0.2.1/24"]], "routes": [["198.51.100.0/24", "192.0.2.254", 10]]}):
                apply_config(config, state_path=state_path)
        self.assertIn(["ip", "address", "del", "192.0.2.1/24", "dev", "lan0"], commands)
        self.assertIn(["ip", "route", "del", "198.51.100.0/24", "via", "192.0.2.254", "metric", "10"], commands)
        self.assertEqual(linux._load_applied_state(state_path), {"addresses": [], "routes": []})

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
        self.assertEqual(execute(self.store, "/ip address print"), "0    192.0.2.1/24         eth1")
        self.assertNotIn("candidate", self.store.snapshot())

    def test_cli_route_add_set_remove_lifecycle(self):
        execute(self.store, "/ip route add dst-address=198.51.100.0/24 gateway=192.0.2.254")
        execute(self.store, "/ip route set numbers=0 metric=25")
        self.assertEqual(self.store.snapshot()["candidate"]["routes"][0]["metric"], 25)
        execute(self.store, "/ip route remove numbers=0")
        self.assertEqual(self.store.snapshot()["candidate"]["routes"], [])

    def test_route_print_shows_candidate_while_active_is_kernel_state(self):
        execute(self.store, "/ip route add dst-address=198.51.100.0/24 gateway=192.0.2.254")
        output = execute(self.store, "/ip route print")
        self.assertIn("198.51.100.0/24", output)
        self.assertIn("metric=100", output)

    def test_interface_set_edits_existing_candidate_definition(self):
        config = {"system": {"hostname": "router01"}, "interfaces": [{"name": "lan0", "enabled": True, "mtu": 1500}], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        self.store.replace_candidate(config, 0)
        execute(self.store, "/interface set name=lan0 mtu=1400 enabled=false")
        updated = self.store.snapshot()["candidate"]["interfaces"][0]
        self.assertEqual(updated, {"name": "lan0", "enabled": False, "mtu": 1400})

    def test_apply_config_rejects_unknown_interface_before_mutation(self):
        config = {"system": {"hostname": "router01"}, "interfaces": [{"name": "missing0", "enabled": True, "mtu": 1500}], "addresses": [], "routes": [], "firewall": {"forward": [], "nat": []}}
        commands = []

        def fake_run(argv, input_text=None):
            commands.append(argv)
            if argv[:2] == ["nft", "-c"]:
                return ""
            if argv[:3] == ["ip", "-j", "address"]:
                return '[{"ifname":"eth0"}]'
            if argv[:4] == ["ip", "-j", "route", "show"]:
                return '[]'
            if argv[:5] == ["ip", "-j", "-details", "link", "show"]:
                return '[{"ifname":"eth0"}]'
            if argv[:3] == ["nft", "-j", "list"]:
                return '{"nftables":[]}'
            return ""

        with patch("rhos.linux.run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                apply_config(config)
        self.assertFalse(any(argv[:3] == ["nft", "-f", "-"] for argv in commands))

    def test_cli_remove_rejects_out_of_range_index(self):
        with self.assertRaisesRegex(ConfigError, "does not exist"):
            execute(self.store, "/ip address remove numbers=0")

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

    def test_confirmed_commit_and_confirmation(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}), patch("rhos.linux.apply_via_worker"):
            pending = self.store.commit_confirmed(0, 30)
            self.assertIn("pending_confirmed", pending)
            confirmed = self.store.confirm(pending["revision"])
        self.assertEqual(confirmed["config"]["addresses"][0]["address"], "192.0.2.1/24")
        self.assertNotIn("pending_confirmed", confirmed)
        self.assertEqual(len(confirmed["history"]), 1)

    def test_confirmed_commit_expiry_restores_previous_state(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}), patch("rhos.linux.apply_via_worker") as apply:
            pending = self.store.commit_confirmed(0, 30)
            with patch("rhos.config.time.time", return_value=pending["pending_confirmed"]["expires_at"] + 1):
                self.assertTrue(self.store.recover_expired_confirmed())
        restored = self.store.snapshot()
        self.assertEqual(restored["config"]["addresses"], [])
        self.assertNotIn("pending_confirmed", restored)
        self.assertEqual(apply.call_args_list[-1].args[0]["addresses"], [])

    def test_expired_confirmed_commit_recovers_after_store_restart(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}), patch("rhos.linux.apply_via_worker"):
            self.store.commit_confirmed(0, 30)
        restarted = Store(self.temp.name)
        with patch("rhos.config.time.time", return_value=10**12), patch("rhos.linux.apply_via_worker"):
            self.assertTrue(restarted.recover_expired_confirmed())
        self.assertEqual(restarted.snapshot()["config"]["addresses"], [])

    def test_candidate_changes_are_locked_during_confirmed_commit(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}), patch("rhos.linux.apply_via_worker"):
            pending = self.store.commit_confirmed(0, 30)
            with self.assertRaisesRegex(Conflict, "locked"):
                self.store.mutate(pending["revision"], "route_add", {"dst-address": "198.51.100.0/24", "gateway": "192.0.2.254"})

    def test_confirmed_commit_persistence_failure_restores_previous_network(self):
        self.store.mutate(0, "address_add", {"address": "192.0.2.1/24", "interface": "eth1"})
        with patch.dict("os.environ", {"RHOS_ALLOW_NETWORK_APPLY": "1"}), patch("rhos.linux.apply_via_worker") as apply:
            with patch.object(self.store, "_persist", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    self.store.commit_confirmed(0, 30)
        self.assertEqual(apply.call_count, 3)
        self.assertEqual(apply.call_args_list[-1].args[0]["addresses"], [])
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
