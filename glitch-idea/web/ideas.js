// Actual accepted order; paths/titles are literal evidence text.
import {copyCurrent} from './steps/review.js';
// Plain names duplicated from steps/method.js and folds.js on purpose: this module is loaded on its own
// (tests/web/backlog.test.mjs fails if they drift). Change the tentative APIV label in steps/method.js too.
const STEPS = ['capture', 'priorities', 'shape', 'method', 'visualize', 'assess', 'review'].map(key => ({key}));
const METHOD_LABELS = {'bounded-plan': 'Bounded plan (APIV)', 'adaptive-slices': 'Adaptive vertical slices',
  'appetite-led': 'Appetite-led shaping', 'experiment-led': 'Experiment-led discovery'};
// Page-local backlog view state (open Markdown, last notice) per Flow; never stored or sent anywhere.
const VIEW = new WeakMap();
const LABELS = {'ready-to-plan': 'Ready to plan', 'review-needed': 'Review needed', 'in-progress': 'In progress', archived: 'Archived'};
// The operator reads local time, never a raw UTC stamp.
const localTime = value => {
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? value : when.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
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
  const ui = VIEW.get(flow) ?? {markdown: null, notice: '', moving: false};
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
        error: error?.code === 'not_found' ? 'This idea has no Markdown file yet.' : 'Could not read this idea’s Markdown: ' + (error?.code ?? 'unknown_error') + '.'};
    }
    if (ui.markdown !== asked) return false;
    ui.markdown = next;
    redraw();
  };
  const list = element('ol', '', 'backlog-list'); list.id = 'ideas-list'; list.setAttribute('aria-label', 'Ideas in accepted order');
  const status = element('p', ui.notice, 'backlog-notice'); status.id = 'backlog-notice'; status.setAttribute('role', 'status'); status.hidden = !ui.notice;
  if (flow.ideas) {
    const total = flow.ideas.total;
    body.append(element('p', (flow.ideasError ? 'Last verified list: ' : '') + total + (total === 1 ? ' idea' : ' ideas') + ' in your order. Move an idea to change its place.', 'help'), status);
    if (!total) body.append(element('p', 'No saved ideas.'));
    for (const item of flow.ideas.ideas) {
      const row = element('li', '', 'backlog-card'); row.dataset.ideaId = item.idea_id; row.dataset.status = item.status;
      const head = element('div', '', 'backlog-head');
      const num = element('span', String(item.position), 'backlog-pos'); num.setAttribute('aria-label', 'Position ' + item.position);
      const title = element('h2', item.title, 'backlog-title');
      const chip = element('span', LABELS[item.status], 'status-chip'); chip.dataset.status = item.status;
      head.append(num, title, chip);
      const meta = element('p', 'Method: ' + (METHOD_LABELS[item.method] ?? 'Not chosen') + ' · Updated ' + localTime(item.updated), 'backlog-meta');
      const actions = element('div', '', 'backlog-actions');
      const resumable = !['ready-to-plan', 'archived'].includes(item.status) && STEPS.some(step => step.key === item.current_step);
      const stepNo = resumable ? STEPS.findIndex(step => step.key === item.current_step) + 1 : 0;
      const select = button(resumable ? 'Step ' + stepNo + ' of 7 · Resume' : 'Open', handle(() => open(item.idea_id)), resumable ? 'primary' : '');
      select.id = 'ideas-open-' + item.idea_id; select.dataset.ideaId = item.idea_id; select.disabled = blocked(); actions.append(select);
      const again = button('Copy again', handle(() => open(item.idea_id, true)));
      again.id = item.idea_id === flow.state?.idea_id ? 'ideas-copy-again' : 'ideas-copy-again-' + item.idea_id;
      again.dataset.ideaId = item.idea_id; again.disabled = blocked() || item.status !== 'ready-to-plan';
      if (item.status === 'ready-to-plan') actions.append(again); else { again.hidden = true; actions.append(again); }
      const md = button('Open Markdown', handle(() => showMarkdown(item))); md.id = 'backlog-md-' + item.idea_id; md.dataset.ideaId = item.idea_id;
      md.disabled = !(ctx.isConnected ? ctx.isConnected() : ctx.connected) || Boolean(ui.markdown?.loading); actions.append(md);
      row.append(head, meta, actions);
      const movable = !blocked() && !ui.moving && item.status !== 'archived';
      const mover = element('div', '', 'backlog-move'); mover.setAttribute('role', 'group'); mover.setAttribute('aria-label', 'Move ' + item.title);
      const up = button('Move up', handle(() => move(item, item.position - 1))); up.id = 'backlog-up-' + item.idea_id; up.disabled = !movable || item.position <= 1;
      const down = button('Move down', handle(() => move(item, item.position + 1))); down.id = 'backlog-down-' + item.idea_id; down.disabled = !movable || item.position >= total;
      const input = element('input'); input.type = 'number'; input.min = '1'; input.max = String(total); input.value = String(item.position);
      input.id = 'backlog-pos-' + item.idea_id; input.setAttribute('aria-label', 'Move ' + item.title + ' to position'); input.disabled = !movable;
      const go = button('Move', handle(() => move(item, Number(input.value)))); go.id = 'backlog-move-' + item.idea_id; go.disabled = !movable;
      mover.append(up, down, element('span', 'to position', 'help'), input, go); row.append(mover);
      if (ui.markdown?.ideaId === item.idea_id) {
        const panel = element('section', '', 'backlog-markdown'); panel.id = 'backlog-md-panel'; panel.setAttribute('aria-label', 'Markdown of ' + item.title);
        if (ui.markdown.loading) panel.append(element('p', 'Reading the Markdown…', 'help'));
        else if (ui.markdown.error) { const m = element('p', ui.markdown.error, 'notice'); m.setAttribute('role', 'status'); panel.append(m); }
        else { const pre = element('pre', ui.markdown.text, 'backlog-md-text'); pre.id = 'backlog-md-text'; pre.tabIndex = 0; panel.append(pre); }
        const close = button('Close', () => { ui.markdown = null; redraw(); }); close.id = 'backlog-md-close'; panel.append(close);
        row.append(panel);
      }
      list.append(row);
    }
  } else body.append(element('p', flow.ideasLoading ? 'Reading saved ideas…' : 'The complete Ideas list is unavailable.', 'notice'));
  body.append(list);
}
