// Actual Flow with typed fake API; no browser/server qualification.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../../glitch-idea/web/folds.js', import.meta.url), 'utf8');
const {Flow, STEPS, validVisualize, visualizeFields} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const copy = value => structuredClone(value);
const IDEA = 'idea_' + '1'.repeat(32), OTHER = 'idea_' + '2'.repeat(32);
const SET = 'set_' + '3'.repeat(32), NEXT_SET = 'set_' + '4'.repeat(32);
const ASSET = 'asset_' + '5'.repeat(32), SECOND = 'asset_' + '6'.repeat(32);
const fields = (disposition = 'accepted_set', setId = null, reason = null) =>
  ({disposition, reason, design_set_id: setId, brief_evidence_id: null});
function initial() {
  return {ok: true, code: 'ok', idea_id: IDEA, revision: 4, draft_version: 0, backlog_revision: 0,
    current_step: 'visualize', agent_status: 'disconnected',
    steps: Object.fromEntries(STEPS.map(({key}, index) => [key, {status: index < 4 ? 'saved' : key === 'visualize' ? 'current' : 'todo',
      accepted_revision: index < 4 ? 4 : null, evidence_id: index < 4 ? 'fixture-' + key : null}])),
    accepted: Object.fromEntries(STEPS.map(({key}) => [key, null])), drafts: {}, draft: null};
}
function harness() {
  const state = initial(), writes = [], receipts = new Map(), faults = {}, reads = [], reconciles = [];
  let counter = 0;
  const api = {
    state: async idea => {
      reads.push(idea);
      if (faults.readFailures > 0) { faults.readFailures--; throw Object.assign(new Error('read_lost'), {uncertain: true}); }
      return faults.foreignState ? {...copy(state), idea_id: OTHER} : copy(state);
    },
    reconcile: async (id, idea) => {
      reconciles.push({id, idea});
      return {result: copy(receipts.get(id) ?? null), state: await api.state(idea)};
    },
    write: async (operation, payload) => {
      writes.push({operation, payload: copy(payload)});
      if (faults.loseBefore) throw Object.assign(new Error('lost_before_receipt'), {uncertain: true});
      assert.ok(['visual-set/accept', 'visual-disposition'].includes(operation));
      const canonical = copy(payload.fields);
      if (operation === 'visual-set/accept') canonical.design_set_id = payload.design_set_id ?? SET;
      state.accepted.visualize = canonical;
      delete state.drafts.visualize;
      state.revision++;
      state.steps.visualize = {status: operation === 'visual-set/accept' ? 'saved' : canonical.disposition,
        accepted_revision: state.revision, evidence_id: 'visualize-fixture'};
      state.current_step = 'assess';
      let result = {ok: true, code: 'ok', request_id: payload.request_id, idea_id: IDEA, write_state: 'applied',
        ...(operation === 'visual-set/accept' ? {design_set_id: canonical.design_set_id} : {})};
      if (faults.badReceipt) result = faults.badReceipt(result);
      receipts.set(payload.request_id, copy(result));
      if (faults.historical === 'pointer') state.accepted.visualize.design_set_id = NEXT_SET;
      if (faults.historical === 'review') state.steps.visualize.status = 'review-needed';
      if (faults.loseAck) throw Object.assign(new Error('lost_ack'), {uncertain: true});
      return result;
    },
  };
  const flow = new Flow(api, () => 'visual-request-' + (++counter));
  flow.load(state);
  return {flow, api, state, writes, receipts, faults, reads, reconciles};
}

test('default and partial Visualize fields are explicit typed buffers', () => {
  const h = harness();
  assert.deepEqual(h.flow.buffers.visualize, fields(null));
  assert.equal(validVisualize(h.flow.buffers.visualize), false);
  assert.equal(visualizeFields({}, true), true);
  assert.equal(visualizeFields({reason: 'Draft reason'}, true), true);
  assert.equal(visualizeFields({design_set_id: 42}, true), false);
  assert.equal(visualizeFields({disposition: 'invented'}, true), false);
  assert.equal(validVisualize({...fields(), score: 1}), false);
  h.state.drafts.visualize = {reason: 'Partial draft'}; h.flow.load(h.state);
  assert.deepEqual(h.flow.buffers.visualize, {reason: 'Partial draft'});
  h.state.drafts.visualize.design_set_id = false;
  assert.throws(() => h.flow.load(h.state), /Invalid Visualize/);
});

test('new explicit set submits one immutable null envelope then hydrates authoritative pointer', async () => {
  const h = harness(); h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET, SECOND]), true);
  assert.equal(h.writes.length, 1);
  assert.deepEqual(h.writes[0], {operation: 'visual-set/accept', payload: {request_id: 'visual-request-1', idea_id: IDEA,
    expected_revision: 4, expected_draft_version: 0, step: 'visualize', fields: fields(), proposal_id: null,
    expected_backlog_revision: null, design_set_id: null, asset_ids: [ASSET, SECOND]}});
  assert.deepEqual(h.flow.buffers.visualize, fields('accepted_set', SET));
  assert.equal(h.flow.dirty.has('visualize'), false);
  assert.equal(h.flow.pending, null); assert.equal(h.flow.current, 'assess');
  assert.equal(h.writes[0].payload.fields.design_set_id, null);
});

