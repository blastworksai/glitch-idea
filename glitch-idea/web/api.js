// Same-origin browser API; credentials live only in this instance.
// A moved or delivered idea is a read-only pointer to its file in a workspace; an active one has no home.
const homeOk = home => home !== null && typeof home === 'object' && !Array.isArray(home) &&
  Object.keys(home).sort().join() === 'file_path,workspace_name,workspace_path' &&
  Object.values(home).every(value => typeof value === 'string' && value.length > 0 && value.length <= 4096);
// A pointer's file is exactly <workspace>/ideas/<idea_id>.md; the reply is checked here, not trusted.
const homeMatches = (home, id) => {
  const file = hostPath(home.file_path), workspace = hostPath(home.workspace_path);
  return file !== null && workspace !== null && file === workspace.replace(/\/+$/, '') + '/ideas/' + id + '.md';
};
const refOk = ref => scalarText(ref, 500) && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/.test(ref);
const lifecycleOk = row => typeof row.read_only === 'boolean' && row.read_only === (row.lifecycle !== 'active') && (
  row.lifecycle === 'active'
    ? ['archived', 'ready-to-plan', 'review-needed', 'in-progress'].includes(row.status) && row.home === null && row.delivered_ref === null
    : row.lifecycle === 'moved'
      ? row.status === 'moved' && homeOk(row.home) && homeMatches(row.home, row.idea_id) && row.delivered_ref === null
      : row.lifecycle === 'delivered' && row.status === 'delivered' && homeOk(row.home) &&
        homeMatches(row.home, row.idea_id) && refOk(row.delivered_ref));

export class ApiError extends Error {
  constructor(code, status = 0, data = {}, uncertain = false) {
    super(code);
    this.code = code;
    this.status = status;
    this.data = data;
    this.uncertain = uncertain;
  }
}

const WRITES = new Set(['capture', 'draft', 'accept', 'navigate', 'transport', 'activity', 'propose',
  'visual-disposition', 'visual-set/accept', 'handoff']);
const BINDING = /^binding_[0-9a-f]{32}$/;
// Per-tab secret: sessionStorage only (origin+port scoped, per tab); never persisted beyond the tab.
const TAB_KEY = 'glitch-idea-tab:';
function defaultTabStorage() {
  try { return globalThis.sessionStorage ?? null; } catch { return null; }
}
const SESSION = /^session_[0-9a-f]{32}$/;
const UPLOAD = /^upload_[0-9a-f]{32}$/;
const ASSET = /^asset_[0-9a-f]{32}$/;
const IDEA = /^idea_[0-9a-f]{32}$/;
// Every step key a resume row may name, in the default order (folds.js STEPS; a test keeps the two equal).
const STEP_KEYS = ['capture', 'priorities', 'method', 'discovery', 'exploration', 'visualize', 'assess', 'review'];
const MARKDOWN_MAX_BYTES = 2 * 1024 * 1024;
const REQUEST = /^[A-Za-z0-9_.:-]{1,128}$/;
export const UPLOAD_MAX_BYTES = 25 * 1024 * 1024;

// Fixed transport schemas. Host paths are evidence text only.
const HANDOFF = /^handoff_[0-9a-f]{32}$/;
const HASH = /^[0-9a-f]{64}$/;
const METHODS = new Set(['bounded-plan', 'adaptive-slices', 'appetite-led', 'experiment-led']);
const HANDOFF_PAYLOAD = ['request_id', 'idea_id', 'expected_revision', 'expected_draft_version', 'expected_backlog_revision'];
const HANDOFF_RECEIPT = ['ok', 'code', 'request_id', 'write_state', 'idea_id', 'revision', 'draft_version',
  'backlog_revision', 'handoff_index', 'handoff_id', 'path', 'sha256', 'handoff', 'handoff_current'];
const MIME_SUFFIXES = { 'image/png': ['png'], 'image/jpeg': ['jpg', 'jpeg'], 'image/webp': ['webp'],
  'application/pdf': ['pdf'], 'image/svg+xml': ['svg'], 'text/html': ['html', 'htm'],
  'text/css': ['css'], 'application/json': ['json'], 'text/markdown': ['md', 'markdown'],
  'text/plain': ['txt', 'text', 'md', 'markdown'], 'application/zip': ['zip'] };

