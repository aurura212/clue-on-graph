from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sp_memory.paths import Workspace, WorkspaceBoundaryError


class PathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sp = root / "self-play"
        self.data = root / "data"
        self.alias = root / "cope_alias"
        self.pog = root / "PoG"
        for path in (self.sp, self.data, self.alias, self.pog):
            path.mkdir()
        self.ws = Workspace.for_tests(self.sp, self.data, self.alias, self.pog)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_legal_write(self) -> None:
        path = self.ws.safe_write_text(self.sp / "artifacts" / "ok.txt", "ok\n")
        self.assertTrue(path.exists())
        self.assertTrue(str(path).startswith(str(self.sp.resolve())))

    def test_reject_data(self) -> None:
        with self.assertRaises(WorkspaceBoundaryError):
            self.ws.assert_writable(self.data / "nope.txt")

    def test_reject_parent_dotdot(self) -> None:
        with self.assertRaises(WorkspaceBoundaryError):
            self.ws.assert_writable(self.sp / ".." / "data" / "nope.txt")

    def test_reject_absolute_outside(self) -> None:
        with self.assertRaises(WorkspaceBoundaryError):
            self.ws.assert_writable(Path("/tmp") / "sp0_outside.txt")

    def test_reject_symlink_escape(self) -> None:
        target = self.data / "secret.txt"
        target.write_text("secret")
        link_dir = self.sp / "artifacts"
        link_dir.mkdir()
        link = link_dir / "escape"
        os.symlink(self.data, link)
        with self.assertRaises(WorkspaceBoundaryError):
            self.ws.assert_writable(link / "secret.txt")

    def test_from_this_package_is_self_play(self) -> None:
        ws = Workspace.from_this_package()
        self.assertEqual(ws.self_play_root.name, "self-play")
        self.assertTrue((ws.self_play_root / "src" / "sp_memory" / "paths.py").exists())


if __name__ == "__main__":
    unittest.main()
