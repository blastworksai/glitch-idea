"""The default runtime root falls back to the OS runtime dir when the home chain is shared."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea


class RuntimeRootFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        # The temp parent itself must pass the ancestor rule, so build everything below one private folder.
        self.base=Path(self.temp.name).resolve();os.chmod(self.base,0o700)
        self.package=self.base/'package/glitch-idea';self.package.mkdir(parents=True)
        fake=self.package/'scripts/idea.py';fake.parent.mkdir();fake.write_text('fixture')
        patcher=patch.object(idea,'__file__',str(fake));patcher.start();self.addCleanup(patcher.stop)
        (self.package/'config.json').write_text(json.dumps(dict(store_path=str(self.base/'ideas'),
                                                                plan_validator_argv=None,validator_timeout_seconds=30)))
        self.home=self.base/'home';self.home.mkdir()
        self.xdg=self.base/'xdg';self.xdg.mkdir();os.chmod(self.xdg,0o700)

    def resolve(self,*argv,xdg=True):
        env={'XDG_RUNTIME_DIR':str(self.xdg)} if xdg else {}
        with patch.object(idea,'_private_home',return_value=self.home),patch.dict(os.environ,env,clear=False):
            if not xdg: os.environ.pop('XDG_RUNTIME_DIR',None)
            args=idea.parser().parse_args(['session-open',*argv])
            return idea.launcher_configuration(args)[1],args.runtime_root_fallback

    def share_home(self):
        os.chmod(self.home,0o2770)

    def test_shared_home_falls_back_to_xdg_with_marker(self):
        self.share_home()
        root,marker=self.resolve()
        self.assertEqual(root,self.xdg/'glitch-idea')
        self.assertEqual(marker,{'from':str(self.home/'.local/state/glitch-idea'),'to':str(self.xdg/'glitch-idea'),
                                 'reason':'home_not_private'})

    def test_private_home_keeps_home_default_without_marker(self):
        os.chmod(self.home,0o700)
        root,marker=self.resolve()
        self.assertEqual(root,self.home/'.local/state/glitch-idea');self.assertIsNone(marker)

    def test_shared_home_without_usable_xdg_keeps_home_default(self):
        self.share_home()
        root,marker=self.resolve(xdg=False)
        self.assertEqual(root,self.home/'.local/state/glitch-idea');self.assertIsNone(marker)
        os.chmod(self.xdg,0o750)
        root,marker=self.resolve()
        self.assertEqual(root,self.home/'.local/state/glitch-idea');self.assertIsNone(marker)

    def test_explicit_runtime_root_is_never_redirected(self):
        self.share_home()
        explicit=self.base/'explicit'
        root,marker=self.resolve('--runtime-root',str(explicit))
        self.assertEqual(root,explicit);self.assertIsNone(marker)

    def test_configured_runtime_root_is_never_redirected(self):
        self.share_home()
        configured=self.base/'configured'
        (self.package/'config.json').write_text(json.dumps(dict(store_path=str(self.base/'ideas'),plan_validator_argv=None,
                                                                validator_timeout_seconds=30,runtime_root=str(configured))))
        root,marker=self.resolve()
        self.assertEqual(root,configured);self.assertIsNone(marker)


if __name__=='__main__':
    unittest.main()