function exact(value, keys) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function counter(value, minimum = 0) {
  return Number.isSafeInteger(value) && value >= minimum && value <= 10 ** 12;
}
function typed(pattern, value) {
  return typeof value === 'string' && pattern.exec(value)?.[0] === value;
}
function scalarText(value, maximum, nonempty = true) {
  if (typeof value !== 'string' || (nonempty && !value.trim())) return false;
  let count = 0;
  for (const scalar of value) {
    const point = scalar.codePointAt(0);
    if (++count > maximum || (point >= 0xd800 && point <= 0xdfff)) return false;
  }
  return true;
}
function boundedReply(value) {
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength <= 1024 * 1024; }
  catch { return false; }
}
function hostPath(value) {
  if (!scalarText(value, 65536) || value.includes('\0')) return null;
  // Accommodate native POSIX, drive and UNC service hosts without fetching paths.
  const windows = /^[A-Za-z]:[\\/]/.test(value) || /^\\\\[^\\]+\\[^\\]+/.test(value);
  if (!windows && !value.startsWith('/')) return null;
  const normalized = windows ? value.replaceAll('\\', '/') : value;
  if (normalized.split('/').includes('..') || normalized.split('/').includes('.')) return null;
  return normalized;
}
function invalidReply(uncertain = false) { throw new ApiError('invalid_response', 0, {}, uncertain); }
const SETTINGS_WHERE = new Set(['native', 'api']);
const SETTINGS_KEY = /^[\x21-\x7e]{8,512}$/;
function settingsReply(data, write = false) {
  if (!exact(data, ['ok', 'code', 'where', 'api']) || data.ok !== true || typeof data.code !== 'string' ||
      !SETTINGS_WHERE.has(data.where) || !exact(data.api, ['base_url', 'key_set']) ||
      !(data.api.base_url === null || (typeof data.api.base_url === 'string' && data.api.base_url.length <= 2048)) ||
      typeof data.api.key_set !== 'boolean' || !boundedReply(data)) invalidReply(write);
  return data;
}
function handoffPayload(payload) {
  if (!exact(payload, HANDOFF_PAYLOAD) || !typed(REQUEST, payload.request_id) ||
      !typed(IDEA, payload.idea_id) || !counter(payload.expected_revision, 1) ||
      !counter(payload.expected_draft_version) || !counter(payload.expected_backlog_revision)) throw new ApiError('invalid_handoff');
  return Object.fromEntries(HANDOFF_PAYLOAD.map(key => [key, payload[key]]));
}

// A checked public packet still needs CURRENT server/local guards before copy.
// This checks the delivery schema and generated path relationships, not bytes on disk.
export function validateHandoff(packet, ideaId = null) {
  if (!exact(packet, ['handoff_id', 'source_revision', 'source_digest', 'path', 'sha256', 'prompt', 'source_files', 'design_set']) ||
      !typed(HANDOFF, packet.handoff_id) || !counter(packet.source_revision, 1) ||
      !typed(HASH, packet.source_digest) || !typed(HASH, packet.sha256) || !scalarText(packet.prompt, 1024 * 1024) ||
      !exact(packet.source_files, ['detail', 'index', 'revision']) || !boundedReply(packet)) invalidReply();
  const files = packet.source_files;
  for (const file of Object.values(files)) {
    if (!exact(file, ['path', 'sha256']) || !hostPath(file.path) || !typed(HASH, file.sha256)) invalidReply();
  }
  const detail = hostPath(files.detail.path), match = detail.match(/^([\s\S]*)\/(idea_[0-9a-f]{32})\.md$/);
  if (!match || match[0] !== detail || (ideaId !== null && match[2] !== ideaId)) invalidReply();
  const [, root, id] = match;
  if (hostPath(files.index.path) !== root + '/IDEAS.md' ||
      hostPath(files.revision.path) !== root + '/history/' + id + '/r' + packet.source_revision + '.md' ||
      hostPath(packet.path) !== root + '/history/' + id + '/metadata/' + packet.sha256 + '.md') invalidReply();
  if (packet.design_set !== null) {
    const design = packet.design_set;
    if (!exact(design, ['set_id', 'members']) || !typed(/^set_[0-9a-f]{32}$/, design.set_id) ||
        !Array.isArray(design.members) || design.members.length < 1 || design.members.length > 20) invalidReply();
    const ids = new Set(); let total = 0;
    for (const member of design.members) {
      if (!exact(member, ['asset_id', 'name', 'type', 'size', 'sha256', 'path']) ||
          !typed(ASSET, member.asset_id) || ids.has(member.asset_id) ||
          !scalarText(member.name, 4096) || typeof member.type !== 'string' || !Object.hasOwn(MIME_SUFFIXES, member.type) ||
          !MIME_SUFFIXES[member.type].includes(member.name.split('.').at(-1).toLowerCase()) || !member.name.includes('.') ||
          !counter(member.size, 1) || member.size > UPLOAD_MAX_BYTES || !typed(HASH, member.sha256) ||
          hostPath(member.path) !== root + '/assets/blobs/' + member.asset_id + '.bin') invalidReply();
      ids.add(member.asset_id); total += member.size;
    }
    if (total > 100 * 1024 * 1024) invalidReply();
  }
  return structuredClone(packet);
}