test('existing set uses identical top-level/fields pointer and null member selection', async () => {
  const h = harness(); h.flow.edit('visualize', fields('accepted_set', SET));
  assert.equal(await h.flow.saveVisualize(), true);
  assert.equal(h.writes[0].payload.design_set_id, SET);
  assert.equal(h.writes[0].payload.fields.design_set_id, SET);
  assert.equal(h.writes[0].payload.asset_ids, null);
  assert.deepEqual(h.flow.buffers.visualize, fields('accepted_set', SET));
});

test('skip needs no reason, has null pointers, and not-applicable no longer exists', async () => {
  for (const disposition of ['skipped']) {
    const h = harness(); h.flow.edit('visualize', fields(disposition, null, null));
    assert.equal(await h.flow.saveVisualize(), true);
    assert.equal(h.writes[0].operation, 'visual-disposition');
    assert.equal(Object.hasOwn(h.writes[0].payload, 'asset_ids'), false);
    assert.equal(Object.hasOwn(h.writes[0].payload, 'design_set_id'), false);
    assert.equal(h.flow.status('visualize'), disposition);
    assert.equal(h.flow.current, 'assess');
  }
  assert.equal(validVisualize(fields('skipped', null, null)), true);
  for (const invalid of [fields('not-applicable', null, 'Reason'), fields('skipped', SET, 'Reason'),
      {...fields('skipped', null, 'Reason'), brief_evidence_id: 'evidence-invented'}]) assert.equal(validVisualize(invalid), false);
});

test('new-set members refuse absent, empty, duplicated, mistyped or over-cap IDs without writing', async () => {
  const invalid = [null, [], [ASSET, ASSET], ['asset-bad'], [false],
    Array.from({length: 21}, (_, n) => 'asset_' + n.toString(16).padStart(32, '0'))];
  for (const ids of invalid) {
    const h = harness(); h.flow.edit('visualize', fields());
    assert.equal(await h.flow.saveVisualize(ids), false); assert.equal(h.writes.length, 0); assert.equal(h.flow.pending, null);
  }
  const h = harness(); h.flow.edit('visualize', fields('accepted_set', SET));
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  h.flow.edit('visualize', fields('skipped', null, 'Reason'));
  assert.equal(await h.flow.saveVisualize([ASSET]), false); assert.equal(h.writes.length, 0);
  const cap = harness(); cap.flow.edit('visualize', fields());
  assert.equal(await cap.flow.saveVisualize(Array.from({length: 20}, (_, n) => 'asset_' + n.toString(16).padStart(32, '0'))), true);
});

test('lost acknowledgement recovers through one existing engine without another write', async () => {
  const h = harness(); h.faults.loseAck = true; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), true);
  assert.equal(h.writes.length, 1); assert.equal(h.flow.pending, null);
  assert.equal(h.flow.buffers.visualize.design_set_id, SET);
  assert.equal(h.flow.message, 'Saved result recovered');
});

test('post-write read failure retains original null payload and explicit retry only reconciles receipt', async () => {
  const h = harness(); h.faults.readFailures = 2; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  const payload = copy(h.flow.pending.payload);
  assert.equal(payload.design_set_id, null); assert.equal(payload.fields.design_set_id, null);
  assert.equal(h.flow.pending.ambiguous, true); assert.equal(h.writes.length, 1);
  assert.equal(await h.flow.retry(), true); assert.equal(h.writes.length, 1);
  assert.deepEqual(h.writes[0].payload, payload); assert.equal(h.flow.buffers.visualize.design_set_id, SET);
});

test('missing receipt requires explicit retry of exactly the original null request', async () => {
  const h = harness(); h.faults.loseBefore = true; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  const original = copy(h.flow.pending.payload); assert.equal(h.writes.length, 1);
  h.faults.loseBefore = false;
  assert.equal(await h.flow.retry(), true); assert.equal(h.writes.length, 2);
  assert.deepEqual(h.writes[1].payload, original); assert.deepEqual(h.writes[0].payload, original);
});

test('newer operator edits survive reconciliation without generated pointer overwrite or advance', async () => {
  const h = harness(); h.faults.readFailures = 2; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  const edited = fields('skipped', null, 'Newer human answer'); h.flow.edit('visualize', edited);
  assert.equal(await h.flow.retry(), true);
  assert.deepEqual(h.flow.buffers.visualize, edited); assert.equal(h.flow.dirty.has('visualize'), true);
  assert.equal(h.flow.current, 'visualize'); assert.equal(h.flow.pending, null);
});

