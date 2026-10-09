"""The owned service writes each accepted keep-alive to an owner-private activity.log."""
import logging
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_launch


@unittest.skipUnless(os.name=='posix','Native owner ACLs are not qualified on this host')
class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.h=test_launch.LaunchTests('test_actual_source_child_startup_reuse_and_owned_stop')
        self.h.setUp(); self.addCleanup(self.h.cleanup)

    def test_keepalive_lands_in_private_log(self):
        opened=self.h.opened();cookie,csrf=self.h.pair(opened)
        status,body,_=self.h.request(opened,'activity',{},cookie=cookie,csrf=csrf)
        self.assertEqual(status,200,body)
        log=self.h.root/'activity.log'
        self.assertTrue(log.exists(),'activity.log missing')
        self.assertEqual(stat.S_IMODE(log.stat().st_mode),0o600)
        lines=log.read_text().splitlines()
        self.assertEqual(len([l for l in lines if 'activity binding=' in l]),1,lines)
        self.assertRegex(lines[0],r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ activity binding=')


@unittest.skipUnless(os.name=='posix','Native owner ACLs are not qualified on this host')
class ActivityLogBoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree,self.tmp,True)

    @staticmethod
    def record(msg):
        return logging.LogRecord('idea.activity',logging.INFO,__file__,0,msg,None,None)

    def test_log_is_truncated_past_limit_and_keeps_logging(self):
        limit=test_launch.launch._ACTIVITY_LIMIT
        handler=test_launch.launch._ActivityHandler(self.tmp/'activity.log')
        self.addCleanup(handler.close)
        log=self.tmp/'activity.log'
        handler.emit(self.record('x'*(limit+1024)))   # one write pushes the file past the limit
        self.assertGreater(log.stat().st_size,limit)
        handler.emit(self.record('after-crossing'))   # the next write sees it full and cuts it back
        size=log.stat().st_size
        self.assertLess(size,limit,size)
        text=log.read_text()
        self.assertIn('after-crossing',text)
        self.assertNotIn('xxxx',text)
        handler.emit(self.record('still-logging'))
        self.assertIn('still-logging',log.read_text())
        self.assertEqual(stat.S_IMODE(log.stat().st_mode),0o600)

    def test_symlinked_log_is_refused(self):
        target=self.tmp/'elsewhere.txt'; target.write_bytes(b'untouched\n')
        os.symlink(target,self.tmp/'activity.log')
        with self.assertRaises(OSError):
            test_launch.launch._ActivityHandler(self.tmp/'activity.log')
        self.assertEqual(target.read_bytes(),b'untouched\n')


if __name__=='__main__':unittest.main()
