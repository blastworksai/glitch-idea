// Actual accepted order; paths/titles are literal evidence text.
import {copyCurrent} from './steps/review.js';
import {STEPS, METHOD_LABELS} from './folds.js';
// Page-local backlog view state (open Markdown, last notice) per Flow; never stored or sent anywhere.
const VIEW = new WeakMap();
// 'in-progress' is an idea still being shaped in the wizard; 'moved' is one handed to a workspace (its file lives there now).
const LABELS = {'ready-to-plan': 'Ready to plan', 'review-needed': 'Review needed', 'in-progress': 'Ideation ongoing', archived: 'Archived', moved: 'In progress', delivered: 'Delivered'};
// Shown by default; the pointer statuses are opt-in chips.
const DEFAULT_ON = ['ready-to-plan', 'review-needed', 'in-progress', 'archived'];
const POINTER = new Set(['moved', 'delivered']);
const labelFor = item => item.status === 'delivered' && item.delivered_ref ? LABELS.delivered + ' · ' + item.delivered_ref : (LABELS[item.status] ?? item.status);
const isPointer = item => item.read_only === true || POINTER.has(item.status);
// The operator reads local time, never a raw UTC stamp.
const localTime = value => {
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? value : when.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
};
// A write that reaches a moved idea is refused; say where the idea lives now instead of a generic failure.
const movedText = error => {
  const home = error?.data?.home;
  return 'This idea moved to ' + (typeof home?.workspace_name === 'string' && home.workspace_name ? home.workspace_name : 'a workspace') +
    (typeof home?.file_path === 'string' && home.file_path ? ' (' + home.file_path + ')' : '') + '. Its file is read and edited there now; nothing was changed.';
};
export function render(ctx) {
  const {body, foot, flow, element, button, handle} = ctx;
  const blocked = () => !(ctx.isConnected ? ctx.isConnected() : ctx.connected) || flow.refreshUnauthorized ||
    typeof flow.api.csrf !== 'string' || !flow.api.csrf || flow.disposed || flow.paused || flow.busy ||
    Boolean(flow.pending) || (Boolean(flow.proposalPending) && !flow.waitingOnTerminal()) || flow.dirty.size > 0;
  const open = async (ideaId, copy = false) => {
    if (blocked()) return false;
    if (!await flow.selectIdea(ideaId) || flow.disposed || flow.state?.idea_id !== ideaId || blocked()) return false;
    if (!copy) return true;
    flow.current = 'review'; flow.onChange();
    if (!flow.canCopyHandoff()) {
      flow.message = 'Copy again is unavailable. Review the saved source and explicitly generate a current prompt.';
      flow.onChange(); return false;
    }
    return copyCurrent(ctx, flow.state.handoff);
  };
  const fresh = button('New idea', handle(() => open(null)), 'primary'); fresh.id = 'new-idea'; fresh.disabled = blocked(); foot.append(fresh);
  const back = button('Return to current idea', () => { if (!flow.disposed) { flow.view = 'workflow'; flow.onChange(); } });
  back.id = 'ideas-return'; back.disabled = flow.busy; foot.append(back);
  const refresh = button(flow.ideasLoading ? 'Loading…' : 'Refresh ideas', handle(() => flow.loadIdeas()));
  refresh.id = 'ideas-refresh'; refresh.disabled = !ctx.connected || flow.ideasLoading || flow.busy || flow.selectionUncertain; foot.append(refresh);
  if (flow.dirty.size || flow.pending || (flow.proposalPending && !flow.waitingOnTerminal()) || flow.paused) body.append(element('p', 'Your current answers and requests are preserved. Return to the current idea to save or resolve them before opening another idea.', 'notice'));
  if (flow.ideasError) { const error = element('p', 'Could not read the complete Ideas list: ' + flow.ideasError.code + '. No partial order is shown as current.', 'error-box'); error.setAttribute('role', 'alert'); body.append(error); }
  const ui = VIEW.get(flow) ?? {markdown: null, notice: '', moving: false, selected: null, search: '', statuses: new Set(DEFAULT_ON)};
  VIEW.set(flow, ui);
  const redraw = () => { if (!flow.disposed) flow.onChange(); };
  const notice = text => { ui.notice = text; redraw(); };
  const reload = async () => {
    // A move changes the backlog revision for everything, so the open idea's state is re-read too.
    const loaded = await flow.loadIdeas();
    if (loaded && flow.state?.idea_id && !flow.dirty.size && !flow.pending) { try { await flow.reloadKeepingAnswers(); flow.message = ''; } catch { /* the list is already current */ } }
    return loaded;
  };
  const move = async (item, position) => {
    const total = flow.ideas?.total ?? 0;
    if (blocked() || ui.moving) return false;
    if (!Number.isInteger(position) || position < 1 || position > total) { notice('Enter a position from 1 to ' + total + '.'); return false; }
    if (position === item.position) { notice('This idea is already at position ' + position + '.'); return false; }
    ui.moving = true; redraw();
    let outcome;
    try {
      await flow.api.rerank({request_id: flow.idFactory(), idea_id: item.idea_id, expected_backlog_revision: flow.ideas.backlog_revision, position, reason: null});
      outcome = 'Moved to position ' + position + '. If this idea was already assessed, re-accept its Assess step to confirm the new position.';
    } catch (error) {
      outcome = error?.code === 'stale_backlog' ? 'The backlog changed; check the order and move again.'
        : error?.code === 'idea_archived' ? 'An archived idea cannot be moved.'
        : error?.code === 'idea_moved' ? movedText(error) + ' It cannot be reordered here.'
        : 'Could not move this idea: ' + (error?.code ?? 'unknown_error') + '. The order is unchanged until the list is re-read.';
    }
    // The notice appears together with the re-read order, never over the old list.
    await reload(); ui.moving = false; ui.notice = outcome; redraw(); return true;
  };
  const showMarkdown = async item => {
    ui.markdown = {ideaId: item.idea_id, text: null, error: null, loading: true}; redraw();
    const asked = ui.markdown;  // a reply lands only on the panel that asked for it, if still open
    let next;
    try { const text = await flow.api.ideaMarkdown(item.idea_id); next = {ideaId: item.idea_id, text: String(text), error: null, loading: false}; }
    catch (error) {
      next = {ideaId: item.idea_id, text: null, loading: false,
        error: error?.code === 'not_found' ? 'This idea has no Markdown file yet.' : error?.code === 'idea_moved' ? movedText(error) : 'Could not read this idea’s Markdown: ' + (error?.code ?? 'unknown_error') + '.'};
    }
    if (ui.markdown !== asked) return false;
    ui.markdown = next;
    redraw();
  };
  const status = element('p', ui.notice, 'backlog-notice'); status.id = 'backlog-notice'; status.setAttribute('role', 'status'); status.hidden = !ui.notice;
  const list = element('tbody', '', 'backlog-rows'); list.id = 'ideas-list';
  if (flow.ideas) {
    const total = flow.ideas.total;
    const items = flow.ideas.ideas;
    if (!items.some(entry => entry.idea_id === ui.selected)) ui.selected = items[0]?.idea_id ?? null;
    body.append(element('p', (flow.ideasError ? 'Last verified list: ' : '') + total + (total === 1 ? ' idea' : ' ideas') + ' in your order. Move an idea to change its place.', 'help'), status);
    if (!total) body.append(element('p', 'No saved ideas.'));
    // Search and status chips only hide rows that are already loaded; positions, order and every request stay as they are.
    const rowsById = new Map(), chips = new Map(), counts = {};
    for (const entry of items) counts[entry.status] = (counts[entry.status] ?? 0) + 1;
    const empty = element('p', 'No ideas match this search and these statuses.', 'help'); empty.id = 'ideas-no-match'; empty.hidden = true;
    const applyFilter = () => {
      const needle = ui.search.trim().toLowerCase(); let shown = 0;
      for (const entry of items) {
        // Search reaches every row, including moved and delivered ones whose chip is off; the other chips still narrow it.
        const found = needle && (String(entry.title).toLowerCase().includes(needle) || String(entry.home?.workspace_name ?? '').toLowerCase().includes(needle));
        const visible = needle ? Boolean(found) && (POINTER.has(entry.status) || ui.statuses.has(entry.status)) : ui.statuses.has(entry.status);
        const row = rowsById.get(entry.idea_id); if (row) row.hidden = !visible; if (visible) shown++;
      }
      for (const [key, chip] of chips) chip.setAttribute('aria-pressed', ui.statuses.has(key) ? 'true' : 'false');
      empty.hidden = shown > 0 || !total;
    };
    if (total) {
      const bar = element('div', '', 'bw-toolbar backlog-toolbar'); bar.setAttribute('role', 'group'); bar.setAttribute('aria-label', 'Filter ideas');
      const search = element('input', '', 'bw-input'); search.type = 'search'; search.id = 'ideas-search'; search.value = ui.search; search.placeholder = 'Search ideas';
      search.setAttribute('aria-label', 'Search ideas'); search.setAttribute('autocomplete', 'off');
      search.addEventListener('input', () => { ui.search = search.value; applyFilter(); });
      bar.append(search, element('span', 'Status', 'bw-caption'));
      for (const key of Object.keys(LABELS)) {
        const chip = element('button', '', 'bw-chip'); chip.type = 'button'; chip.id = 'ideas-filter-' + key; chip.dataset.status = key;
        chip.append(element('b', LABELS[key]), element('span', String(counts[key] ?? 0)));
        chip.setAttribute('aria-label', LABELS[key] + ' (' + (counts[key] ?? 0) + ')');
        chip.addEventListener('click', () => { if (ui.statuses.has(key)) ui.statuses.delete(key); else ui.statuses.add(key); applyFilter(); });
        chips.set(key, chip); bar.append(chip);
      }
      body.append(bar, empty);
    }
    const table = element('table', '', 'bw-table backlog-table');
    const caption = element('caption', 'Ideas in accepted order', 'sr-only'); table.append(caption);
    const head = element('thead'); const headRow = element('tr');
    for (const [label, cls] of [['#', 'n'], ['Idea', ''], ['Status', ''], ['Method', ''], ['Steps saved', ''], ['Updated', 'n'], ['Order', 'sr-only']]) {
      const th = element('th', label, cls); th.scope = 'col'; headRow.append(th);
    }
    head.append(headRow); table.append(head);
    const select = entry => { ui.selected = entry.idea_id; ui.markdown = null; redraw(); };
    for (const item of items) {
      const row = element('tr', '', 'backlog-card'); row.dataset.ideaId = item.idea_id; row.dataset.status = item.status;
      row.setAttribute('aria-selected', item.idea_id === ui.selected ? 'true' : 'false');
      rowsById.set(item.idea_id, row);
      const cell = (text, cls = '', label = '') => { const td = element('td', text, cls); if (label) td.dataset.label = label; return td; };
      const num = cell(String(item.position), 'n ref', '#'); num.setAttribute('aria-label', 'Position ' + item.position);
      const titleCell = cell('', 't', 'Idea');
      const title = element('h2', '', 'backlog-title');
      const pick = element('button', item.title, 'backlog-select bw-btn bw-btn--ghost'); pick.type = 'button'; pick.id = 'backlog-select-' + item.idea_id; pick.dataset.ideaId = item.idea_id;
      pick.setAttribute('aria-pressed', item.idea_id === ui.selected ? 'true' : 'false');
      pick.addEventListener('click', () => select(item));
      title.append(pick); titleCell.append(title, element('div', shortId(item.idea_id), 'bw-meta'));
      const statusCell = cell('', '', 'Status'); statusCell.append(statusChip(element, item));
      const method = cell(METHOD_LABELS[item.method] ?? 'Not chosen', 'backlog-method', 'Method');
      const saved = cell('', '', 'Steps saved'); saved.append(savedBar(element, item));
      const updated = cell(shortDate(item.updated), 'n bw-meta', 'Updated');
      const order = cell('', 'backlog-order'); order.setAttribute('role', 'group'); order.setAttribute('aria-label', 'Order of ' + item.title);
      const movable = !blocked() && !ui.moving && item.status !== 'archived' && !isPointer(item);
      const up = button('↑', handle(() => move(item, item.position - 1)), 'bw-btn bw-btn--ghost'); up.id = 'backlog-up-' + item.idea_id; up.setAttribute('aria-label', 'Move ' + item.title + ' up'); up.disabled = !movable || item.position <= 1;
      const down = button('↓', handle(() => move(item, item.position + 1)), 'bw-btn bw-btn--ghost'); down.id = 'backlog-down-' + item.idea_id; down.setAttribute('aria-label', 'Move ' + item.title + ' down'); down.disabled = !movable || item.position >= total;
      if (!isPointer(item)) order.append(up, down);
      row.append(num, titleCell, statusCell, method, saved, updated, order);
      list.append(row);
    }
    table.append(list);
    const chosen = items.find(entry => entry.idea_id === ui.selected);
    const layout = element('div', '', 'backlog-layout');
    const wrap = element('div', '', 'backlog-tablewrap'); wrap.append(table); layout.append(wrap);
    if (chosen) layout.append(detail(chosen, total));
    if (total) body.append(layout);
    applyFilter();
  } else body.append(element('p', flow.ideasLoading ? 'Reading saved ideas…' : 'The complete Ideas list is unavailable.', 'notice'));

  function detail(item, total) {
    const panel = element('aside', '', 'bw-panel backlog-detail'); panel.id = 'backlog-detail'; panel.setAttribute('aria-labelledby', 'backlog-sel-title');
    panel.dataset.ideaId = item.idea_id;
    const top = element('div', '', 'bw-stack');
    const heading = element('h2', item.title, 'bw-heading'); heading.id = 'backlog-sel-title';
    top.append(element('span', 'Position ' + item.position + ' · ' + shortId(item.idea_id), 'bw-meta'), heading, statusChip(element, item));
    const pointer = isPointer(item);
    const resumable = !pointer && !['ready-to-plan', 'archived'].includes(item.status) && STEPS.some(step => step.key === item.current_step);
    const stepNo = resumable ? STEPS.findIndex(step => step.key === item.current_step) + 1 : 0;
    const facts = element('dl', '', 'bw-dl');
    const fact = (name, value) => { facts.append(element('dt', name), element('dd', value)); };
    fact('Method', METHOD_LABELS[item.method] ?? 'Not chosen');
    if (Number.isInteger(item.completed_steps)) fact('Saved', item.completed_steps + ' of 8 steps');
    fact('Updated', localTime(item.updated));
    if (pointer) {
      // Read-only: the idea's file now lives in the workspace, so nothing here can open, edit, move or reorder it.
      if (item.status === 'delivered' && item.delivered_ref) fact('Delivered', item.delivered_ref);
      const moved = element('p', 'Moved to ' + (item.home?.workspace_name ?? 'a workspace'), 'notice moved-note'); moved.id = 'backlog-moved-note';
      const where = element('p', item.home?.file_path ?? item.detail_path, 'bw-meta moved-path'); where.id = 'backlog-moved-path';
      panel.append(top, facts, moved, element('p', 'The file lives here, and this is where it is edited:', 'help'), where);
      return panel;
    }
    const actions = element('div', '', 'bw-row backlog-actions');
    const open_ = button(resumable ? 'Step ' + stepNo + ' of 8 · Resume' : 'Open', handle(() => open(item.idea_id)), resumable ? 'bw-btn bw-btn--secondary primary' : 'bw-btn bw-btn--secondary');
    open_.id = 'ideas-open-' + item.idea_id; open_.dataset.ideaId = item.idea_id; open_.disabled = blocked(); actions.append(open_);
    const again = button('Copy again', handle(() => open(item.idea_id, true)), 'bw-btn bw-btn--ghost');
    again.id = item.idea_id === flow.state?.idea_id ? 'ideas-copy-again' : 'ideas-copy-again-' + item.idea_id;
    again.dataset.ideaId = item.idea_id; again.disabled = blocked() || item.status !== 'ready-to-plan';
    if (item.status === 'ready-to-plan') actions.append(again); else { again.hidden = true; actions.append(again); }
    const md = button('Open Markdown', handle(() => showMarkdown(item)), 'bw-btn bw-btn--ghost'); md.id = 'backlog-md-' + item.idea_id; md.dataset.ideaId = item.idea_id;
    md.disabled = !(ctx.isConnected ? ctx.isConnected() : ctx.connected) || Boolean(ui.markdown?.loading); actions.append(md);
    const movable = !blocked() && !ui.moving && item.status !== 'archived';
    const mover = element('div', '', 'bw-row backlog-move'); mover.setAttribute('role', 'group'); mover.setAttribute('aria-label', 'Move ' + item.title);
    const input = element('input', '', 'bw-input bw-num'); input.type = 'number'; input.min = '1'; input.max = String(total); input.value = String(item.position);
    input.id = 'backlog-pos-' + item.idea_id; input.setAttribute('aria-label', 'Move ' + item.title + ' to position'); input.disabled = !movable;
    const go = button('Move', handle(() => move(item, Number(input.value))), 'bw-btn bw-btn--ghost'); go.id = 'backlog-move-' + item.idea_id; go.disabled = !movable;
    mover.append(element('span', 'Move to', 'bw-meta'), input, go);
    panel.append(top, facts, actions, mover);
    if (ui.markdown?.ideaId === item.idea_id) {
      const md_ = element('section', '', 'backlog-markdown'); md_.id = 'backlog-md-panel'; md_.setAttribute('aria-label', 'Markdown of ' + item.title);
      if (ui.markdown.loading) md_.append(element('p', 'Reading the Markdown…', 'help'));
      else if (ui.markdown.error) { const m = element('p', ui.markdown.error, 'notice'); m.setAttribute('role', 'status'); md_.append(m); }
      else { const pre = element('pre', ui.markdown.text, 'backlog-md-text'); pre.id = 'backlog-md-text'; pre.tabIndex = 0; md_.append(pre); }
      const close = button('Close', () => { ui.markdown = null; redraw(); }); close.id = 'backlog-md-close'; md_.append(close);
      panel.append(element('hr', '', 'g-divider'), md_);
    }
    return panel;
  }
}

