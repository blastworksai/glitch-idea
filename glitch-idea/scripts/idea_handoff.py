"""Verified planning delivery and fixed navigation handlers.

Host observations belong to Store; immutable packet codecs own source identity.
No route accepts a filesystem path, command, validator, or browser actor.
"""
import copy
import os
import stat

import idea_handoff_evidence as codec
from idea_domain import IdeaError, check_id, digest, integer, require
from idea_store import MAX_STORE_FILES, _request_ids, _request_json
from idea_steps import TrustedRoute
from idea_bridge import Response
from idea_workflow import derive_state

PUBLIC_FIELDS = frozenset(('handoff_id','source_revision','source_digest','path',
                          'sha256','prompt','source_files','design_set'))
RECEIPT_FIELDS = frozenset(('ok','code','request_id','write_state','idea_id','revision',
    'draft_version','backlog_revision','handoff_index','handoff_id','path','sha256'))
ELIGIBILITY_REFUSALS = frozenset(('not_ready','archived_revision','stale_revision',
    'stale_source','stale_backlog','workspace_unavailable'))


def _exact(value,fields):
    require(type(value) is dict and set(value) == set(fields), 'Unexpected handoff fields')


def _bounded(value,code):
    try:
        _request_json(value)
    except IdeaError as exc:
        if exc.code == 'too_large':
            raise IdeaError(code,'Complete response exceeds capacity') from exc
        raise
    return value


class HandoffProvider:
    """Trusted Service extension; project uses the caller's active Store scope."""
    def __init__(self,store):
        self.store = store

    def _current(self,state,idea,record):
        try:
            observed = self.store.handoff_observations(state,idea['idea_id'])
            codec.verify_current(record,state,idea,observed['design_set'],source_files=observed['source_files'])
        except IdeaError as exc:
            if exc.code not in ELIGIBILITY_REFUSALS:
                raise
            return dict(available=False,code=exc.code)
        return dict(available=True,code='ok')

    def _public(self,entry):
        record,link = entry['record'],entry['evidence']
        path = codec.recorded_packet_path(record,link)
        return dict(handoff_id=record['handoff_id'],source_revision=record['source_revision'],
            source_digest=record['source_digest'],path=path,sha256=link['sha256'],
            prompt=codec.render_prompt(record,path),source_files=copy.deepcopy(record['source_files']),
            design_set=copy.deepcopy(record['design_set']))

    def project(self,state,idea,context):
        entries = self.store.handoffs(state,idea['idea_id'],with_links=True)
        if not entries:
            return dict(handoff=None,handoff_status=dict(available=False,code='not_ready'))
        entry = entries[-1]
        result = dict(handoff=self._public(entry),handoff_status=self._current(state,idea,entry['record']))
        return copy.deepcopy(_bounded(result,'handoff_capacity'))

    def __call__(self,state,idea,context=None):
        projected = self.project(state,idea,context)
        if not projected['handoff_status']['available']:
            return None
        return {name:projected['handoff'][name] for name in ('handoff_id','source_revision')}

    def reconcile(self,result,context,request_id):
        """Reconstruct exactly the requested durable receipt, never newest history."""
        _request_ids(context.session_id,request_id)
        _exact(result,RECEIPT_FIELDS)
        require(result['request_id'] == request_id, 'Handoff request identity differs', 'corrupt_receipts')
        with self.store.transaction() as state:
            _,_,session = self.store._read_receipts(context.session_id)
            receipt = session['receipts'].get(request_id)
            require(receipt is not None and receipt['result'] == result,
                    'Handoff result differs from bound session receipt','corrupt_receipts')
            expected = dict(operation='handoff',payload=dict(request_id=request_id,idea_id=result['idea_id'],
                expected_revision=result['revision'],expected_draft_version=result['draft_version'],
                expected_backlog_revision=result['backlog_revision']))
            require(result['ok'] is True and result['code'] == 'ok'
                    and result['write_state'] in ('applied','no_op')
                    and receipt['payload_sha256'] == digest(_request_json(expected)),
                    'Handoff request witness differs from its canonical envelope','corrupt_receipts')
            key = result['idea_id']; check_id(key)
            require(key in state['ideas'], 'Handoff idea is missing','corrupt_store')
            entries = self.store.handoffs(state,key,with_links=True)
            ordinal = result['handoff_index']; integer(ordinal,'handoff ordinal',1,len(entries))
            entry = entries[ordinal-1]; link = entry['evidence']; record = entry['record']
            require(link == {name:result[name] for name in ('handoff_id','path','sha256')}
                    and record['source_revision'] == result['revision'],
                    'Handoff receipt does not identify its exact packet','corrupt_receipts')
            require(result['write_state'] != 'applied' or
                    (record['session_id'] == context.session_id and record['request_id'] == request_id),
                    'Applied handoff receipt differs from original publisher','corrupt_receipts')
            status = self._current(state,state['ideas'][key],record)
            delivery = dict(copy.deepcopy(result),handoff=self._public(entry),handoff_current=status['available'])
            if not status['available']:
                delivery['code'] = 'historical_handoff'
            return _bounded(delivery,'handoff_capacity')

    def publish(self,context,payload):
        _request_json(payload)
        _exact(payload,('request_id','idea_id','expected_revision','expected_draft_version','expected_backlog_revision'))
        _request_ids(context.session_id,payload['request_id']); check_id(payload['idea_id'])
        for name in ('expected_revision','expected_draft_version','expected_backlog_revision'):
            integer(payload[name],name,1 if name == 'expected_revision' else 0)
        result = self.store.publish_handoff(context.session_id,payload['request_id'],
            dict(operation='handoff',payload=copy.deepcopy(payload)),codec.build_record)
        try:
            return self.reconcile(result,context,payload['request_id'])
        except IdeaError as exc:
            # Store returned a durable witness, including receipt
            # replay/no_op. A later delivery fault cannot make it not_applied.
            identity = {name:result[name] for name in
                        ('request_id','idea_id','revision','draft_version','backlog_revision')}
            raise IdeaError(exc.code,'Handoff is saved; check its request result',
                            committed=True,**identity) from exc

    def ideas(self,context):
        with self.store.transaction() as state:
            self.store._read_receipts(context.session_id)
            require(len(state['order']) <= MAX_STORE_FILES, 'Ideas exceed Store capacity','ideas_capacity')
            rows = []
            for position,key in enumerate(state['order'],1):
                idea = state['ideas'][key]
                view = derive_state(idea); packet = self.project(state,idea,context)
                if idea['status'] == 'archived':
                    status = 'archived'
                elif packet['handoff_status']['available']:
                    status = 'ready-to-plan'
                elif packet['handoff'] is not None or any(value['status'] == 'review-needed' for value in view['steps'].values()):
                    status = 'review-needed'
                else:
                    status = 'in-progress'
                accepted = view['accepted']; capture = accepted['capture']
                title = capture['raw_text'] if capture is not None else idea['origin']['text']
                method = accepted['method']
                detail = self.store._safe(key+'.md')
                # Legacy JSON-only Ideas may not yet have a detail Markdown leaf.
                detail_path = detail.parent.resolve(strict=True)/detail.name
                rows.append(dict(idea_id=key,revision=idea['revision'],position=position,title=title[:200],
                    status=status,method=method['selection'] if method is not None else None,
                    updated=idea['revisions'][-1]['timestamp'],detail_path=str(detail_path),
                    # Where a resumed wizard opens, and how far it got (the page says "Step N of 7").
                    current_step=view['current_step'],
                    completed_steps=sum(value['status'] in ('saved','skipped','not-applicable') for value in view['steps'].values())))
            return _bounded(dict(ok=True,code='ok',backlog_revision=state['backlog_revision'],
                                 total=len(rows),ideas=rows),'ideas_capacity')