function handoffResult(data, payload) {
  if (data?.write_state === 'committed_uncertain' || data?.committed === true) throw new ApiError('durability_uncertain', 0, {}, true);
  if (!exact(data, HANDOFF_RECEIPT) || data.ok !== true || !['applied', 'no_op'].includes(data.write_state) ||
      typeof data.handoff_current !== 'boolean' || data.code !== (data.handoff_current ? 'ok' : 'historical_handoff') ||
      data.request_id !== payload.request_id || data.idea_id !== payload.idea_id ||
      data.revision !== payload.expected_revision || data.draft_version !== payload.expected_draft_version ||
      data.backlog_revision !== payload.expected_backlog_revision || !counter(data.handoff_index, 1) || data.handoff_index > 128) invalidReply(true);
  try { validateHandoff(data.handoff, payload.idea_id); } catch { invalidReply(true); }
  if (data.handoff_id !== data.handoff.handoff_id || data.sha256 !== data.handoff.sha256 ||
      data.revision !== data.handoff.source_revision || data.path !== 'history/' + payload.idea_id + '/metadata/' + data.sha256 + '.md') invalidReply(true);
  if (!boundedReply(data)) invalidReply(true);
  return data;
}

function uploadName(value) {
  if (typeof value !== 'string' || !value.trim()) return false;
  let count = 0;
  for (const scalar of value) {
    const point = scalar.codePointAt(0);
    if (++count > 4096 || (point >= 0xd800 && point <= 0xdfff)) return false;
  }
  return true;
}

function uploadId(value) {
  if (typeof value !== 'string' || !UPLOAD.test(value)) throw new ApiError('invalid_upload');
  return value;
}

function uploadResult(data, requestId, expectedUpload = null, expectedIdea = null) {
  if (data.write_state === 'committed_uncertain' || data.committed === true) {
    throw new ApiError('durability_uncertain', 0, data, true);
  }
  if (data.ok !== true || typeof data.code !== 'string' ||
      !['applied', 'no_op'].includes(data.write_state) || data.request_id !== requestId ||
      typeof data.upload_id !== 'string' || !UPLOAD.test(data.upload_id) ||
      typeof data.asset_id !== 'string' || !ASSET.test(data.asset_id) ||
      typeof data.idea_id !== 'string' || !IDEA.test(data.idea_id) ||
      (data.completion_request_id !== undefined && data.completion_request_id !== 'upload-bytes:' + data.upload_id) ||
      (expectedUpload !== null && data.upload_id !== expectedUpload) ||
      (expectedIdea !== null && data.idea_id !== expectedIdea)) {
    throw new ApiError('invalid_response', 0, {}, true);
  }
  return data;
}

async function attachmentBody(response, expected, maximum) {
  if (!response.body || typeof response.body.getReader !== 'function') throw new ApiError('invalid_response', response.status);
  const reader = response.body.getReader(), chunks = [];
  let size = 0, ended = false;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) { ended = true; break; }
      if (!(value instanceof Uint8Array)) throw new ApiError('invalid_response', response.status);
      size += value.byteLength;
      if (size > maximum || size > expected) throw new ApiError('too_large', response.status);
      chunks.push(value);
    }
    if (size !== expected) throw new ApiError('invalid_response', response.status);
    return new Blob(chunks, {type: 'application/octet-stream'});
  } finally {
    if (!ended) { try { await reader.cancel(); } catch {} }
    reader.releaseLock();
  }
}