const shortId = id => String(id).length > 14 ? String(id).slice(0, 9) + '…' + String(id).slice(-4) : String(id);
const shortDate = value => {
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? String(value) : when.toLocaleDateString(undefined, {day: 'numeric', month: 'short'});
};
// Status glyphs are drawn only where the browser can make SVG; the word always carries the meaning.
const SVG_NS = 'http://www.w3.org/2000/svg';
const GLYPHS = {
  'ready-to-plan': [['circle', {class: 'dot', cx: 7, cy: 7, r: 6.5}]],
  'in-progress': [['circle', {class: 'trk', cx: 7, cy: 7, r: 5.5}], ['circle', {class: 'arc', cx: 7, cy: 7, r: 5.5, 'stroke-dasharray': '17.28 34.56', transform: 'rotate(-90 7 7)'}]],
  'review-needed': [['circle', {class: 'trk', cx: 7, cy: 7, r: 5.5}], ['rect', {class: 'bang', x: 6.25, y: 3.6, width: 1.5, height: 4.4, rx: .5}], ['rect', {class: 'bang', x: 6.25, y: 9, width: 1.5, height: 1.5, rx: .5}]],
  moved: [['circle', {class: 'trk', cx: 7, cy: 7, r: 5.5}], ['path', {class: 'arc', d: 'M4.5 7h5M7.5 4.5L10 7l-2.5 2.5', fill: 'none'}]],
  delivered: [['circle', {class: 'dot', cx: 7, cy: 7, r: 6.5}], ['path', {class: 'tick', d: 'M4.2 7.2l2 2 3.6-4', fill: 'none'}]],
  archived: [['circle', {class: 'trk', cx: 7, cy: 7, r: 5.5}], ['rect', {class: 'bar', x: 5, y: 4.5, width: 1.5, height: 5, rx: .5}], ['rect', {class: 'bar', x: 7.5, y: 4.5, width: 1.5, height: 5, rx: .5}]],
};
const MODIFIER = {'review-needed': ' bw-status--attention', archived: ' bw-status--held', moved: ' bw-status--moved', delivered: ' bw-status--delivered'};
function glyph(status) {
  if (typeof document === 'undefined' || typeof document.createElementNS !== 'function') return null;
  const svg = document.createElementNS(SVG_NS, 'svg'); svg.setAttribute('viewBox', '0 0 14 14'); svg.setAttribute('aria-hidden', 'true');
  for (const [tag, attrs] of GLYPHS[status] ?? []) {
    const part = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs)) part.setAttribute(name, String(value));
    svg.append(part);
  }
  return svg;
}
function statusChip(element, item) {
  const status = item.status;
  const chip = element('span', '', 'bw-status status-chip' + (MODIFIER[status] ?? '')); chip.dataset.status = status;
  const mark = glyph(status); if (mark) chip.append(mark);
  chip.append(element('span', labelFor(item)));
  return chip;
}
function savedBar(element, item) {
  if (!Number.isInteger(item.completed_steps)) return element('span', '–', 'bw-progress');
  const wrap = element('span', '', 'bw-progress'), track = element('span', '', 'bw-progress__track'), fill = element('span', '', 'bw-progress__fill');
  if (fill.style) fill.style.width = Math.round(Math.min(8, Math.max(0, item.completed_steps)) / 8 * 100) + '%';
  track.append(fill);
  wrap.append(track, element('span', item.completed_steps + '/8'));
  return wrap;
}