def _provider(app):
    require(isinstance(app.handoff_provider,HandoffProvider), 'Packaged handoff provider is unavailable','operation_unavailable')
    require(app.handoff_provider.store is app.store,'Provider Store differs','invalid_transaction')
    return app.handoff_provider


def handoff(binding,request,payload):
    app = binding.application
    result = _provider(app).publish(app.context,payload)
    app.context.selected_idea_id = result['idea_id']
    return result


def ideas(binding,request,payload):
    require(payload is None,'Ideas has no body')
    app = binding.application
    return _provider(app).ideas(app.context)


MAX_MARKDOWN = 2*1024*1024


def idea_markdown(binding,request,payload):
    """One idea's own detail Markdown, read-only, as text: the id is the only browser input."""
    require(payload is None,'Markdown read takes no payload')
    key = request.resource_id; check_id(key)
    app = binding.application
    with app.store.transaction() as state:
        app.store._read_receipts(app.context.session_id)
        require(key in state['ideas'],'Unknown idea','not_found')
        path = app.store._safe(key+'.md')
        try:
            descriptor = os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
        except FileNotFoundError:
            raise IdeaError('not_found','This idea has no Markdown file yet') from None
        except OSError as exc:
            raise IdeaError('corrupt_store','Idea Markdown cannot be read') from exc
        with os.fdopen(descriptor,'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode),'Unsafe idea Markdown','corrupt_store')
            raw = stream.read(MAX_MARKDOWN+1)
    require(len(raw) <= MAX_MARKDOWN,'Idea Markdown exceeds the read limit','too_large')
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        raise IdeaError('corrupt_store','Idea Markdown is not UTF-8') from None
    # Shown as text by the page, never rendered or executed.
    return Response(raw,content_type='text/plain; charset=utf-8',headers={'Content-Disposition':'inline'})


def selection(binding,request,payload):
    """Private navigation only; Bridge persists and rolls back on callback failure."""
    _request_json(payload); _exact(payload,('idea_id',))
    app = binding.application; key = payload['idea_id']
    if key is not None: check_id(key)
    with app.store.transaction() as state:
        app.store._read_receipts(app.context.session_id)
        require(key is None or key in state['ideas'],'Unknown selected idea','not_found')
        idea = state['ideas'][key] if key is not None else None
        result = dict(ok=True,code='ok',session_id=app.context.session_id,idea_id=key,
            revision=idea['revision'] if idea is not None else 0,
            draft_version=idea.get('workflow',{}).get('draft_version',0) if idea is not None else 0,
            backlog_revision=state['backlog_revision'])
    app.context.selected_idea_id = key
    return result


# Ideas/selection become Owner routes only after J11-4b's exact registry specs.
ROUTES = (TrustedRoute('handoff',handoff),)