export class IdeaApi {
  constructor(fetcher = globalThis.fetch.bind(globalThis), timeout = 15000, bindingId = null, transferTimeout = 300000,
              tabStorage = defaultTabStorage()) {
    this.tabStorage = tabStorage;
    this.tabSecret = null;
    // Byte transfers have a separate, representable timer budget.
    if (!Number.isSafeInteger(transferTimeout) || transferTimeout < 1 || transferTimeout > 2147483647) {
      throw new ApiError('invalid_timeout');
    }
    this.fetcher = fetcher;
    this.timeout = timeout;
    this.transferTimeout = transferTimeout;
    this.csrf = null;
    this.bindingId = null;
    this.sessionId = null;
    this.requestScope = 0;
    if (bindingId !== null) this.pinBinding(bindingId);
  }

  pinBinding(bindingId) {
    if (typeof bindingId !== 'string' || !BINDING.test(bindingId)) throw new ApiError('invalid_binding');
    if (this.bindingId && this.bindingId !== bindingId) throw new ApiError('session_binding_mismatch', 403);
    this.bindingId = bindingId;
    if (!this.tabSecret) this.tabSecret = this.loadTabSecret();
  }

  tabKey() { return TAB_KEY + this.bindingId; }

  loadTabSecret() {
    try {
      const value = this.tabStorage ? this.tabStorage.getItem(this.tabKey()) : null;
      return typeof value === 'string' && value ? value : null;
    } catch { return null; }
  }

  storeTabSecret(value) {
    this.tabSecret = value;
    try { if (this.tabStorage) this.tabStorage.setItem(this.tabKey(), value); } catch { /* tab memory only */ }
  }

  clearTabSecret() {
    this.tabSecret = null;
    try { if (this.tabStorage && this.bindingId) this.tabStorage.removeItem(this.tabKey()); } catch { /* ignore */ }
  }

  // Every /api/v1 call carries the per-tab secret; the cookie alone never authorises.
  authHeaders(headers) {
    if (this.bindingId) headers['X-Idea-Binding'] = this.bindingId;
    if (this.tabSecret) headers['X-Idea-Tab'] = this.tabSecret;
    return headers;
  }

  acceptSession(data) {
    if (typeof data.binding_id !== 'string' || !BINDING.test(data.binding_id) ||
        typeof data.session_id !== 'string' || !SESSION.test(data.session_id) ||
        typeof data.csrf_token !== 'string' || !data.csrf_token) {
      this.csrf = null;
      throw new ApiError('invalid_session');
    }
    if ((this.bindingId && this.bindingId !== data.binding_id) ||
        (this.sessionId && this.sessionId !== data.session_id)) {
      this.csrf = null;
      throw new ApiError('session_binding_mismatch', 403);
    }
    this.pinBinding(data.binding_id);
    this.sessionId = data.session_id;
    this.requestScope++;
    this.csrf = data.csrf_token;
    return data;
  }

  async request(route, body, pairing = false) {
    const writing = body !== undefined;
    if (!pairing && !this.bindingId) throw new ApiError('browser_unauthorized', 401);
    if (writing && !pairing && !this.csrf) throw new ApiError('browser_unauthorized', 401);
    const scope = this.requestScope;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    try {
      const headers = this.authHeaders({Accept: 'application/json'});
      if (writing) headers['Content-Type'] = 'application/json';
      if (writing && !pairing) headers['X-CSRF-Token'] = this.csrf;
      const response = await this.fetcher('/api/v1/' + route, {
        method: writing ? 'POST' : 'GET', credentials: 'same-origin',
        cache: 'no-store', redirect: 'error', headers,
        signal: controller.signal, ...(writing ? {body: JSON.stringify(body)} : {}),
      });
      let data;
      try { data = await response.json(); }
      catch { throw new ApiError('invalid_response', response.status, {}, writing); }
      // The superseded-tab refusal arrives as {"error":{"code":...}}; read it as an ordinary failure.
      if (!response.ok && data && typeof data === 'object' && typeof data.code !== 'string' && typeof data.error?.code === 'string') {
        data = {ok: false, code: data.error.code};
      }
      if (!data || typeof data.ok !== 'boolean' || typeof data.code !== 'string') {
        throw new ApiError('invalid_response', response.status, {}, writing);
      }
      if (!response.ok || !data.ok) {
        if (response.status === 401 && scope === this.requestScope) { this.csrf = null; this.clearTabSecret(); }
        throw new ApiError(data.code, response.status, data,
          data.write_state === 'committed_uncertain' || data.committed === true);
      }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('connection_lost', 0, {}, writing);
    } finally { clearTimeout(timer); }
  }

  async session() {
    const data = await this.request('session');
    return this.acceptSession(data);
  }

