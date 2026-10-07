"""A tab replaced by a resume says so; unknown credentials stay plain unauthorized; activity is visible."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_agent_policy import AgentPolicyTests as Base


class SupersededTests(Base):
    # Inherit only the fixture; the base suite already runs in its own module.
    def runTest(self):pass

    def old_tab(self):
        self.pair();return self.browser

    def reopen(self):
        self.policy.open_binding('Operator',self.sid,binding_id=self.bid,resume=True)

    def test_old_tab_after_resume_is_superseded_before_repair(self):
        old=self.old_tab();self.reopen()
        self.code('session_superseded',lambda:self.policy.authorize(old))
        self.code('session_superseded',lambda:self.policy.authorize(old,write=True))

    def test_old_tab_secret_with_new_cookie_is_superseded(self):
        self.old_tab();old_tab=self.tab;self.reopen();self.pair()
        mixed=self.request({'X-Idea-Binding':self.bid,'Cookie':self.cookie,'X-CSRF-Token':self.csrf,'X-Idea-Tab':old_tab})
        self.code('session_superseded',lambda:self.policy.authorize(mixed))
        self.assertIs(self.policy.authorize(self.browser),self.binding)  # The new tab still works.

    def test_unknown_credentials_stay_browser_unauthorized(self):
        self.old_tab();self.reopen()
        junk=self.request({'X-Idea-Binding':self.bid,'Cookie':self.cookie.split('=')[0]+'='+'0'*64,'X-CSRF-Token':self.csrf,'X-Idea-Tab':'f'*64})
        self.code('browser_unauthorized',lambda:self.policy.authorize(junk))
        self.code('browser_unauthorized',lambda:self.policy.authorize(self.request({'X-Idea-Binding':self.bid})))

    def test_revoke_without_resume_is_not_superseded(self):
        old=self.old_tab();self.policy.revoke(self.bid)
        self.code('browser_unauthorized',lambda:self.policy.authorize(old))

    def test_no_raw_secret_is_retained(self):
        self.old_tab();secrets=(self.cookie.split('=',1)[1],self.tab,self.csrf);self.reopen()
        entry=self.policy._entries[self.bid]
        self.assertNotIn('retired',repr(entry))
        for secret in secrets:
            for pair in entry.retired:
                for digest in pair:self.assertNotIn(secret.encode(),digest)

    def test_retired_record_is_bounded(self):
        for _ in range(12):
            self.old_tab();self.reopen()
        self.assertLessEqual(len(self.policy._entries[self.bid].retired),8)

    def test_accepted_activity_is_counted_with_broker_result(self):
        self.human()
        with self.binding.lock:self.policy.activity(self.binding)
        stats=self.policy.activity_diagnostics()[self.bid]
        self.assertEqual((stats['accepted'],stats['agent_clock_moved'],stats['last_agent_clock_moved']),(1,1,True))
        self.assertIsNotNone(stats['last_seen'])
        self.now+=10**6  # Expire the agent: the broker answers False and the counter says so.
        with self.binding.lock:self.policy.activity(self.binding)
        stats=self.policy.activity_diagnostics()[self.bid]
        self.assertEqual((stats['accepted'],stats['agent_clock_moved'],stats['last_agent_clock_moved']),(2,1,False))
        self.assertNotIn(self.tab,repr(stats));self.assertNotIn(self.cookie,repr(stats))

    def test_activity_emits_a_log_line_without_secrets(self):
        self.human()
        with self.assertLogs('idea.activity',level='INFO') as logs:
            with self.binding.lock:self.policy.activity(self.binding)
        line=logs.output[0]
        self.assertIn('count=1',line);self.assertIn('agent_clock_moved=true',line)
        self.assertNotIn(self.tab,line);self.assertNotIn(self.credentials['token'],line)

for _name in [n for n in dir(Base) if n.startswith('test_') and n not in SupersededTests.__dict__]:
    setattr(SupersededTests,_name,None)  # Fixture only; the base module runs its own tests.
del Base
if __name__=='__main__':unittest.main()
