import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from contextlib import ExitStack
from unittest.mock import patch

from PIL import Image

from painting_restore import rendering as restore
from painting_restore.tasks import Job, execute


class ExpressionExportTest(unittest.TestCase):
    def test_plan_before_composition(self):
        base = Image.new("RGBA", (5, 5), "white")
        default = Image.new("RGBA", (2, 2), "black")
        first = Image.new("RGBA", (2, 2), "red")
        second = first.copy()
        second.putpixel((0, 0), (255, 0, 0, 128))
        faces = [first, default.copy(), first.copy(), second]
        def compose(prefab, painting, *, probe, **kwargs):
            probe["hole"] = True
            return base.copy(), []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "out" / "ship"
            root.mkdir(parents=True)
            job = Job(Path(folder) / "assets", root.parent, ("ship",), align=False)
            for filename in ("ship_exp.png", "ship_exp_3.png", "ship_exp_notes.png"):
                base.save(root / filename)
            with ExitStack() as mocks:
                prefab = SimpleNamespace(layers=["body"], face_rect=(1, 2, 2, 2))
                mocks.enter_context(patch.object(restore, "read_prefab", return_value=prefab))
                mocks.enter_context(patch.object(restore, "composite", side_effect=compose))
                mocks.enter_context(patch.object(restore, "load_face_pack", return_value=(dict(enumerate(faces)), {})))
                mocks.enter_context(patch.object(restore, "pick_default_face", return_value=default))
                draw = mocks.enter_context(patch.object(restore, "paste_face", wraps=restore.paste_face))
                self.assertEqual(execute(job)[0].status, "成功")
                self.assertEqual([call.args[2] for call in draw.call_args_list], [default, first, second])
                for i, face in enumerate((first, second), 1):
                    expected = base.copy()
                    expected.alpha_composite(face, (1, 2))
                    with Image.open(root / f"ship_exp_{i}.png") as exported:
                        self.assertEqual(exported.tobytes(), expected.tobytes())
                self.assertFalse((root / "ship_exp.png").exists())
                self.assertFalse((root / "ship_exp_3.png").exists())
                self.assertEqual(execute(replace(job, expressions=False))[0].status, "成功")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["ship.png", "ship_exp_notes.png"])