  async pair(code) {
    this.requestScope++;
    this.csrf = null;
    const paired = await this.request('pair', {code}, true);
    this.acceptSession(paired);
    if (typeof paired.tab_secret !== 'string' || !paired.tab_secret) throw new ApiError('invalid_session');
    this.storeTabSecret(paired.tab_secret);
    return this.session();
  }

  state(ideaId = null) {
    return this.request('state' + (ideaId ? '?idea_id=' + encodeURIComponent(ideaId) : ''));
  }

  write(operation, payload) {
    if (!WRITES.has(operation)) throw new Error('Unsupported API operation');
    return this.request(operation, payload);
  }

  requirePinnedSession() {
    if (!typed(BINDING, this.bindingId) || !typed(SESSION, this.sessionId)) {
      throw new ApiError('browser_unauthorized', 401);
    }
  }

  validateHandoff(packet, ideaId = null) { return validateHandoff(packet, ideaId); }

  async handoff(payload) {
    const envelope = handoffPayload(payload);
    this.requirePinnedSession();
    return handoffResult(await this.request('handoff', envelope), envelope);
  }

  async reconcileHandoff(requestId, ideaId, expectedPayload) {
    const envelope = handoffPayload(expectedPayload);
    if (requestId !== envelope.request_id || ideaId !== envelope.idea_id) throw new ApiError('invalid_handoff');
    this.requirePinnedSession();
    // Validate the receipt BEFORE reconcile can use its idea to read state:
    // state reads also set private server selection. No foreign receipt redirect.
    const resolved = await this.reconcile(requestId, ideaId, result => handoffResult(result, envelope));
    if (resolved.state?.session_id !== this.sessionId || resolved.state?.idea_id !== ideaId) invalidReply(true);
    return resolved;
  }

  // Move one idea in the backlog by the human's hand (CAS on the backlog revision).
  async rerank(payload) {
    const keys = ['request_id', 'idea_id', 'expected_backlog_revision', 'position', 'reason'];
    if (!exact(payload, keys) || typeof payload.request_id !== 'string' || !REQUEST.test(payload.request_id) ||
        !typed(IDEA, payload.idea_id) || !counter(payload.expected_backlog_revision) || !counter(payload.position, 1) ||
        !(payload.reason === null || (typeof payload.reason === 'string' && payload.reason.trim() && payload.reason.length <= 2000))) {
      throw new ApiError('invalid_input');
    }
    this.requirePinnedSession();
    const data = await this.request('rerank', payload);
    if (!exact(data, ['ok', 'code', 'request_id', 'write_state', 'idea_id', 'position', 'backlog_revision', 'revision', 'draft_version']) ||
        data.ok !== true || data.code !== 'ok' || data.request_id !== payload.request_id || !['applied', 'no_op'].includes(data.write_state) ||
        data.idea_id !== payload.idea_id || data.position !== payload.position || !counter(data.backlog_revision, 1) ||
        !counter(data.revision, 1) || !counter(data.draft_version)) invalidReply(true);
    return data;
  }

  // Take a terminal-guided step by hand: cancels only that step's open agent request.
  async release(step) {
    if (step !== 'discovery' && step !== 'exploration') throw new ApiError('invalid_input');
    this.requirePinnedSession();
    const data = await this.request('conversation/release', {step});
    if (!exact(data, ['ok', 'code', 'idea_id', 'step', 'hand', 'released', 'write_state', 'revision', 'draft_version']) ||
        data.ok !== true || data.code !== 'ok' || !typed(IDEA, data.idea_id) || data.step !== step || typeof data.hand !== 'boolean' ||
        !counter(data.released) || !['applied', 'no_op'].includes(data.write_state) || !counter(data.revision, 1) ||
        !counter(data.draft_version)) invalidReply(true);
    return data;
  }

