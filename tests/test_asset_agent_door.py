"""POST /agent/v1/asset: agent-only PNG/ZIP door onto the one asset-store write path. Operator."""
import hashlib
import http.client
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]/'glitch-idea/scripts'
sys.path.insert(0,str(SCRIPTS))
import idea_assets as assets
import idea_bridge as bridge
import idea_asset_evidence as codec
import idea_store as storage
from idea_service import Service, TrustedContext
from idea_steps import load_registry
from test_bridge import FixturePolicy
from test_asset_store import KEY, seed

PNG = b'\x89PNG\r\n\x1a\nfixture'
ZIP = b'PK\x03\x04fixture-bundle'
TOKEN = 'Bearer ' + 'a' * 64


class AgentPolicy(FixturePolicy):
    def authorize_agent(self, request):
        bridge.check(request.header('Authorization') == TOKEN, 'agent_unauthorized', 401)
        return SimpleNamespace(binding=self.binding, session_id=self.binding.application.context.session_id)

    def recheck_agent(self, context):
        return self.binding


class AgentAssetDoorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'Store'; self.sid = seed(self.root)
        self.store = storage.Store(self.root,observer='Operator')
        self.app = Service(self.store,{},TrustedContext('Operator',self.sid),handlers=load_registry()[0])
        self.binding = bridge.ApplicationBinding(self.app); self.policy = AgentPolicy(self.binding)
        self.server = bridge.BridgeServer(self.policy,routes={r.route_id:r for r in assets.ROUTES},
                                          agent=lambda *args:{'ok':True})
        self.worker = threading.Thread(target=self.server.serve_forever,daemon=True); self.worker.start()
        self.addCleanup(self.stop)
        self.count = 0

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def call(self,data,mime='image/png',*,auth=True,browser=False,name='Proto.png',session=None,
             revision='7',request_id=None,idea=KEY,length=None):
        self.count += 1
        headers = {'Host':self.server.host,'Content-Type':mime,'X-Idea-Session':session or self.sid,
                   'X-Idea-Id':idea,'X-Idea-Revision':revision,'X-Idea-Asset-Name':name,
                   'X-Idea-Request-Id':request_id or 'door-'+str(self.count)}
        if length is not None: headers['Content-Length'] = str(length)
        if auth: headers['Authorization'] = TOKEN
        if browser:
            headers.update(Cookie='browser='+self.policy.cookie,Origin=self.server.origin)
            headers['X-CSRF-Token'] = self.policy.csrf
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            connection.request('POST','/agent/v1/asset',data,headers)
            response = connection.getresponse(); raw = response.read()
            return response.status,json.loads(raw)
        finally: connection.close()

    def browser_get(self,path):
        connection = http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            connection.request('GET',path,headers={'Host':self.server.host,'Cookie':'browser='+self.policy.cookie})
            response = connection.getresponse(); return response.status,response.read()
        finally: connection.close()

    def held(self):
        with self.store.transaction() as state:
            return [r for r in self.store.asset_records(state,KEY) if r['kind']=='asset']

    def test_agent_png_and_zip_stored_and_readable_through_existing_read_path(self):
        for data,mime,name in ((PNG,'image/png','a.png'),(ZIP,'application/zip','b.zip')):
            status,result = self.call(data,mime,name=name); self.assertEqual(status,200,result)
            self.assertEqual(result['asset_ids'],[result['asset_id']])
            self.assertRegex(result['asset_id'],r'asset_[0-9a-f]{32}')
            status,raw = self.browser_get('/api/v1/attachments/'+result['asset_id'])
            self.assertEqual(status,200)
            record = next(r for r in self.held() if r['asset_id']==result['asset_id'])
            self.assertEqual((record['validated_type'],record['sha256']),(mime,hashlib.sha256(data).hexdigest()))

    def test_store_modes_staging_and_sealing_preserved(self):
        status,result = self.call(PNG); self.assertEqual(status,200,result)
        blob = self.root/codec.blob_path(result['asset_id'])
        self.assertEqual(stat.S_IMODE(blob.stat().st_mode),0o400)
        self.assertEqual(stat.S_IMODE((self.root/'assets').stat().st_mode)&0o077,0)

    def test_auth_agent_only(self):
        status,error = self.call(PNG,auth=False); self.assertEqual((status,error['code']),(401,'agent_unauthorized'))
        status,error = self.call(PNG,auth=False,browser=True)
        self.assertEqual((status,error['code']),(403,'agent_browser_headers_refused'))
        status,error = self.call(PNG,browser=True)
        self.assertEqual((status,error['code']),(403,'agent_browser_headers_refused'))
        status,error = self.call(PNG,session='session_'+'9'*32)
        self.assertEqual((status,error['code']),(403,'wrong_session'))
        self.assertEqual(self.held(),[])

    def test_type_refusals_declared_and_bytes(self):
        for data,mime in ((ZIP,'image/png'),(PNG,'application/zip'),(b'<html></html>','text/html'),
                          (b'%PDF-1.7','application/pdf'),(PNG,'image/jpeg')):
            status,error = self.call(data,mime)
            self.assertEqual((status,error['code']),(400,'unsupported_asset_type'),(mime,data))
        self.assertEqual(self.held(),[])

    def test_size_limits_reuse_existing_constants(self):
        status,error = self.call(PNG,length=codec.MAX_FILE+1)
        self.assertEqual((status,error['code']),(413,'too_large'))

    def test_member_count_uses_codec_limit(self):
        for index in range(codec.MAX_MEMBERS):
            status,result = self.call(PNG,name='p'+str(index)+'.png'); self.assertEqual(status,200,result)
        status,error = self.call(PNG,name='extra.png')
        self.assertEqual((status,error['code']),(413,'too_large'))

    def test_replayed_request_returns_same_asset(self):
        first = self.call(PNG,request_id='same-1'); second = self.call(PNG,request_id='same-1')
        self.assertEqual(first[0],200); self.assertEqual(first[1]['asset_id'],second[1]['asset_id'])

    def test_prototype_set_zip_and_png_accepts_through_visualize(self):
        zip_id = self.call(ZIP,'application/zip',name='proto.zip')[1]['asset_id']
        png_id = self.call(PNG,name='proto.png')[1]['asset_id']
        with self.store.transaction() as state: idea = state['ideas'][KEY]
        payload = dict(request_id='proto-set',idea_id=KEY,expected_revision=idea['revision'],
            expected_draft_version=idea['workflow']['draft_version'],step='visualize',proposal_id=None,
            expected_backlog_revision=None,fields=dict(disposition='accepted_set',reason=None,design_set_id=None,
            brief_evidence_id=None,source='prototype'),design_set_id=None,asset_ids=[zip_id,png_id])
        result = assets.visual_set_accept(self.binding,None,payload)
        accepted = self.app.state()['accepted']['visualize']
        self.assertEqual(accepted['source'],'prototype')
        self.assertEqual(accepted['design_set_id'],result['design_set_id'])


if __name__ == '__main__':
    unittest.main()
