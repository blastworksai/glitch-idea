"""Migration step: workflow version 2 (glitch-idea v0.2) -> version 3.

step(raw_detail, history, *, actor, timestamp) -> (new_detail, new_files) is the
chain's contract (idea_chain.py). The v2 record is first checked by the frozen v0.2
validators (idea_workflow_v2); anything they reject is refused, so the idea is held
and left exactly as it was.

Mapping (migration design, section 2):
- capture and priorities keep their accepted fields and receipts byte-for-byte;
- Shape becomes the Exploration draft (same keys), never accepted;
- Method selection, reason and memory stay a Method draft; its investment and experiment
  join the Exploration draft; neither is accepted;
- Discovery stays empty: v2 holds nothing shaped like it;
- a not-applicable Visualize disposition is dropped (the rest stays a draft);
- the assessment (not the position) becomes an Assess draft;
- Review is dropped; its handoff evidence stays linked in extensions as history;
- drafts for capture, priorities and assess carry over; legacy shape, ratings and
  assessments are domain records and do not change.
The notes are carried byte-for-byte; the generated summary is rewritten by the v3 encoder.
One new history revision (action 'migrated-v2-v3') is returned; the old ones are untouched.
"""
import copy

import idea_markdown as md
import idea_workflow as wf
import idea_workflow_v2 as v2
from idea_domain import IdeaError, digest

ACTION = 'migrated-v2-v3'
CODE = 'unsupported_idea_version'
_HISTORY = 'history/{}/r{}.md'


def _refuse(message):
    return IdeaError(CODE, message)


def _overlay(workflow, step):
    """The step's accepted fields overlaid by its draft (the draft is the newer edit), or None."""
    merged = {}
    fields = workflow['steps'][step]['fields']
    if fields is not None:
        merged.update(copy.deepcopy(fields))
    merged.update(copy.deepcopy(workflow['drafts'].get(step) or {}))
    return merged or None


def _pick(source, keys):
    return {k: source[k] for k in keys if source is not None and k in source}


def convert_workflow(old):
    """Return the v3 workflow for a v2 workflow that already passed the frozen validators."""
    steps = {s: {'fields': None, 'acceptance': None, 'invalidated_by': []} for s in wf.STEP_ORDER}
    for step in ('capture', 'priorities'):
        steps[step] = copy.deepcopy(old['steps'][step])
    drafts = {}
    for step in ('capture', 'priorities'):
        if step in old['drafts']:
            drafts[step] = copy.deepcopy(old['drafts'][step])

    shape, method = _overlay(old, 'shape'), _overlay(old, 'method')
    exploration = {**_pick(shape, ('outcome', 'scope', 'scope_reason', 'alternatives', 'assumptions', 'next_slice', 'learning')),
                   **_pick(method, ('investment', 'experiment'))}
    if exploration:
        drafts['exploration'] = exploration
    method_draft = _pick(method, ('selection', 'reason', 'memory'))
    if method_draft:
        drafts['method'] = method_draft

    visualize = _overlay(old, 'visualize')
    if visualize is not None:
        if visualize.get('disposition') == 'not-applicable':
            visualize = {k: v for k, v in visualize.items() if k != 'disposition' and v is not None}
        if visualize:
            drafts['visualize'] = visualize

    assess = _pick(_overlay(old, 'assess'), ('assessment',))
    if assess:
        drafts['assess'] = assess

    current = next(s for s in wf.STEP_ORDER if steps[s]['acceptance'] is None)
    return {'schema_version': wf.WORKFLOW_VERSION, 'current_step': current,
            'draft_version': old['draft_version'] + 1, 'steps': steps, 'drafts': drafts}


def _split_notes(document):
    body = document.body
    if body.count(md.NOTES_START) != 1 or body.count(md.NOTES_END) != 1:
        raise _refuse('Notes markers missing or duplicated')
    notes, after = body.split(md.NOTES_START, 1)[1].split(md.NOTES_END, 1)
    if after:
        raise _refuse('Unexpected text after Notes')
    return notes


def step(raw_detail, history, *, actor, timestamp):
    document = md.parse_document(raw_detail)
    meta = document.metadata
    idea = copy.deepcopy(meta['idea'])
    key = idea['idea_id']
    old = idea.get('workflow')
    if type(old) is not dict or old.get('schema_version') != 2:
        raise _refuse('Not a version 2 idea')
    v2.validate_workflow(old)
    revision = idea['revision']
    links = meta['history']
    if len(links) != revision or any(_HISTORY.format(key, n) not in history for n in range(1, revision + 1)):
        raise _refuse('History does not match the revision')
    for n, link in enumerate(links, 1):
        path = _HISTORY.format(key, n)
        if link['path'] != path or digest(history[path]) != link['sha256']:
            raise _refuse('History file does not match its link')
        md.decode_history(history[path], historical=True)

    idea['workflow'] = convert_workflow(old)
    idea['revision'] = revision + 1
    snapshot = {'revision': revision + 1, 'shape': copy.deepcopy(idea['shape']), 'ratings': copy.deepcopy(idea['ratings']),
                'assessments': copy.deepcopy(idea['assessments']), 'actor': actor, 'action': ACTION,
                'timestamp': timestamp, 'schema_version': wf.WORKFLOW_VERSION,
                'workflow': copy.deepcopy(idea['workflow'])}
    wf.validate_snapshot(snapshot)
    raw_history = md.encode_history(key, snapshot, origin=idea['origin'])
    path = _HISTORY.format(key, revision + 1)
    new_links = copy.deepcopy(links) + [{'path': path, 'sha256': digest(raw_history)}]
    new_detail = md.encode_detail(idea, _split_notes(document), extensions=copy.deepcopy(meta['extensions']),
                                  history_links=new_links, transaction_revision=meta['transaction_revision'])
    return new_detail, {path: raw_history}