  async ideas() {
    this.requirePinnedSession();
    const data = await this.request('ideas');
    if (!exact(data, ['ok', 'code', 'backlog_revision', 'total', 'ideas']) || data.ok !== true || data.code !== 'ok' ||
        !counter(data.backlog_revision) || !counter(data.total) || data.total > 4096 ||
        !Array.isArray(data.ideas) || data.ideas.length !== data.total || !boundedReply(data)) invalidReply();
    const ids = new Set();
    for (const [index, row] of data.ideas.entries()) {
      const base = ['idea_id', 'revision', 'position', 'title', 'status', 'method', 'updated', 'detail_path'];
      // Resume fields (current step, completed count) come from current services; older replies omit them.
      const resume = Object.hasOwn(row, 'current_step');
      // The four lifecycle keys travel together: all present, or (an older reply) all absent, which means an active idea.
      const pointer = Object.hasOwn(row, 'lifecycle');
      const view = pointer ? row : {...row, lifecycle: 'active', home: null, delivered_ref: null, read_only: false};
      if (!exact(pointer ? row : view, [...base, ...(resume ? ['current_step', 'completed_steps'] : []), 'lifecycle', 'home', 'delivered_ref', 'read_only']) ||
          (!pointer && ['home', 'delivered_ref', 'read_only'].some(key => Object.hasOwn(row, key))) ||
          (resume && (!STEP_KEYS.includes(row.current_step) || !Number.isSafeInteger(row.completed_steps) ||
            row.completed_steps < 0 || row.completed_steps > STEP_KEYS.length)) ||
          !typed(IDEA, row.idea_id) || ids.has(row.idea_id) || !counter(row.revision, 1) ||
          row.position !== index + 1 || !scalarText(row.title, 200, false) ||
          !lifecycleOk(view) ||
          !(row.method === null || METHODS.has(row.method)) || !scalarText(row.updated, 200) ||
          !hostPath(row.detail_path) || (view.lifecycle === 'active' && !hostPath(row.detail_path).endsWith('/' + row.idea_id + '.md')) ||
          (view.lifecycle !== 'active' && row.detail_path !== view.home.file_path)) invalidReply();
      ids.add(row.idea_id);
    }
    return data;
  }

  // Setup: where ideas live. The key is write-only: it is sent by saveSettings and never read back.
  async settings() {
    this.requirePinnedSession();
    return settingsReply(await this.request('settings'));
  }

  async saveSettings(payload) {
    if (!exact(payload, ['where', 'base_url', 'key']) || !SETTINGS_WHERE.has(payload.where) ||
        !(payload.base_url === null || (typeof payload.base_url === 'string' && payload.base_url.length <= 2048)) ||
        !(payload.key === null || payload.key === '' || (typeof payload.key === 'string' && SETTINGS_KEY.test(payload.key)))) {
      throw new ApiError('invalid_input'); // fixed code; the payload (and any key in it) is never echoed
    }
    this.requirePinnedSession();
    return settingsReply(await this.request('settings/save', {where: payload.where, base_url: payload.base_url, key: payload.key}), true);
  }

  async testSettings() {
    this.requirePinnedSession();
    const data = await this.request('settings/test', {});
    if (!exact(data, ['ok', 'code', 'reachable', 'reason', 'service']) || data.ok !== true || typeof data.reachable !== 'boolean' ||
        !(data.reason === null || (typeof data.reason === 'string' && /^[a-z_]{1,64}$/.test(data.reason))) ||
        !(data.service === null || scalarText(data.service, 200)) || !boundedReply(data)) invalidReply();
    return data;
  }

  async selection(ideaId) {
    if (!(ideaId === null || typed(IDEA, ideaId))) throw new ApiError('invalid_selection');
    this.requirePinnedSession();
    // An explicit navigation ATTEMPT supersedes older credential
    // side effects even on refusal. Its own/live 401 still invalidates CSRF.
    // This never restores credentials; it only scopes asynchronous invalidation.
    this.requestScope++;
    const data = await this.request('selection', {idea_id: ideaId});
    if (!exact(data, ['ok', 'code', 'session_id', 'idea_id', 'revision', 'draft_version', 'backlog_revision']) ||
        data.ok !== true || data.code !== 'ok' || data.session_id !== this.sessionId || data.idea_id !== ideaId ||
        !counter(data.revision, ideaId === null ? 0 : 1) || !counter(data.draft_version) || !counter(data.backlog_revision) ||
        (ideaId === null && (data.revision !== 0 || data.draft_version !== 0))) invalidReply(true);
    return data;
  }

