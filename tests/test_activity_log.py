"""The owned service writes each accepted keep-alive to an owner-private activity.log."""
import os
from pathlib import Path
import stat
import sys
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


if __name__=='__main__':unittest.main()
