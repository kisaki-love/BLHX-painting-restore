from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from painting_restore.tasks import Job, execute, publish


class Transactions(unittest.TestCase):
    def test_cancel_does_not_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/"out"
            old=out/"ship"
            old.mkdir(parents=True)
            (old/"ship.png").write_bytes(b"previous")
            cancel = []
            def work(job,name,stage,staging):
                (staging/"ship.png").write_bytes(b"incomplete")
                cancel.append(True)
                stage("保存表情 1")
            job=Job(Path(folder)/"resources",out,("ship","next"))
            with patch("painting_restore.tasks.process", side_effect=work):
                results=execute(job,stopped=lambda: bool(cancel))
            self.assertEqual([r.status for r in results],["取消","取消"])
            self.assertEqual((old/"ship.png").read_bytes(),b"previous")
            self.assertEqual(list(out.iterdir()),[old])

    def test_rollback_mid_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            old=root/"ship"
            new=root/"new"
            old.mkdir()
            new.mkdir()
            for name in ["ship.png","ship_exp_1.png"]:
                (old/name).write_bytes(b"old")
                (new/name).write_bytes(b"new")
            original=Path.rename
            def fail(path,target):
                if path==new/"ship_exp_1.png":
                    raise PermissionError("locked")
                return original(path,target)
            with patch.object(Path,"rename",fail), self.assertRaises(PermissionError):
                publish(new,old,"ship")
            self.assertEqual([p.read_bytes() for p in old.iterdir()],[b"old",b"old"])

    def test_unwritable_destination_and_empty_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            dest=Path(folder)/"regular-file"
            dest.touch()
            job=Job(Path(folder)/"resources",dest,("a","b"))
            self.assertEqual([r.status for r in execute(job)],["失败","失败"])
            with self.assertRaises(ValueError):
                execute(Job(job.root,dest,()))
