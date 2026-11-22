from pathlib import Path
import tempfile
import unittest

from painting_restore.dynamic_assets import catalog
from painting_restore.tasks import choose, locate, publish


class BackgroundCatalogTest(unittest.TestCase):
    def test_background_only_install_and_login_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "files" / "AssetBundles"
            for relative in ["bg/intro", "loadingbg/loginbg_1031", "loadingbg/bg_1",
                             "loadingbg_hx/bg_1", "loadingbg/bg_1.manifest", "newloading/loading_01"]:
                f = root / relative
                f.parent.mkdir(parents=True, exist_ok=True)
                f.touch()
            self.assertEqual(locate(tmp), root.resolve())
            self.assertEqual(catalog(root, "startup"), ("loadingbg__loginbg_1031",))
            self.assertEqual(set(catalog(root, "background")), {
                "bg__intro", "loadingbg__bg_1", "loadingbg_hx__bg_1", "newloading__loading_01"})
            self.assertEqual(choose(root, ["bg__intro"], asset_kind="background"), ("bg__intro",))

    def test_flat_publish_refreshes_only_the_selected_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            target = parent / "out"
            stage = parent / "new"
            target.mkdir()
            stage.mkdir()
            for name in ("bg__intro.png", "bg__intro~2.png", "bg__intro~12.png", "bg__intro_2.png", "bg__intro~~2.png"):
                (target / name).write_bytes(b"original")
            (stage / "bg__intro.png").write_bytes(b"updated")
            publish(stage, target, "bg__intro", flat_images=True)
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["bg__intro.png", "bg__intro_2.png", "bg__intro~~2.png"])
            self.assertEqual((target / "bg__intro.png").read_bytes(), b"updated")
