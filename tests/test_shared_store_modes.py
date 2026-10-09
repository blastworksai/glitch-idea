"""A store whose root is setgid and group-writable is shared: group-writable files, 2770 dirs, 0440 seals.

Every other root stays owner-only. The second-account part is attended proof, not emulated here.
"""
import os
from pathlib import Path
import stat
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_assets
import idea_asset_evidence as codec
import idea_platform as platform_
import idea_transactions as tx


def mode(path):
    return stat.S_IMODE(Path(path).stat().st_mode)


@unittest.skipUnless(os.name == 'posix' and hasattr(os,'fchmod'),'POSIX file modes are required')
class SharedStoreModeTests(unittest.TestCase):
    setUp = test_assets.AssetsTests.setUp
    stop = test_assets.AssetsTests.stop
    request = test_assets.AssetsTests.request
    metadata = test_assets.AssetsTests.metadata
    start = test_assets.AssetsTests.start
    put = test_assets.AssetsTests.put

    def upload(self,root_mode):
        os.chmod(self.root,root_mode)
        # The seed fixture predates the root's mode; only what the store writes afterwards is in scope.
        self.before = set(self.root.rglob('*'))
        previous = os.umask(0o022)
        try:
            intent = self.start()
            self.assertEqual(self.put(intent)[0],200)
        finally:
            os.umask(previous)
        return intent

    def test_journal_folders_are_made_2770_in_a_shared_root(self):
        seen = []
        real = platform_.share_directory
        def spy(path,shared):
            real(path,shared)
            seen.append((Path(path).name,shared,mode(path)))
        os.chmod(self.root,0o2770)
        with patch.object(platform_,'share_directory',spy):
            self.assertEqual(self.put(self.start())[0],200)
        journals = [entry for entry in seen if entry[0].startswith('txn_')]
        self.assertTrue(journals)
        for _,shared,observed in journals:
            self.assertTrue(shared); self.assertEqual(observed,0o2770)

    def test_shared_root_makes_group_writable_files_and_2770_dirs(self):
        intent = self.upload(0o2770)
        self.assertEqual(mode(self.root),0o2770)
        new = [p for p in self.root.rglob('*') if p not in self.before]
        blob = self.root/codec.blob_path(intent['asset_id'])
        self.assertEqual(mode(blob),0o440)
        stages = list((self.root/'assets/staging').iterdir())
        self.assertTrue(stages)
        for stage in stages:
            self.assertEqual(mode(stage),0o440)
        for directory in (p for p in new if p.is_dir()):
            self.assertEqual(mode(directory),0o2770,str(directory))
        for path in (p for p in new if p.is_file()):
            self.assertEqual(mode(path) & 0o007,0,str(path))
            if path != blob and path.parent != self.root/'assets/staging':
                self.assertEqual(mode(path),0o660,str(path))
        self.assertEqual(mode(self.root/'.lock'),0o660)

    def test_plain_owner_only_root_stays_owner_only(self):
        intent = self.upload(0o700)
        self.assertFalse(platform_.is_shared_root(self.root))
        blob = self.root/codec.blob_path(intent['asset_id'])
        self.assertEqual(mode(blob),0o400)
        for stage in (self.root/'assets/staging').iterdir():
            self.assertEqual(mode(stage),0o400)
        for directory in (blob.parent,*blob.parent.parents,self.root/'assets/staging'):
            if directory == self.root: break
            self.assertEqual(mode(directory) & 0o077,0,str(directory))

    def test_setgid_without_group_write_is_not_shared(self):
        os.chmod(self.root,0o2750)
        self.assertFalse(platform_.is_shared_root(self.root))


@unittest.skipUnless(os.name == 'posix' and hasattr(os,'fchmod'),'POSIX file modes are required')
class SharedDirectoryCreationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()/'store'
        self.root.mkdir()
        (self.root/'.lock').write_bytes(b'initialized\n')
        os.chmod(self.root,0o2770)

    def test_new_directories_are_never_other_reachable_even_for_an_instant(self):
        seen, real = [], os.mkdir
        def spy(path,*args,**kwargs):
            real(path,*args,**kwargs)
            seen.append((Path(path).name,mode(path)))
        previous = os.umask(0o022)
        try:
            with patch.object(platform_.os,'mkdir',spy):
                platform_.atomic_write(self.root/'a'/'b'/'file',b'x').raise_for_error()
        finally:
            os.umask(previous)
        self.assertEqual([name for name,_ in seen],['a','b'])
        for name,observed in seen:
            self.assertEqual(observed & 0o007,0,name)
        self.assertEqual(mode(self.root/'a'),0o2770)
        self.assertEqual(mode(self.root/'a'/'b'),0o2770)

    def test_a_symlink_planted_at_the_path_is_left_alone(self):
        private = Path(self.temp.name).resolve()/'private'
        private.mkdir(mode=0o700)
        link = self.root/'swapped'
        os.symlink(private,link)
        platform_.share_directory(link,True)
        self.assertEqual(mode(private),0o700)

    def test_a_directory_we_do_not_own_is_skipped_and_a_missing_one_is_not_an_error(self):
        platform_.share_directory(self.root/'missing',True)
        folder = self.root/'folder'
        folder.mkdir(mode=0o750)
        os.chmod(folder,0o750)
        with patch.object(platform_.os,'geteuid',return_value=os.geteuid()+1):
            platform_.share_directory(folder,True)
        self.assertEqual(mode(folder),0o750)


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(os.name == 'posix' and hasattr(os,'fchmod'),'POSIX file modes are required')
class JournalFolderModeTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()/'store'
        self.root.mkdir()
        (self.root/'.lock').write_bytes(b'initialized\n')

    def publish(self,umask=0o022,raw=b'index\n',before=None):
        if (self.root/'IDEAS.md').exists():
            (self.root/'IDEAS.md').unlink()
        previous = os.umask(umask)
        try:
            result = tx.publish(self.root,{'IDEAS.md':raw},{'IDEAS.md':before})
        finally:
            os.umask(previous)
        result.raise_for_error()

    def test_a_new_journal_folder_is_never_other_reachable_under_umask_022(self):
        for root_mode,expected in ((0o700,0o700),(0o2770,0o2770)):
            with self.subTest(root_mode=oct(root_mode)):
                os.chmod(self.root,root_mode)
                journal = self.root/tx.JOURNAL
                if journal.exists():
                    import shutil
                    shutil.rmtree(journal)
                self.publish()
                self.assertEqual(mode(journal) & 0o007,0)
                self.assertEqual(mode(journal),expected)

    def test_a_folder_that_exists_at_the_wrong_mode_is_healed_on_the_next_publish(self):
        for root_mode,expected in ((0o700,0o700),(0o2770,0o2770)):
            with self.subTest(root_mode=oct(root_mode)):
                os.chmod(self.root,root_mode)
                journal = self.root/tx.JOURNAL
                journal.mkdir(exist_ok=True)
                os.chmod(journal,0o755)
                self.publish()
                self.assertEqual(mode(journal),expected)
