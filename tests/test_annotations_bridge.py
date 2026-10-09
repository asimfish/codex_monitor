"""Read, edit and concurrently save fabricated annotations through Python and Swift."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codex_monitor.annotations import Annotations


def main(binary):
    with tempfile.TemporaryDirectory(prefix="codex-annotation-bridge-") as directory:
        root = Path(directory)
        store = Annotations(root)
        auth = root / "auth.json"
        auth.write_bytes(b'{"fake": "credentials must stay untouched"}')
        original = auth.read_bytes()
        store.save("profile:shared", ["Python 添加"], True)
        read = subprocess.check_output([binary, "read", directory, "profile:shared"], text=True)
        assert json.loads(read) == {"tags": ["Python 添加"], "unavailable": True}
        subprocess.run([binary, "put", directory, "profile:shared", "Swift 修改", "0"], check=True)
        assert store.read()["profile:shared"] == {"tags": ["Swift 修改"], "unavailable": False}
        store.save("profile:shared", [], False)
        read = subprocess.check_output([binary, "read", directory, "profile:shared"], text=True)
        assert json.loads(read)["tags"] == []
        writer = subprocess.Popen([binary, "batch", directory])
        try:
            for index in range(50):
                store.save("profile:python-%d" % index, ["Python 备注 %d" % index], False)
            assert writer.wait(timeout=15) == 0
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.wait(timeout=5)
        values = store.read()
        assert len(values) == 101, "Concurrent writers lost another account's notes"
        assert auth.read_bytes() == original
        print("Swift/Python annotation bridge passed: shared add/edit/delete, concurrent saves, credentials unchanged")


if __name__ == "__main__":
    main(sys.argv[1])
