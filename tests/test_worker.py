import importlib.machinery
import importlib.util
import struct
import unittest
from pathlib import Path
from unittest.mock import patch


WORKER = Path(__file__).parents[1] / "packaging" / "rhos-network-worker"
loader = importlib.machinery.SourceFileLoader("rhos_network_worker", str(WORKER))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_rejects_unknown_operation_before_network_apply(self):
        class FakeConnection:
            def getsockopt(self, *_):
                return struct.pack("3i", 1, 0, 0)

            def recv(self, _):
                return b'{"operation":"exec","config":{}}\n'

        with patch.object(worker.grp, "getgrnam", return_value=type("Group", (), {"gr_gid": 0})()):
            with self.assertRaises(ValueError):
                worker.process_connection(FakeConnection())

    def test_apply_client_rejects_worker_errors(self):
        import json
        from rhos.linux import apply_via_worker

        class FakeClient:
            def __init__(self, *_):
                self.response = bytearray(json.dumps({"ok": False, "error": "permission denied"}).encode() + b"\n")

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def settimeout(self, _):
                pass

            def connect(self, _):
                pass

            def sendall(self, _):
                pass

            def shutdown(self, _):
                pass

            def recv(self, size):
                result = self.response[:size]
                del self.response[:size]
                return bytes(result)

        with patch("rhos.linux.socket.socket", FakeClient):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                apply_via_worker({"test": "invalid"}, "/unused")


if __name__ == "__main__":
    unittest.main()
