import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import al_restore as restore


class ExpressionExportTest(unittest.TestCase):
    def test_plan_before_composition(self):
        base = Image.new("RGBA", (5, 5), "white")
        default = Image.new("RGBA", (2, 2), "black")
        first = Image.new("RGBA", (2, 2), "red")
        second = first.copy()
        second.putpixel((0, 0), (255, 0, 0, 128))
        faces = [first, default.copy(), first.copy(), second]
        variants = restore.expression_faces(faces, default)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for filename in ("ship_exp.png", "ship_exp_3.png", "ship_exp_notes.png"):
                base.save(root / filename)
            with patch.object(restore, "paste_face", wraps=restore.paste_face) as draw:
                count = restore.save_expressions(root, "ship", base, (1, 2, 2, 2), (0, 0), variants)
            self.assertEqual(count, 2)
            self.assertEqual([call.args[2] for call in draw.call_args_list], [first, second])
            for i, face in enumerate((first, second), 1):
                expected = base.copy()
                expected.alpha_composite(face, (1, 2))
                with Image.open(root / f"ship_exp_{i}.png") as exported:
                    self.assertEqual(exported.tobytes(), expected.tobytes())
            self.assertFalse((root / "ship_exp.png").exists())
            self.assertFalse((root / "ship_exp_3.png").exists())
            restore.save_expressions(root, "ship", base, None, (0, 0), ())
            self.assertEqual([p.name for p in root.iterdir()], ["ship_exp_notes.png"])