test('malformed or cross-identity receipts stay uncertain with the original pending request', async () => {
  for (const mutate of [r => ({...r, request_id: 'wrong-request'}), r => ({...r, idea_id: OTHER}),
      r => ({...r, design_set_id: null}), r => ({...r, design_set_id: 'set-bad'}), r => ({...r, code: 'invented'}),
      r => ({...r, write_state: 'invented'})]) {
    const h = harness(); h.faults.badReceipt = mutate; h.flow.edit('visualize', fields());
    assert.equal(await h.flow.saveVisualize([ASSET]), false);
    assert.equal(h.flow.pending.ambiguous, true); assert.equal(h.flow.pending.payload.fields.design_set_id, null);
    assert.equal(h.flow.error.code, 'invalid_response'); assert.equal(h.writes.length, 1);
    assert.ok(h.reads.every(id => id === IDEA)); assert.ok(h.reconciles.every(r => r.idea === IDEA));
    assert.equal(await h.flow.reloadKeepingAnswers(), false);
  }
});

test('Visualize foreign state never loads or changes the submitted idea scope', async () => {
  const h = harness(); h.faults.foreignState = true; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  assert.equal(h.flow.error.code, 'invalid_response'); assert.equal(h.flow.pending.ambiguous, true);
  assert.equal(h.flow.state.idea_id, IDEA); assert.equal(h.flow.state.revision, 4);
  assert.ok(h.reads.every(id => id === IDEA)); assert.ok(h.reconciles.every(r => r.idea === IDEA));
  assert.deepEqual(h.flow.buffers.visualize, fields()); assert.equal(h.writes.length, 1);
});

test('Visualize skip receipts with non-null design set stay uncertain', async () => {
  for (const disposition of ['skipped']) {
    const h = harness(); h.faults.badReceipt = result => ({...result, design_set_id: SET});
    const submitted = fields(disposition, null, 'Explicit reason'); h.flow.edit('visualize', submitted);
    assert.equal(await h.flow.saveVisualize(), false);
    assert.equal(h.flow.error.code, 'invalid_response'); assert.equal(h.flow.pending.ambiguous, true);
    assert.equal(h.flow.pending.operation, 'visual-disposition'); assert.equal(h.flow.current, 'visualize');
    assert.deepEqual(h.flow.buffers.visualize, submitted); assert.equal(h.writes.length, 1);
    assert.ok(h.reads.every(id => id === IDEA));
  }
});

test('existing-set receipt must match the requested pointer', async () => {
  const h = harness(); h.faults.badReceipt = r => ({...r, design_set_id: NEXT_SET});
  h.flow.edit('visualize', fields('accepted_set', SET));
  assert.equal(await h.flow.saveVisualize(), false); assert.equal(h.flow.pending.ambiguous, true);
  assert.equal(h.flow.buffers.visualize.design_set_id, SET);
});

test('valid historical changed-pointer receipt is a known conflict with reload available', async () => {
  const h = harness(); h.faults.historical = 'pointer'; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  assert.equal(h.flow.error.code, 'saved_state_changed'); assert.equal(h.flow.error.status, 409);
  assert.equal(h.flow.pending, null); assert.equal(h.flow.current, 'visualize');
  assert.deepEqual(h.flow.buffers.visualize, fields()); assert.equal(h.flow.dirty.has('visualize'), true);
  assert.equal(h.flow.state.accepted.visualize.design_set_id, NEXT_SET);
  assert.equal(await h.flow.reloadKeepingAnswers(), true); assert.deepEqual(h.flow.buffers.visualize, fields());
  assert.equal(h.writes.length, 1);
});

test('recovered historical review-needed receipt clears uncertainty without false saved status', async () => {
  const h = harness(); h.faults.historical = 'review'; h.faults.loseAck = true; h.flow.edit('visualize', fields());
  assert.equal(await h.flow.saveVisualize([ASSET]), false);
  assert.equal(h.flow.error.code, 'saved_state_changed'); assert.equal(h.flow.pending, null);
  assert.equal(h.flow.state.steps.visualize.status, 'review-needed'); assert.equal(h.flow.current, 'visualize');
  assert.equal(h.flow.status('visualize'), 'unsaved');
  assert.equal(await h.flow.reloadKeepingAnswers(), true); assert.equal(h.writes.length, 1);
});

test('historical disposition answer drift is known and preserves the submitted reason', async () => {
  const h = harness(); h.faults.readFailures = 2;
  const submitted = fields('skipped', null, 'Original reason'); h.flow.edit('visualize', submitted);
  assert.equal(await h.flow.saveVisualize(), false);
  h.state.accepted.visualize.reason = 'Later saved reason';
  assert.equal(await h.flow.retry(), false); assert.equal(h.flow.pending, null);
  assert.equal(h.flow.error.code, 'saved_state_changed'); assert.deepEqual(h.flow.buffers.visualize, submitted);
});
