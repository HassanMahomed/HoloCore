/* HoloCore Board for Obsidian. Plain CommonJS, no build step.
 * Reads HoloCore data only by running `holocore board`; it never opens the database itself. */
'use strict';
const { Plugin, ItemView, PluginSettingTab, Setting, Notice, addIcon } = require('obsidian');
const { execFile } = require('child_process');

const VIEW_TYPE = 'holocore-board';
const ICON = 'holocore-board';
const DEFAULTS = { command: [], days: 7, project: '', hideNoise: true };
const PERIODS = [[1, 'Last 24 hours'], [7, 'Last 7 days'], [30, 'Last 30 days'], [0, 'All time']];
const COLUMNS = [
  { key: 'projects', title: 'Projects', empty: 'No projects in this period' },
  { key: 'sessions', title: 'AI sessions', empty: 'No AI sessions recorded in this period' },
  { key: 'memories', title: 'Memories', empty: 'No memories linked to these sessions' },
  { key: 'files', title: 'Files changed', empty: 'No file changes recorded' },
];
const AGENTS = { claude: 'Claude', codex: 'Codex', cursor: 'Cursor', gemini: 'Gemini', opencode: 'OpenCode' };

function ago(value) {
  const time = Date.parse(value || '');
  if (Number.isNaN(time)) return '';
  const minutes = Math.round((Date.now() - time) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 14) return `${days} d ago`;
  return new Date(time).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

function plural(count, word, many = `${word}s`) {
  return `${count} ${count === 1 ? word : many}`;
}

function runBoard(command, args) {
  return new Promise((resolve, reject) => {
    if (!command || !command.length) {
      reject(new Error('The HoloCore command is not set. Open Settings → HoloCore Board, or run HoloCore Setup again.'));
      return;
    }
    execFile(command[0], [...command.slice(1), 'board', ...args],
      { windowsHide: true, timeout: 120000, maxBuffer: 64 * 1024 * 1024 },
      (error, stdout, stderr) => {
        if (error && error.code === 'ENOENT') {
          reject(new Error(`HoloCore was not found at ${command[0]}. Run HoloCore Setup again, or set the program in Settings → HoloCore Board.`));
          return;
        }
        if (error) {
          reject(new Error((stderr || '').trim().split('\n').pop() || error.message));
          return;
        }
        try {
          resolve(JSON.parse(stdout));
        } catch (parseError) {
          reject(new Error('HoloCore returned data this plugin could not read. Update HoloCore and try again.'));
        }
      });
  });
}

class BoardView extends ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.data = null;
    this.selected = null;
    this.cards = new Map();
    this.loading = false;
  }

  getViewType() { return VIEW_TYPE; }
  getDisplayText() { return 'HoloCore board'; }
  getIcon() { return ICON; }

  async onOpen() {
    const root = this.contentEl;
    root.empty();
    root.addClass('holocore-board');
    this.buildToolbar(root.createDiv({ cls: 'hcb-toolbar' }));
    this.scroller = root.createDiv({ cls: 'hcb-scroller' });
    this.footer = root.createDiv({ cls: 'hcb-footer' });
    this.resizer = new ResizeObserver(() => this.drawLinks());
    this.resizer.observe(this.scroller);
    await this.refresh();
  }

  async onClose() {
    if (this.resizer) this.resizer.disconnect();
  }

  buildToolbar(bar) {
    const brand = bar.createDiv({ cls: 'hcb-brand' });
    brand.createDiv({ cls: 'hcb-mark' });
    const words = brand.createDiv();
    words.createDiv({ cls: 'hcb-title', text: 'HoloCore' });
    words.createDiv({ cls: 'hcb-subtitle', text: 'Projects → AI sessions → memories → files' });

    const settings = this.plugin.settings;
    this.projectSelect = bar.createEl('select', { cls: 'hcb-control', attr: { 'aria-label': 'Project' } });
    this.projectSelect.createEl('option', { text: 'All projects', value: '' });
    this.projectSelect.onchange = () => this.changeFilter({ project: this.projectSelect.value });

    const period = bar.createEl('select', { cls: 'hcb-control', attr: { 'aria-label': 'Period' } });
    for (const [days, label] of PERIODS) {
      const option = period.createEl('option', { text: label, value: String(days) });
      if (days === settings.days) option.selected = true;
    }
    period.onchange = () => this.changeFilter({ days: Number(period.value) });

    const noise = bar.createEl('label', { cls: 'hcb-toggle' });
    const box = noise.createEl('input', { type: 'checkbox' });
    box.checked = settings.hideNoise;
    noise.appendText('Hide noise');
    box.onchange = () => this.changeFilter({ hideNoise: box.checked });

    const refresh = bar.createEl('button', { cls: 'hcb-button', text: 'Refresh' });
    refresh.onclick = () => this.refresh();
    this.status = bar.createDiv({ cls: 'hcb-status' });
  }

  async changeFilter(change) {
    Object.assign(this.plugin.settings, change);
    await this.plugin.saveSettings();
    this.selected = null;
    await this.refresh();
  }

  async refresh() {
    if (this.loading) return;
    this.loading = true;
    const settings = this.plugin.settings;
    this.status.setText('Reading HoloCore…');
    this.scroller.toggleClass('hcb-busy', true);
    const args = ['--days', String(settings.days || 0), '--limit', '40'];
    if (settings.project) args.push('--project', settings.project);
    if (!settings.hideNoise) args.push('--show-noise');
    try {
      this.data = await runBoard(settings.command, args);
      this.render();
      const time = new Date(this.data.generated_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
      this.status.setText(`Updated ${time}`);
    } catch (error) {
      this.renderError(error);
      this.status.setText('Could not read HoloCore');
    } finally {
      this.loading = false;
      this.scroller.toggleClass('hcb-busy', false);
    }
  }

  renderError(error) {
    this.scroller.empty();
    this.footer.empty();
    const box = this.scroller.createDiv({ cls: 'hcb-message' });
    box.createEl('h3', { text: 'HoloCore could not be reached' });
    box.createEl('p', { text: error.message });
    box.createEl('p', { cls: 'hcb-muted', text: `Command: ${(this.plugin.settings.command || []).join(' ') || 'not set'}` });
  }

  render() {
    const data = this.data;
    const select = this.projectSelect;
    select.empty();
    select.createEl('option', { text: 'All projects', value: '' });
    for (const choice of data.project_choices || []) {
      const option = select.createEl('option', { text: choice.name, value: choice.id });
      if (choice.id === this.plugin.settings.project) option.selected = true;
    }

    this.scroller.empty();
    this.cards.clear();
    this.board = this.scroller.createDiv({ cls: 'hcb-board' });
    this.svg = this.board.createSvg('svg', { cls: 'hcb-links' });
    const defs = this.svg.createSvg('defs');
    // User-space units: a bounding-box gradient would make perfectly horizontal lines invisible.
    this.gradient = defs.createSvg('linearGradient', { attr: { id: 'hcb-copper', gradientUnits: 'userSpaceOnUse', x1: '0', y1: '0', y2: '0' } });
    const gradient = this.gradient;
    gradient.createSvg('stop', { attr: { offset: '0%', 'stop-color': '#C0521F' } });
    gradient.createSvg('stop', { attr: { offset: '100%', 'stop-color': '#F3B33D' } });
    this.board.onclick = (event) => { if (event.target === this.board || event.target === this.svg) this.select(null); };

    const ordered = this.order(data);
    COLUMNS.forEach((column, index) => {
      const items = ordered[column.key];
      const element = this.board.createDiv({ cls: `hcb-column hcb-column-${index}` });
      const head = element.createDiv({ cls: 'hcb-column-head' });
      head.createSpan({ text: column.title });
      head.createSpan({ cls: 'hcb-count', text: String(items.length) });
      if (!items.length) element.createDiv({ cls: 'hcb-empty', text: column.empty });
      for (const item of items) this.cards.set(item.id, this[`${column.key}Card`](element, item));
    });

    this.footer.empty();
    const hidden = data.unregistered_sessions || 0;
    if (!data.database_found) this.footer.setText('No HoloCore memories database was found in this Home yet.');
    else if (hidden) this.footer.setText(`${plural(hidden, 'session')} from folders that are not registered HoloCore projects are hidden.`);
    this.footer.toggleClass('is-empty', !this.footer.getText());
    this.links = data.links || [];
    window.requestAnimationFrame(() => { this.drawLinks(); this.applySelection(); });
  }

  /* Order cards so lines run mostly straight: sessions follow their project, memories and files their session. */
  order(data) {
    const projectIndex = new Map(data.projects.map((item, index) => [item.project, index]));
    const sessions = [...data.sessions].sort((a, b) =>
      (projectIndex.get(a.project) - projectIndex.get(b.project)) || (Date.parse(b.updated_at) - Date.parse(a.updated_at)));
    const rank = new Map();
    data.projects.forEach((item, index) => rank.set(item.id, index * 1000));
    sessions.forEach((item, index) => rank.set(item.id, projectIndex.get(item.project) * 1000 + index + 1));
    const firstSource = new Map();
    for (const link of data.links) if (!firstSource.has(link.target)) firstSource.set(link.target, rank.get(link.source) ?? 1e9);
    const byRank = (items) => [...items].sort((a, b) => (firstSource.get(a.id) ?? 1e9) - (firstSource.get(b.id) ?? 1e9));
    return { projects: data.projects, sessions, memories: byRank(data.memories), files: byRank(data.files) };
  }

  card(column, item, cls) {
    const card = column.createDiv({ cls: `hcb-card ${cls}` });
    card.dataset.id = item.id;
    card.tabIndex = 0;
    card.onclick = (event) => { event.stopPropagation(); this.select(this.selected === item.id ? null : item.id); };
    card.onkeydown = (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); card.click(); } };
    return card;
  }

  projectsCard(column, item) {
    const card = this.card(column, item, 'hcb-project');
    card.createDiv({ cls: 'hcb-card-title', text: item.name });
    card.createDiv({ cls: 'hcb-meta', text: `${plural(item.sessions, 'session')} here · ${item.all_sessions} recorded in total` });
    if (item.root) {
      const path = card.createDiv({ cls: 'hcb-path', text: item.root });
      path.title = item.root;
    }
    return card;
  }

  sessionsCard(column, item) {
    const card = this.card(column, item, `hcb-session is-${item.status}`);
    const top = card.createDiv({ cls: 'hcb-row' });
    top.createSpan({ cls: 'hcb-agent', text: AGENTS[item.agent] || item.agent });
    top.createSpan({ cls: `hcb-pill is-${item.status}`, text: item.status === 'completed' ? 'done' : item.status });
    top.createSpan({ cls: 'hcb-time', text: ago(item.updated_at) });
    const title = card.createDiv({ cls: 'hcb-card-text', text: item.title || 'No conversation text recorded' });
    if (!item.title) title.addClass('hcb-muted');
    title.title = item.title || '';
    const facts = [plural(item.actions, 'action')];
    facts.push(item.memories == null ? 'no transcript linked' : plural(item.memories, 'memory', 'memories'));
    if (item.subagents) facts.push(plural(item.subagents, 'subagent'));
    card.createDiv({ cls: 'hcb-meta', text: facts.join(' · ') });
    return card;
  }

  memoriesCard(column, item) {
    const card = this.card(column, item, 'hcb-memory');
    const top = card.createDiv({ cls: 'hcb-row' });
    top.createSpan({ cls: 'hcb-kind', text: item.kind });
    top.createSpan({ cls: 'hcb-time', text: ago(item.created_at) });
    const text = card.createDiv({ cls: 'hcb-card-text hcb-clamp-4', text: item.text });
    text.title = item.text;
    if (!item.session) card.createDiv({ cls: 'hcb-meta', text: 'From this project, not a session shown here' });
    return card;
  }

  filesCard(column, item) {
    const card = this.card(column, item, 'hcb-file');
    const top = card.createDiv({ cls: 'hcb-row' });
    top.createSpan({ cls: 'hcb-card-title', text: item.label });
    const open = top.createEl('button', { cls: 'hcb-open', text: 'Open', attr: { 'aria-label': `Open ${item.label}` } });
    open.onclick = (event) => { event.stopPropagation(); this.openFile(item.path); };
    const path = card.createDiv({ cls: 'hcb-path', text: item.path });
    path.title = item.path;
    card.createDiv({ cls: 'hcb-meta', text: plural(item.touches, 'recorded change') });
    return card;
  }

  async openFile(path) {
    const adapter = this.app.vault.adapter;
    const base = typeof adapter.getBasePath === 'function' ? adapter.getBasePath() : '';
    const normal = (value) => value.replace(/\\/g, '/').toLowerCase();
    if (base && normal(path).startsWith(normal(base) + '/')) {
      await this.app.workspace.openLinkText(path.slice(base.length + 1).replace(/\\/g, '/'), '', true);
      return;
    }
    try {
      const failure = await (window.require || require)('electron').shell.openPath(path);
      if (failure) new Notice(`Could not open ${path}: ${failure}`);
    } catch (error) {
      new Notice(`Could not open ${path}`);
    }
  }

  drawLinks() {
    if (!this.board || !this.svg || !this.links) return;
    const width = this.board.scrollWidth;
    const height = this.board.scrollHeight;
    this.svg.setAttr('width', String(width));
    this.svg.setAttr('height', String(height));
    this.svg.setAttr('viewBox', `0 0 ${width} ${height}`);
    this.gradient.setAttr('x2', String(width));
    this.svg.querySelectorAll('path').forEach((path) => path.remove());
    const origin = this.board.getBoundingClientRect();
    this.paths = [];
    for (const link of this.links) {
      const from = this.cards.get(link.source);
      const to = this.cards.get(link.target);
      if (!from || !to) continue;
      const a = from.getBoundingClientRect();
      const b = to.getBoundingClientRect();
      const x1 = a.right - origin.left;
      const y1 = a.top + a.height / 2 - origin.top;
      const x2 = b.left - origin.left;
      const y2 = b.top + b.height / 2 - origin.top;
      const bend = Math.max(40, (x2 - x1) * 0.5);
      const path = this.svg.createSvg('path', {
        cls: 'hcb-link',
        attr: { d: `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}` },
      });
      path.dataset.source = link.source;
      path.dataset.target = link.target;
      this.paths.push(path);
    }
    this.applySelection();
  }

  select(id) {
    this.selected = id;
    this.applySelection();
  }

  /* The selected card's whole chain: everything it came from and everything that came from it. */
  chain(id) {
    const keep = new Set([id]);
    const walk = (start, forward) => {
      const queue = [start];
      while (queue.length) {
        const current = queue.shift();
        for (const link of this.links) {
          const [from, to] = forward ? [link.source, link.target] : [link.target, link.source];
          if (from === current && !keep.has(to)) { keep.add(to); queue.push(to); }
        }
      }
    };
    walk(id, true);
    walk(id, false);
    return keep;
  }

  applySelection() {
    if (!this.board) return;
    const keep = this.selected ? this.chain(this.selected) : null;
    this.board.toggleClass('has-selection', Boolean(keep));
    for (const [id, card] of this.cards) {
      card.toggleClass('is-selected', id === this.selected);
      card.toggleClass('is-related', Boolean(keep && keep.has(id) && id !== this.selected));
    }
    for (const path of this.paths || []) {
      path.toggleClass('is-active', Boolean(keep && keep.has(path.dataset.source) && keep.has(path.dataset.target)));
    }
  }
}

class BoardSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    new Setting(containerEl)
      .setName('HoloCore program')
      .setDesc('HoloCore Setup fills this in. Change it only if you moved HoloCore. The plugin runs “<program> board” to read your projects, sessions, and memories.')
      .addText((text) => text
        .setPlaceholder('C:\\Users\\you\\AppData\\Local\\Programs\\HoloCore\\holocore.exe')
        .setValue((this.plugin.settings.command || []).join(' '))
        .onChange(async (value) => {
          const trimmed = value.trim();
          const current = this.plugin.settings.command || [];
          if (trimmed !== current.join(' ')) this.plugin.settings.command = trimmed ? [trimmed] : [];
          await this.plugin.saveSettings();
        }));
  }
}

module.exports = class HoloCoreBoardPlugin extends Plugin {
  async onload() {
    this.settings = Object.assign({}, DEFAULTS, await this.loadData());
    addIcon(ICON, '<g fill="none" stroke="currentColor" stroke-width="8" stroke-linecap="round">'
      + '<rect x="6" y="14" width="22" height="22" rx="5"/><rect x="39" y="14" width="22" height="22" rx="5"/>'
      + '<rect x="39" y="62" width="22" height="22" rx="5"/><rect x="72" y="38" width="22" height="22" rx="5"/>'
      + '<path d="M28 25h11M50 36v26M61 25c8 0 6 24 11 24M61 73c8 0 6-24 11-24"/></g>');
    this.registerView(VIEW_TYPE, (leaf) => new BoardView(leaf, this));
    this.addRibbonIcon(ICON, 'Open HoloCore board', () => this.activateView());
    this.addCommand({ id: 'open-board', name: 'Open board', callback: () => this.activateView() });
    this.addSettingTab(new BoardSettingTab(this.app, this));
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async activateView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = workspace.getLeaf('tab');
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    workspace.revealLeaf(leaf);
  }
};
