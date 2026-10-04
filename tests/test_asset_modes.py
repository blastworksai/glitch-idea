"""Uploaded attachments are owner-only, whatever the process umask allows."""
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_assets
import idea_asset_evidence as codec
import idea_assets as assets


@unittest.skipUnless(os.name == 'posix' and hasattr(os,'fchmod'),'POSIX file modes are required')
class AssetModeTests(unittest.TestCase):
    setUp = test_assets.AssetsTests.setUp
    stop = test_assets.AssetsTests.stop
    request = test_assets.AssetsTests.request
    metadata = test_assets.AssetsTests.metadata
    start = test_assets.AssetsTests.start
    put = test_assets.AssetsTests.put

    def test_stage_blob_and_directories_are_owner_only(self):
        previous = os.umask(0o002)
        try:
            intent = self.start()
            self.assertEqual(self.put(intent)[0],200)
        finally:
            os.umask(previous)
        blob = self.root/codec.blob_path(intent['asset_id'])
        stages = list((self.root/'assets/staging').iterdir())
        self.assertEqual(len(stages),1)
        self.assertEqual(stat.S_IMODE(stages[0].stat().st_mode),0o400)
        self.assertEqual(stat.S_IMODE(blob.stat().st_mode),0o400)
        for directory in (blob.parent,*blob.parent.parents):
            if directory == self.root: break
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode) & 0o077,0,str(directory))
        self.assertEqual(stat.S_IMODE((self.root/'assets/staging').stat().st_mode) & 0o077,0)

    def test_interrupted_upload_stage_is_owner_only(self):
        previous = os.umask(0o002)
        try:
            intent = self.start()
            with patch.object(assets.os,'link',side_effect=OSError('interrupted')):
                self.assertEqual(self.put(intent)[0],500)
        finally:
            os.umask(previous)
        stage = next((self.root/'assets/staging').iterdir())
        self.assertEqual(stat.S_IMODE(stage.stat().st_mode),0o400)


if __name__ == '__main__':
    unittest.main()
