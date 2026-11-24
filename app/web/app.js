/* Personal OS 前端：原生 JS，无构建工具。
   所有数据都走 /api/*，服务端 SQLite 是唯一真实数据源。 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ---------------------------------------------------------------- 工具 */
function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function todayStr() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function hhmm(iso) {
  if (!iso) return '--:--';
  return new Date(iso).toTimeString().slice(0, 5);
}

function fmtMinutes(total) {
  const minutes = Math.max(0, Math.round(total || 0));
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, '0')}m`;
}

const PRIORITY_LABEL = { 1: '高', 2: '中', 3: '低' };
const REPEAT_LABEL = { none: '', daily: '每天', weekdays: '工作日', weekly: '每周', monthly: '每月' };
const STATUS_LABEL = { active: '进行中', paused: '暂停', done: '已完成' };

function toast(message, isError = false) {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast show${isError ? ' err' : ''}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.className = 'toast'; }, 2600);
}

/* ---------------------------------------------------------------- 请求 */
async function call(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }

  if (!res.ok) {
    let detail = data && data.detail ? data.detail : `请求失败（${res.status}）`;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join('；');
    throw new Error(detail);
  }
  return data;
}

/* 统一的错误处理：弹 toast，控制台留堆栈 */
async function guard(fn) {
  try {
    return await fn();
  } catch (err) {
    console.error(err);
    toast(err.message || '操作失败', true);
    return undefined;
  }
}

/* ---------------------------------------------------------------- 弹窗 */
function askText(title, placeholder, value = '') {
  return new Promise((resolve) => {
    const dlg = $('#dialog');
    dlg.returnValue = '';  // 防止上一次的 ok 残留
    $('#dialog-title').textContent = title;
    const input = $('#dialog-input');
    input.placeholder = placeholder;
    input.value = value;
    const onClose = () => {
      dlg.removeEventListener('close', onClose);
      resolve(dlg.returnValue === 'ok' ? input.value.trim() : null);
    };
    dlg.addEventListener('close', onClose);
    dlg.showModal();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
}

function askTask(task) {
  return new Promise((resolve) => {
    const dlg = $('#task-dialog');
    dlg.returnValue = '';
    $('#edit-title').value = task.title || '';
    $('#edit-date').value = task.due_date || '';
    $('#edit-time').value = task.due_time || '';
    $('#edit-priority').value = String(task.priority || 2);
    $('#edit-repeat').value = task.repeat || 'none';
    $('#edit-notes').value = task.notes || '';
    fillProjectSelect($('#edit-project'), task.project_id, true);

    const onClose = () => {
      dlg.removeEventListener('close', onClose);
      if (dlg.returnValue !== 'ok') return resolve(null);
      resolve({
        title: $('#edit-title').value.trim(),
        due_date: $('#edit-date').value || null,
        due_time: $('#edit-time').value || null,
        priority: Number($('#edit-priority').value),
        repeat: $('#edit-repeat').value,
        notes: $('#edit-notes').value.trim() || null,
        project_id: $('#edit-project').value ? Number($('#edit-project').value) : null,
      });
    };
    dlg.addEventListener('close', onClose);
    dlg.showModal();
  });
}

/* ---------------------------------------------------------------- 状态 */
const state = {
  today: null,
  tasks: [],
  projects: [],
  taskFilter: 'open',
  tab: 'today',
};

/* ---------------------------------------------------------------- 导航 */
function switchTab(tab) {
  state.tab = tab;
  $$('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.tab === tab));
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `view-${tab}`));
  if (tab === 'tasks') loadTasks();
  if (tab === 'projects') loadProjects();
  if (tab === 'records') loadRecords();
  if (tab === 'today') loadToday();
}

/* ---------------------------------------------------------------- 今日 */
async function loadToday() {
  await guard(async () => {
    state.today = await call('GET', '/api/today');
    renderToday();
  });
}

function renderToday() {
  const data = state.today;
  if (!data) return;

  $('#today-date').textContent = data.date;
  $('#stat-worked').textContent = fmtMinutes(data.work.worked_minutes);
  $('#stat-open').textContent = String(data.tasks.open_count);
  $('#stat-shots').textContent = String(data.screenshots.count);
  $('#badge-tasks').textContent = String(data.tasks.open_count);

  // 打卡状态
  const clockedIn = data.work.clocked_in;
  $('#punch-state').textContent = clockedIn
    ? `上班中 · ${hhmm(data.work.session.clock_in)} 起`
    : (data.work.sessions.length ? '已下班' : '未上班');
  $('#punch-state').classList.toggle('on', clockedIn);
  const punchBtn = $('#btn-punch');
  punchBtn.textContent = clockedIn ? '下班打卡' : '上班打卡';
  punchBtn.classList.toggle('btn-primary', !clockedIn);
  punchBtn.classList.toggle('btn-danger', clockedIn);
  punchBtn.classList.toggle('btn', true);

  // 今天三件事
  const planBody = $('#plan-body');
  const plans = data.work.sessions.map((s) => s.plan).filter(Boolean);
  planBody.innerHTML = plans.length
    ? `<pre>${esc(plans[plans.length - 1])}</pre>`
    : '<p class="muted">还没有上班打卡，点左下角「上班打卡」写下今天三件事。</p>';

  // 任务分组
  const groups = [
    ['逾期', data.tasks.overdue, 'late'],
    ['今天到期', data.tasks.due_today, ''],
    ['高优先级', data.tasks.high, 'p1'],
    ['其他', data.tasks.others, ''],
    ['今日完成', data.tasks.completed_today, 'ok'],
  ];
  const html = groups
    .filter(([, items]) => items.length)
    .map(([label, items]) => `
      <div class="group-label">${label} · ${items.length}</div>
      ${items.map((t) => taskRow(t, { compact: true })).join('')}
    `).join('');
  $('#today-tasks').innerHTML = html || '<div class="empty">今天没有待办，爽。</div>';

  // 项目
  $('#today-projects').innerHTML = data.projects.length
    ? data.projects.map((p) => `
        <div class="row">
          <div class="row-main">
            <span class="row-title">${esc(p.name)}</span>
            <div class="row-meta">
              <span>${p.progress}%</span>
              <span>${p.next_step ? `下一步：${esc(p.next_step)}` : '没写下一步'}</span>
            </div>
            <div class="progress"><i style="width:${Math.min(100, Math.max(0, p.progress))}%"></i></div>
          </div>
        </div>
      `).join('')
    : '<div class="empty">还没有进行中的项目。</div>';
}

/* ---------------------------------------------------------------- 任务 */
function taskRow(task, opts = {}) {
  const late = task.due_date && task.due_date < todayStr() && !task.done;
  const repeat = REPEAT_LABEL[task.repeat] || '';
  const projectName = state.projects.find((p) => p.id === task.project_id)?.name;
  return `
    <div class="row" data-id="${task.id}">
      <button class="check ${task.done ? 'on' : ''}" data-act="toggle" title="完成/取消"></button>
      <div class="row-main">
        <span class="row-title ${task.done ? 'done' : ''}">${esc(task.title)}</span>
        <div class="row-meta">
          ${task.due_date ? `<span class="tag ${late ? 'late' : ''}">${task.due_date}${task.due_time ? ` ${task.due_time}` : ''}</span>` : ''}
          ${task.priority === 1 ? '<span class="tag p1">高</span>' : ''}
          ${repeat ? `<span class="tag">${repeat}</span>` : ''}
          ${projectName ? `<span>${esc(projectName)}</span>` : ''}
          ${opts.compact === false && task.notes ? `<span>${esc(task.notes)}</span>` : ''}
        </div>
      </div>
      <div class="row-actions">
        <button class="btn btn-sm" data-act="edit">编辑</button>
        <button class="btn btn-sm btn-danger" data-act="del">删除</button>
      </div>
    </div>
  `;
}

function bindTaskActions(container) {
  container.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-act]');
    if (!btn) return;
    const row = btn.closest('.row');
    const id = Number(row.dataset.id);
    const task = state.tasks.find((t) => t.id === id) || null;
    const act = btn.dataset.act;

    if (act === 'toggle') {
      const current = task ? task.done : row.querySelector('.check').classList.contains('on');
      await guard(async () => {
        await call('PATCH', `/api/tasks/${id}`, { done: !current });
        await refreshAll();
        toast(current ? '已取消完成' : '完成 ✔');
      });
    }
    if (act === 'edit') {
      if (!task) return;
      const payload = await askTask(task);
      if (!payload) return;
      await guard(async () => {
        await call('PATCH', `/api/tasks/${id}`, payload);
        await refreshAll();
        toast('已保存');
      });
    }
    if (act === 'del') {
      if (!confirm('删除这个任务？')) return;
      await guard(async () => {
        await call('DELETE', `/api/tasks/${id}`);
        await refreshAll();
        toast('已删除');
      });
    }
  });
}

async function loadTasks() {
  await guard(async () => {
    state.tasks = await call('GET', '/api/tasks');
    renderTasks();
  });
}

function renderTasks() {
  const filter = state.taskFilter;
  const items = state.tasks.filter((t) => (
    filter === 'open' ? !t.done : filter === 'done' ? t.done : true
  ));
  const list = $('#task-list');
  list.innerHTML = items.length
    ? items.map((t) => taskRow(t, { compact: false })).join('')
    : `<div class="empty">${filter === 'done' ? '还没有完成的任务' : '没有任务，先加一个吧'}</div>`;
}

async function addTask() {
  const title = $('#task-title').value.trim();
  if (!title) return toast('先写任务标题', true);
  const body = {
    title,
    due_date: $('#task-date').value || null,
    due_time: $('#task-time').value || null,
    priority: Number($('#task-priority').value),
    repeat: $('#task-repeat').value,
    project_id: $('#task-project').value ? Number($('#task-project').value) : null,
  };
  await guard(async () => {
    await call('POST', '/api/tasks', body);
    $('#task-title').value = '';
    await refreshAll();
    toast('任务已添加');
  });
}

/* ---------------------------------------------------------------- 项目 */
function fillProjectSelect(select, selected, withEmpty = false) {
  const options = state.projects.map(
    (p) => `<option value="${p.id}" ${p.id === selected ? 'selected' : ''}>${esc(p.name)}</option>`
  );
  select.innerHTML = (withEmpty ? '<option value="">（无项目）</option>' : '') + options.join('');
  if (withEmpty && !selected) select.value = '';
}

async function loadProjects() {
  await guard(async () => {
    state.projects = await call('GET', '/api/projects');
    // 每个项目顺带取最近日志，卡片上直接展示
    const logs = await Promise.all(
      state.projects.map((p) => call('GET', `/api/projects/${p.id}/logs?limit=5`).catch(() => []))
    );
    state.projects.forEach((p, i) => { p.logs = logs[i] || []; });
    renderProjects();
    fillProjectSelect($('#task-project'), null, true);
    $('#badge-tasks').textContent = state.today ? String(state.today.tasks.open_count) : '0';
  });
}

function renderProjects() {
  const wrap = $('#project-list');
  if (!state.projects.length) {
    wrap.innerHTML = '<div class="card"><div class="empty">还没有项目，先新建一个。</div></div>';
    return;
  }
  wrap.innerHTML = state.projects.map((p) => `
    <div class="card project-card" data-id="${p.id}">
      <div class="project-top">
        <div>
          <div class="project-name">${esc(p.name)}</div>
          <div class="project-step">${p.next_step ? `下一步：<b>${esc(p.next_step)}</b>` : '<span class="muted">还没写下一步</span>'}</div>
        </div>
        <select class="input" data-act="status" title="状态">
          ${Object.entries(STATUS_LABEL).map(([value, label]) =>
            `<option value="${value}" ${p.status === value ? 'selected' : ''}>${label}</option>`).join('')}
        </select>
      </div>

      <div>
        <div class="progress"><i style="width:${Math.min(100, Math.max(0, p.progress))}%"></i></div>
        <div class="project-nums">
          <span>进度 ${p.progress}%</span>
          <span>${p.deadline ? `截止 ${p.deadline}` : '无截止日'}${p.last_log_date ? ` · 最近记录 ${p.last_log_date}` : ' · 还没有记录'}</span>
        </div>
      </div>

      <div class="range-row">
        <input type="range" min="0" max="100" step="5" value="${p.progress}" data-act="progress" />
        <button class="btn btn-sm" data-act="edit">改信息</button>
        <button class="btn btn-sm btn-danger" data-act="del">删除</button>
      </div>

      <div class="logs">
        ${p.logs.length ? p.logs.map((log) => `
          <div class="log-item">
            <b>${log.date}</b>
            <span>${esc(log.text)}${log.minutes ? ` · ${log.minutes} 分钟` : ''}</span>
          </div>
        `).join('') : '<div class="log-item"><b>—</b><span class="muted">还没有进度日志</span></div>'}
        <div class="log-add">
          <input class="input grow" placeholder="今天推进了什么？" data-role="log-input" />
          <button class="btn btn-sm" data-act="log">记一笔</button>
        </div>
      </div>
    </div>
  `).join('');
}

function bindProjectActions() {
  const wrap = $('#project-list');
  wrap.addEventListener('change', async (event) => {
    const el = event.target.closest('[data-act]');
    if (!el) return;
    const id = Number(el.closest('.project-card').dataset.id);

    if (el.dataset.act === 'status') {
      await guard(async () => {
        await call('PATCH', `/api/projects/${id}`, { status: el.value });
        await refreshAll();
        toast('状态已更新');
      });
    }
    if (el.dataset.act === 'progress') {
      await guard(async () => {
        await call('PATCH', `/api/projects/${id}`, { progress: Number(el.value) });
        await refreshAll();
      });
    }
  });

  wrap.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-act]');
    if (!btn || btn.tagName === 'SELECT') return;
    const card = btn.closest('.project-card');
    const id = Number(card.dataset.id);
    const project = state.projects.find((p) => p.id === id);
    const act = btn.dataset.act;

    if (act === 'log') {
      const input = card.querySelector('[data-role="log-input"]');
      const text = input.value.trim();
      if (!text) return toast('先写点内容', true);
      await guard(async () => {
        await call('POST', `/api/projects/${id}/logs`, { text });
        await refreshAll();
        toast('已记录');
      });
    }
    if (act === 'edit') {
      const name = await askText('项目名称', '', project.name);
      if (name === null) return;
      const next = await askText('下一步做什么', '', project.next_step || '');
      if (next === null) return;
      await guard(async () => {
        await call('PATCH', `/api/projects/${id}`, { name: name || project.name, next_step: next || null });
        await refreshAll();
        toast('已保存');
      });
    }
    if (act === 'del') {
      if (!confirm(`删除项目「${project.name}」？项目和它的日志都会删除。`)) return;
      await guard(async () => {
        await call('DELETE', `/api/projects/${id}`);
        await refreshAll();
        toast('项目已删除');
      });
    }
  });
}

async function addProject() {
  const name = $('#project-name').value.trim();
  if (!name) return toast('先写项目名称', true);
  await guard(async () => {
    await call('POST', '/api/projects', {
      name,
      deadline: $('#project-deadline').value || null,
      next_step: $('#project-next').value.trim() || null,
    });
    $('#project-name').value = '';
    $('#project-next').value = '';
    await refreshAll();
    toast('项目已创建');
  });
}

/* ---------------------------------------------------------------- 打卡 */
async function punch() {
  const clockedIn = state.today && state.today.work.clocked_in;
  if (!clockedIn) {
    const plan = await askText('上班打卡', '今天三件事，一行一件', '');
    if (plan === null) return;
    await guard(async () => {
      await call('POST', '/api/clock/in', { plan: plan || null });
      await refreshAll();
      toast('上班打卡成功，好好干');
    });
  } else {
    const summary = await askText('下班打卡', '今天做完了什么？', '');
    if (summary === null) return;
    await guard(async () => {
      const session = await call('POST', '/api/clock/out', { summary: summary || null });
      await refreshAll();
      toast(`下班打卡成功，今天 ${fmtMinutes(session.worked_minutes)}`);
    });
  }
}

/* ---------------------------------------------------------------- 记录 */
async function loadRecords() {
  const date = $('#record-date').value || todayStr();
  await guard(async () => {
    const [sessions, shots] = await Promise.all([
      call('GET', `/api/sessions?date=${date}`),
      call('GET', `/api/screenshots?date=${date}`),
    ]);

    $('#record-sessions').innerHTML = sessions.length
      ? sessions.map((s) => `
          <div class="row">
            <div class="row-main">
              <span class="row-title">${hhmm(s.clock_in)} → ${s.clock_out ? hhmm(s.clock_out) : '进行中'}</span>
              <div class="row-meta">
                <span>${fmtMinutes(s.worked_minutes)}</span>
                ${s.plan ? `<span>计划：${esc(s.plan.replace(/\n/g, ' / '))}</span>` : ''}
                ${s.summary ? `<span>总结：${esc(s.summary)}</span>` : ''}
              </div>
            </div>
          </div>
        `).join('')
      : '<div class="empty">这一天没有打卡记录。</div>';

    $('#record-shots').innerHTML = shots.length
      ? shots.map((s) => `
          <div class="shot">
            <a href="/shots/${encodeURI(s.path)}" target="_blank">
              <img src="/shots/${encodeURI(s.path)}" alt="${s.taken_at}" loading="lazy" />
            </a>
            <span>${hhmm(s.taken_at)}</span>
          </div>
        `).join('')
      : '<div class="empty">这一天没有截图（自动截图从 M2 开始）。</div>';
  });
}

/* ---------------------------------------------------------------- 汇总刷新 */
async function refreshAll() {
  await loadProjects();
  await loadTasks();
  await loadToday();
  if (state.tab === 'records') loadRecords();
}

/* ---------------------------------------------------------------- 启动 */
function tickClock() {
  const now = new Date();
  $('#clock-now').textContent = now.toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  });
}

async function boot() {
  tickClock();
  setInterval(tickClock, 20000);

  $$('.nav-item').forEach((btn) => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));
  $$('[data-jump]').forEach((btn) => btn.addEventListener('click', () => switchTab(btn.dataset.jump)));

  $('#btn-punch').addEventListener('click', punch);

  $('#task-add').addEventListener('click', addTask);
  $('#task-title').addEventListener('keydown', (e) => { if (e.key === 'Enter') addTask(); });
  $('#project-add').addEventListener('click', addProject);
  $('#project-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') addProject(); });

  $('#task-filter').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-filter]');
    if (!btn) return;
    state.taskFilter = btn.dataset.filter;
    $$('#task-filter .seg-item').forEach((el) => el.classList.toggle('active', el === btn));
    renderTasks();
  });

  $('#record-date').addEventListener('change', loadRecords);

  bindTaskActions($('#task-list'));
  bindTaskActions($('#today-tasks'));
  bindProjectActions();

  $('#record-date').value = todayStr();

  await loadProjects();
  await loadTasks();
  await loadToday();
}

boot();