  // Authenticated fixed GET: renderer creates/revokes only a download object URL.
  async attachment(assetId, expectedSize) {
    if (typeof assetId !== 'string' || !ASSET.test(assetId) ||
        !Number.isSafeInteger(expectedSize) || expectedSize < 1 || expectedSize > UPLOAD_MAX_BYTES) {
      throw new ApiError('invalid_attachment');
    }
    if (!this.bindingId) throw new ApiError('browser_unauthorized', 401);
    const scope = this.requestScope;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.transferTimeout);
    let response;
    try {
      response = await this.fetcher('/api/v1/attachments/' + assetId, {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: this.authHeaders({Accept: 'application/octet-stream'}),
        signal: controller.signal,
      });
      if (response.status === 401 && scope === this.requestScope) { this.csrf = null; this.clearTabSecret(); }
      if (!response.headers || typeof response.headers.get !== 'function') throw new ApiError('invalid_response', response.status);
      const length = response.headers.get('Content-Length');
      const type = response.headers.get('Content-Type');
      if (typeof length !== 'string' || !/^[1-9][0-9]{0,9}$/.test(length)) throw new ApiError('invalid_response', response.status);
      const size = Number(length);
      if (!response.ok) {
        // Never expose arbitrary HTTP bodies/diagnostics through ApiError.data.
        if (type !== 'application/json' || size > 8192) throw new ApiError('invalid_response', response.status);
        const body = await attachmentBody(response, size, 8192);
        let data;
        try { data = JSON.parse(await body.text()); }
        catch { throw new ApiError('invalid_response', response.status); }
        if (!data || data.ok !== false || typeof data.code !== 'string' || !/^[a-z][a-z0-9_]{0,99}$/.test(data.code)) {
          throw new ApiError('invalid_response', response.status);
        }
        throw new ApiError(data.code, response.status, {ok: false, code: data.code});
      }
      if (size > UPLOAD_MAX_BYTES) throw new ApiError('too_large', response.status);
      if (response.status !== 200 || size !== expectedSize ||
          !['application/octet-stream', 'image/png', 'image/jpeg', 'image/webp'].includes(type)) {
        throw new ApiError('invalid_response', response.status);
      }
      return await attachmentBody(response, expectedSize, UPLOAD_MAX_BYTES);
    } catch (error) {
      try { await response?.body?.cancel(); } catch {}
      if (error instanceof ApiError) throw error;
      throw new ApiError('connection_lost');
    } finally { clearTimeout(timer); }
  }

  // One idea's own Markdown file, read-only, as text: shown, never rendered or executed.
  async ideaMarkdown(ideaId) {
    if (typeof ideaId !== 'string' || !IDEA.test(ideaId)) throw new ApiError('invalid_selection');
    if (!this.bindingId) throw new ApiError('browser_unauthorized', 401);
    const scope = this.requestScope;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.transferTimeout);
    let response;
    try {
      response = await this.fetcher('/api/v1/ideas/' + ideaId + '/markdown', {
        method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: this.authHeaders({Accept: 'text/plain'}), signal: controller.signal,
      });
      if (response.status === 401 && scope === this.requestScope) { this.csrf = null; this.clearTabSecret(); }
      if (!response.headers || typeof response.headers.get !== 'function') throw new ApiError('invalid_response', response.status);
      const length = response.headers.get('Content-Length'), type = response.headers.get('Content-Type');
      if (typeof length !== 'string' || !/^(0|[1-9][0-9]{0,9})$/.test(length)) throw new ApiError('invalid_response', response.status);
      const size = Number(length);
      if (!response.ok) {
        if (type !== 'application/json' || size < 1 || size > 8192) throw new ApiError('invalid_response', response.status);
        let data;
        try { data = JSON.parse(await (await attachmentBody(response, size, 8192)).text()); }
        catch (error) { if (error instanceof ApiError) throw error; throw new ApiError('invalid_response', response.status); }
        if (!data || data.ok !== false || typeof data.code !== 'string' || !/^[a-z][a-z0-9_]{0,99}$/.test(data.code)) throw new ApiError('invalid_response', response.status);
        throw new ApiError(data.code, response.status, {ok: false, code: data.code});
      }
      if (response.status !== 200 || type !== 'text/plain; charset=utf-8' || size > MARKDOWN_MAX_BYTES) throw new ApiError('invalid_response', response.status);
      if (size === 0) return '';
      const raw = await (await attachmentBody(response, size, MARKDOWN_MAX_BYTES)).arrayBuffer();
      try { return new TextDecoder('utf-8', {fatal: true}).decode(raw); }
      catch { throw new ApiError('invalid_response', response.status); }
    } catch (error) {
      try { await response?.body?.cancel(); } catch {}
      if (error instanceof ApiError) throw error;
      throw new ApiError('connection_lost');
    } finally { clearTimeout(timer); }
  }

  // Upload seam: one explicit request per call, never an automatic retry.
  async uploadMetadata(payload) {
    const keys = ['request_id', 'idea_id', 'expected_revision', 'name', 'declared_type', 'size'];
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
        Object.keys(payload).length !== keys.length || !keys.every(key => Object.hasOwn(payload, key)) ||
        typeof payload.request_id !== 'string' || !REQUEST.test(payload.request_id) ||
        typeof payload.idea_id !== 'string' || !IDEA.test(payload.idea_id) ||
        !Number.isSafeInteger(payload.expected_revision) || payload.expected_revision < 1 ||
        !uploadName(payload.name) ||
        typeof payload.declared_type !== 'string' || !payload.declared_type || payload.declared_type.length > 128 ||
        /[\0\r\n]/.test(payload.declared_type) ||
        !Number.isSafeInteger(payload.size) || payload.size < 1 || payload.size > UPLOAD_MAX_BYTES) {
      throw new ApiError('invalid_upload');
    }
    const envelope = Object.fromEntries(keys.map(key => [key, payload[key]]));
    const data = uploadResult(await this.request('uploads', envelope), envelope.request_id, null, envelope.idea_id);
    if (data.completion_request_id !== 'upload-bytes:' + data.upload_id) {
      throw new ApiError('invalid_response', 0, {}, true);
    }
    // An intent is not a completed upload or accepted Visualize set.
    return data;
  }

  async uploadBytes(id, bytes) {
    uploadId(id);
    if (!(bytes instanceof Blob) || bytes.size < 1 || bytes.size > UPLOAD_MAX_BYTES) throw new ApiError('invalid_upload');
    if (!this.bindingId || !this.csrf) throw new ApiError('browser_unauthorized', 401);
    const scope = this.requestScope;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.transferTimeout);
    try {
      const response = await this.fetcher('/api/v1/uploads/' + id + '/bytes', {
        method: 'PUT', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: this.authHeaders({Accept: 'application/json', 'Content-Type': 'application/octet-stream',
          'X-CSRF-Token': this.csrf}),
        // Browser Fetch owns Content-Length; no caller-supplied URL/header/path.
        body: bytes, signal: controller.signal,
      });
      let data;
      try { data = await response.json(); }
      catch { throw new ApiError('invalid_response', response.status, {}, true); }
      if (!data || typeof data.ok !== 'boolean' || typeof data.code !== 'string') {
        throw new ApiError('invalid_response', response.status, {}, true);
      }
      if (!response.ok || !data.ok) {
        if (response.status === 401 && scope === this.requestScope) { this.csrf = null; this.clearTabSecret(); }
        throw new ApiError(data.code, response.status, data,
          data.write_state === 'committed_uncertain' || data.committed === true);
      }
      return uploadResult(data, 'upload-bytes:' + id, id);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError('connection_lost', 0, {}, true);
    } finally { clearTimeout(timer); }
  }

  reconcileUpload(requestId, ideaId) {
    if (typeof requestId !== 'string' || !REQUEST.test(requestId) || typeof ideaId !== 'string' || !IDEA.test(ideaId)) {
      throw new ApiError('invalid_upload');
    }
    return this.reconcile(requestId, ideaId).then(resolved => {
      if (resolved.result !== null) {
        uploadResult(resolved.result, requestId, null, ideaId);
        if (resolved.result.completion_request_id !== 'upload-bytes:' + resolved.result.upload_id) {
          throw new ApiError('invalid_response', 0, {}, true);
        }
      }
      return resolved;
    });
  }

  reconcileUploadBytes(id, ideaId) {
    uploadId(id);
    if (typeof ideaId !== 'string' || !IDEA.test(ideaId)) throw new ApiError('invalid_upload');
    return this.reconcile('upload-bytes:' + id, ideaId).then(resolved => {
      if (resolved.result !== null) uploadResult(resolved.result, 'upload-bytes:' + id, id, ideaId);
      return resolved;
    });
  }

  async reconcile(requestId, ideaId = null, validateResult = null) {
    let result;
    try { result = await this.request('requests/' + encodeURIComponent(requestId)); }
    catch (error) {
      if (error.status !== 404 || error.code !== 'request_not_found') throw error;
      result = null;
    }
    // A trusted typed helper can reject identity BEFORE the state navigation read.
    // Existing callers retain their original receipt/state behavior.
    if (result !== null && validateResult !== null) validateResult(result);
    // Read both, including when the receipt is missing, before retry is offered.
    const state = await this.state(result?.idea_id ?? ideaId);
    return {result, state};
  }
}
