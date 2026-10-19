/**
 * Hub for Google Apps Script.
 *
 * Your data lives in a "Hub data" spreadsheet in your Drive, and attachments in a
 * "Hub files" folder. Both are created the first time you open the web app.
 * Deploy as a web app: Execute as "Me", Who has access "Only myself".
 */

const TZ = 'America/New_York';
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const MAX_FEED_BYTES = 5 * 1024 * 1024;
const LOOKBACK_DAYS = 14; // skip Schoology assignments due longer ago than this
const SCHOOLOGY_HOSTS = ['schoology.com', 'fcps.edu'];

// Column types for each sheet. A "?" means the value can be empty.
const SCHEMA = {
  projects: { id: 'int', name: 'str', color: 'str', status: 'str', next_step: 'str', notes: 'str', created_at: 'str' },
  tasks: {
    id: 'int', project_id: 'int?', title: 'str', priority: 'int', due: 'str?', today: 'bool', done: 'bool',
    created_at: 'str', completed_at: 'str?', source: 'str?', link: 'str?',
  },
  attachments: {
    id: 'int', task_id: 'int?', project_id: 'int?', filename: 'str', content_type: 'str', size: 'int',
    file_id: 'str', created_at: 'str',
  },
  sync_items: { uid: 'str', task_id: 'int?' },
};

// ---------------------------------------------------------------- web app

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Hub')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

class HubError extends Error {}

function fail_(msg) {
  throw new HubError(msg);
}

/**
 * One entry point that mirrors the hub's web API, so the page works the same either way.
 * Returns a JSON string (or null) because google.script.run can't pass Dates.
 */
function api(method, path, bodyJson) {
  const body = bodyJson ? JSON.parse(bodyJson) : {};
  const [rawPath, qs] = String(path).split('?');
  const query = {};
  (qs || '').split('&').filter(Boolean).forEach(pair => {
    const [k, v] = pair.split('=');
    query[decodeURIComponent(k)] = decodeURIComponent(v || '');
  });
  const parts = rawPath.replace(/^\/api\//, '').split('/').filter(Boolean);
  const writes = method !== 'GET';
  const lock = LockService.getUserLock();
  if (writes) lock.waitLock(20000);
  try {
    const out = route_(method, parts, query, body);
    return out === undefined || out === null ? null : JSON.stringify(out);
  } finally {
    if (writes) lock.releaseLock();
  }
}

function route_(method, parts, query, body) {
  const [res, id, sub] = parts;
  const num = id !== undefined ? Number(id) : undefined;
  const key = `${method} ${res}${id !== undefined ? (res === 'schoology' ? '/' + id : '/:id') : ''}${sub ? '/' + sub : ''}`;
  switch (key) {
    case 'GET projects': return listProjects_();
    case 'POST projects': return createProject_(body);
    case 'PATCH projects/:id': return updateProject_(num, body);
    case 'DELETE projects/:id': return deleteProject_(num);
    case 'GET tasks': return listTasks_(query);
    case 'POST tasks': return createTask_(body);
    case 'PATCH tasks/:id': return updateTask_(num, body);
    case 'DELETE tasks/:id': return deleteTask_(num);
    case 'GET attachments': return listAttachments_(query);
    case 'DELETE attachments/:id': return deleteAttachment_(num);
    case 'GET schoology': return schoologyStatus_();
    case 'PUT schoology': return schoologyConnect_(body);
    case 'DELETE schoology': return schoologyDisconnect_();
    case 'POST schoology/sync': return schoologySync_();
    default: fail_(`Unknown request: ${method} /api/${parts.join('/')}`);
  }
}

// ---------------------------------------------------------------- storage

function props_() {
  return PropertiesService.getUserProperties();
}

let DB_CACHE_ = null;

function db_() {
  if (!DB_CACHE_) DB_CACHE_ = new Db_();
  return DB_CACHE_;
}

class Db_ {
  constructor() {
    this.cache = {};
    this.ss = this.tryOpen_();
    const ready = this.ss && Object.keys(SCHEMA).every(n => this.ss.getSheetByName(n));
    if (!ready) {
      // First run (or the sheet was deleted). The page makes several calls at once,
      // so only one of them may create the spreadsheet and its tabs.
      const lock = LockService.getScriptLock();
      lock.waitLock(30000);
      try {
        this.ss = this.tryOpen_() || this.create_();
        Object.keys(SCHEMA).forEach(name => this.ensureSheet_(name));
      } finally {
        lock.releaseLock();
      }
    }
  }

  tryOpen_() {
    const id = props_().getProperty('hub_sheet_id');
    if (!id) return null;
    try {
      return SpreadsheetApp.openById(id);
    } catch (e) {
      return null; // deleted or moved to trash
    }
  }

  create_() {
    const ss = SpreadsheetApp.create('Hub data');
    props_().setProperty('hub_sheet_id', ss.getId());
    return ss;
  }

  ensureSheet_(name) {
    let sheet = this.ss.getSheetByName(name);
    const cols = Object.keys(SCHEMA[name]);
    if (!sheet) {
      sheet = this.ss.insertSheet(name);
      sheet.getRange(1, 1, 1, cols.length).setNumberFormat('@').setValues([cols]);
      sheet.setFrozenRows(1);
      // a brand-new spreadsheet starts with an empty "Sheet1"
      const blank = this.ss.getSheetByName('Sheet1');
      if (blank && this.ss.getSheets().length > 1) this.ss.deleteSheet(blank);
    }
    return sheet;
  }

  load(name) {
    if (this.cache[name]) return this.cache[name];
    const types = SCHEMA[name];
    const cols = Object.keys(types);
    const values = this.ss.getSheetByName(name).getDataRange().getValues();
    const header = values[0].map(String);
    const rows = values.slice(1).filter(r => r.some(v => v !== '' && v !== null)).map(r => {
      const obj = {};
      cols.forEach(c => {
        const i = header.indexOf(c);
        obj[c] = fromCell_(i < 0 ? '' : r[i], types[c]);
      });
      return obj;
    });
    this.cache[name] = rows;
    return rows;
  }

  save(name, rows) {
    const types = SCHEMA[name];
    const cols = Object.keys(types);
    const sheet = this.ss.getSheetByName(name);
    const data = [cols].concat(rows.map(r => cols.map(c => toCell_(r[c]))));
    const need = data.length;
    if (sheet.getMaxRows() < need) sheet.insertRowsAfter(sheet.getMaxRows(), need - sheet.getMaxRows());
    sheet.clearContents();
    sheet.getRange(1, 1, need, cols.length).setNumberFormat('@').setValues(data);
    this.cache[name] = rows;
  }

  nextId(name) {
    const keyName = `next_id_${name}`;
    let next = Number(props_().getProperty(keyName) || 0);
    const max = this.load(name).reduce((m, r) => Math.max(m, r.id || 0), 0);
    // never reuse an id, even after deletes
    next = Math.max(next, max + 1);
    props_().setProperty(keyName, String(next + 1));
    return next;
  }
}

function fromCell_(v, type) {
  const optional = type.endsWith('?');
  const base = type.replace('?', '');
  if (v === '' || v === null || v === undefined) {
    if (optional) return null;
    if (base === 'bool') return false;
    if (base === 'int') return 0;
    return '';
  }
  if (base === 'int') return Number(v);
  if (base === 'bool') return v === true || String(v).toLowerCase() === 'true';
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'yyyy-MM-dd');
  return String(v);
}

function toCell_(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  const s = String(v);
  // keep text like "=2+2" or "+1 bonus" from turning into a formula
  return /^[=+]/.test(s) ? "'" + s : s;
}

function nowIso_() {
  return new Date().toISOString();
}

function todayLocal_() {
  return Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
}

function find_(rows, id, label) {
  const row = rows.find(r => r.id === id);
  if (!row) fail_(`${label} not found`);
  return row;
}

// ---------------------------------------------------------------- validation

function checkText_(v, label, max, required) {
  if (v === undefined || v === null) {
    if (required) fail_(`${label} is required`);
    return v;
  }
  const s = String(v);
  if (required && !s.trim()) fail_(`${label} can't be empty`);
  if (s.length > max) fail_(`${label} is too long (max ${max} characters)`);
  return s;
}

function checkStatus_(v) {
  if (!['active', 'paused', 'done'].includes(v)) fail_('Status must be active, paused or done');
  return v;
}

function checkPriority_(v) {
  const n = Number(v);
  if (![0, 1, 2].includes(n)) fail_('Priority must be 0, 1 or 2');
  return n;
}

function checkDate_(v) {
  if (v === null || v === undefined || v === '') return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(v))) fail_('Dates must look like 2026-10-31');
  return String(v);
}

// ---------------------------------------------------------------- projects

function projectOut_(p, tasks) {
  return Object.assign({}, p, { open_tasks: tasks.filter(t => t.project_id === p.id && !t.done).length });
}

function listProjects_() {
  const tasks = db_().load('tasks');
  return db_().load('projects')
    .slice()
    .sort((a, b) => a.created_at.localeCompare(b.created_at))
    .map(p => projectOut_(p, tasks));
}

function createProject_(b) {
  const db = db_();
  const p = {
    id: db.nextId('projects'),
    name: checkText_(b.name, 'Name', 120, true),
    color: checkText_(b.color, 'Color', 16, false) || '#5b8def',
    status: checkStatus_(b.status || 'active'),
    next_step: checkText_(b.next_step, 'Next step', 2000, false) || '',
    notes: checkText_(b.notes, 'Notes', 20000, false) || '',
    created_at: nowIso_(),
  };
  db.save('projects', db.load('projects').concat([p]));
  return projectOut_(p, db.load('tasks'));
}

function updateProject_(id, b) {
  const db = db_();
  const rows = db.load('projects');
  const p = find_(rows, id, 'Project');
  if ('name' in b) p.name = checkText_(b.name, 'Name', 120, true);
  if ('color' in b) p.color = checkText_(b.color, 'Color', 16, true);
  if ('status' in b) p.status = checkStatus_(b.status);
  if ('next_step' in b) p.next_step = checkText_(b.next_step, 'Next step', 2000, false) || '';
  if ('notes' in b) p.notes = checkText_(b.notes, 'Notes', 20000, false) || '';
  db.save('projects', rows);
  return projectOut_(p, db.load('tasks'));
}

function deleteProject_(id) {
  const db = db_();
  const projects = db.load('projects');
  find_(projects, id, 'Project');
  const taskIds = db.load('tasks').filter(t => t.project_id === id).map(t => t.id);
  removeAttachments_(a => a.project_id === id || taskIds.includes(a.task_id));
  forgetTasks_(taskIds);
  db.save('tasks', db.load('tasks').filter(t => t.project_id !== id));
  db.save('projects', projects.filter(p => p.id !== id));
  return null;
}

// ---------------------------------------------------------------- tasks

function listTasks_(q) {
  const doneDays = q.done_days !== undefined ? Number(q.done_days) : 7;
  const cutoff = new Date(Date.now() - doneDays * 86400000).toISOString();
  let rows = db_().load('tasks').filter(t => !t.done || (t.completed_at && t.completed_at >= cutoff));
  if (q.project_id) rows = rows.filter(t => t.project_id === Number(q.project_id));
  return rows.slice().sort((a, b) =>
    (a.done - b.done) ||
    (b.priority - a.priority) ||
    ((a.due === null) - (b.due === null)) ||
    String(a.due || '').localeCompare(String(b.due || '')) ||
    a.created_at.localeCompare(b.created_at));
}

function checkProjectId_(v) {
  if (v === null || v === undefined || v === '') return null;
  find_(db_().load('projects'), Number(v), 'Project');
  return Number(v);
}

function createTask_(b) {
  const db = db_();
  const t = {
    id: db.nextId('tasks'),
    project_id: checkProjectId_(b.project_id),
    title: checkText_(b.title, 'Title', 300, true),
    priority: checkPriority_(b.priority === undefined ? 1 : b.priority),
    due: checkDate_(b.due),
    today: !!b.today,
    done: false,
    created_at: nowIso_(),
    completed_at: null,
    source: null,
    link: null,
  };
  db.save('tasks', db.load('tasks').concat([t]));
  return t;
}

function updateTask_(id, b) {
  const db = db_();
  const rows = db.load('tasks');
  const t = find_(rows, id, 'Task');
  if ('title' in b) t.title = checkText_(b.title, 'Title', 300, true);
  if ('project_id' in b) t.project_id = checkProjectId_(b.project_id);
  if ('priority' in b) t.priority = checkPriority_(b.priority);
  if ('due' in b) t.due = checkDate_(b.due);
  if ('today' in b) t.today = !!b.today;
  if ('done' in b && !!b.done !== t.done) {
    t.done = !!b.done;
    t.completed_at = t.done ? nowIso_() : null;
  }
  db.save('tasks', rows);
  return t;
}

function deleteTask_(id) {
  const db = db_();
  const rows = db.load('tasks');
  find_(rows, id, 'Task');
  removeAttachments_(a => a.task_id === id);
  forgetTasks_([id]);
  db.save('tasks', rows.filter(t => t.id !== id));
  return null;
}

// Imported tasks you delete stay deleted: keep the event ID, drop the task link
function forgetTasks_(taskIds) {
  if (!taskIds.length) return;
  const items = db_().load('sync_items');
  let changed = false;
  items.forEach(s => {
    if (taskIds.includes(s.task_id)) {
      s.task_id = null;
      changed = true;
    }
  });
  if (changed) db_().save('sync_items', items);
}

// ---------------------------------------------------------------- attachments

function filesFolder_() {
  const id = props_().getProperty('hub_folder_id');
  if (id) {
    try {
      const f = DriveApp.getFolderById(id);
      if (!f.isTrashed()) return f;
    } catch (e) {
      // fall through and recreate
    }
  }
  const folder = DriveApp.createFolder('Hub files');
  props_().setProperty('hub_folder_id', folder.getId());
  return folder;
}

function attachmentOut_(a) {
  return Object.assign({}, a, { url: `https://drive.google.com/file/d/${a.file_id}/view` });
}

function listAttachments_(q) {
  let rows = db_().load('attachments');
  if (q.task_id) rows = rows.filter(a => a.task_id === Number(q.task_id));
  if (q.project_id) rows = rows.filter(a => a.project_id === Number(q.project_id));
  return rows.slice().sort((a, b) => a.created_at.localeCompare(b.created_at)).map(attachmentOut_);
}

// Detect the real file type from its first bytes instead of trusting the browser
function sniffType_(bytes) {
  const u = i => (bytes[i] + 256) % 256;
  const starts = sig => sig.every((b, i) => u(i) === b);
  if (starts([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return 'image/png';
  if (starts([0xff, 0xd8, 0xff])) return 'image/jpeg';
  if (starts([0x47, 0x49, 0x46, 0x38])) return 'image/gif';
  if (starts([0x25, 0x50, 0x44, 0x46, 0x2d])) return 'application/pdf';
  if (starts([0x52, 0x49, 0x46, 0x46]) && [0x57, 0x45, 0x42, 0x50].every((b, i) => u(8 + i) === b)) return 'image/webp';
  return null;
}

/** Called from the page with the file as base64. kind is "task" or "project". */
function uploadAttachment(kind, targetId, filename, base64) {
  const lock = LockService.getUserLock();
  lock.waitLock(20000);
  try {
    const db = db_();
    const id = Number(targetId);
    if (kind === 'task') find_(db.load('tasks'), id, 'Task');
    else if (kind === 'project') find_(db.load('projects'), id, 'Project');
    else fail_('Attach the file to either a task or a project');

    const bytes = Utilities.base64Decode(base64);
    if (bytes.length > MAX_UPLOAD_BYTES) fail_('File is over 10 MB. Try a smaller photo or PDF.');
    const type = sniffType_(bytes);
    if (!type) fail_('Only photos (PNG, JPG, GIF, WebP) and PDFs can be attached');

    const name = String(filename || 'file').split(/[\\/]/).pop().slice(0, 200) || 'file';
    const file = filesFolder_().createFile(Utilities.newBlob(bytes, type, name));
    const a = {
      id: db.nextId('attachments'),
      task_id: kind === 'task' ? id : null,
      project_id: kind === 'project' ? id : null,
      filename: name,
      content_type: type,
      size: bytes.length,
      file_id: file.getId(),
      created_at: nowIso_(),
    };
    db.save('attachments', db.load('attachments').concat([a]));
    return JSON.stringify(attachmentOut_(a));
  } finally {
    lock.releaseLock();
  }
}

/** The page asks for a file's bytes to show a thumbnail or the full photo. */
function getAttachmentData(id) {
  const a = find_(db_().load('attachments'), Number(id), 'Attachment');
  const blob = DriveApp.getFileById(a.file_id).getBlob();
  return JSON.stringify({ type: a.content_type, data: Utilities.base64Encode(blob.getBytes()) });
}

function deleteAttachment_(id) {
  find_(db_().load('attachments'), id, 'Attachment');
  removeAttachments_(a => a.id === id);
  return null;
}

function removeAttachments_(match) {
  const db = db_();
  const rows = db.load('attachments');
  const gone = rows.filter(match);
  if (!gone.length) return;
  gone.forEach(a => {
    try {
      DriveApp.getFileById(a.file_id).setTrashed(true);
    } catch (e) {
      // already gone from Drive; just drop the record
    }
  });
  db.save('attachments', rows.filter(a => !match(a)));
}

// ---------------------------------------------------------------- Schoology

function schoologyStatus_() {
  return {
    connected: !!props_().getProperty('schoology_url'),
    last_sync: props_().getProperty('schoology_last_sync') || null,
  };
}

function hostOf_(url) {
  const m = /^https:\/\/([^/:?#]+)/i.exec(url);
  return m ? m[1].toLowerCase() : null;
}

function normalizeUrl_(raw) {
  let url = String(raw || '').trim();
  if (/^webcal:\/\//i.test(url)) url = 'https://' + url.slice('webcal://'.length);
  const host = hostOf_(url);
  if (!host) fail_("That doesn't look like a calendar link. Copy the whole link from Schoology Settings.");
  if (!SCHOOLOGY_HOSTS.some(h => host === h || host.endsWith('.' + h))) {
    fail_(`Only Schoology calendar links are allowed (got ${host}).`);
  }
  const path = url.slice(('https://' + host).length);
  if (path.indexOf('/calendar/') < 0) {
    fail_("That isn't a Schoology calendar link. Use the iCal link from Schoology Settings.");
  }
  return url;
}

function schoologyConnect_(b) {
  const url = normalizeUrl_(checkText_(b.url, 'Link', 600, true));
  props_().setProperty('schoology_url', url);
  props_().deleteProperty('schoology_last_sync');
  return schoologyStatus_();
}

function schoologyDisconnect_() {
  // imported tasks stay; only the link is forgotten
  props_().deleteProperty('schoology_url');
  props_().deleteProperty('schoology_last_sync');
  return null;
}

function fetchFeed_(startUrl) {
  let url = normalizeUrl_(startUrl);
  for (let hop = 0; hop < 5; hop++) {
    let res;
    try {
      res = UrlFetchApp.fetch(url, {
        followRedirects: false,
        muteHttpExceptions: true,
        headers: { 'User-Agent': 'Hub calendar sync' },
      });
    } catch (e) {
      fail_("Couldn't reach Schoology. Try again in a minute.");
    }
    const code = res.getResponseCode();
    if (code >= 300 && code < 400) {
      const headers = res.getHeaders();
      const loc = headers.Location || headers.location;
      if (!loc) fail_('Schoology sent a broken redirect.');
      // every redirect must stay on an allowed host
      url = normalizeUrl_(/^https?:\/\//i.test(loc) ? loc : 'https://' + hostOf_(url) + loc);
      continue;
    }
    if (code === 401 || code === 403 || code === 404) {
      fail_('Schoology turned down the link. It may have been reset. Copy a fresh one from Settings.');
    }
    if (code >= 400) fail_("Couldn't reach Schoology. Try again in a minute.");
    const blob = res.getBlob();
    if (blob.getBytes().length > MAX_FEED_BYTES) fail_('The calendar feed is too large to import.');
    return res.getContentText();
  }
  fail_('Schoology redirected too many times.');
}

function icsUnescape_(s) {
  return s.replace(/\\([nN,;\\])/g, (m, c) => (c === 'n' || c === 'N' ? '\n' : c));
}

/** Small iCalendar reader: just what the Schoology feed needs. */
function parseIcs_(text) {
  if (!/BEGIN:VCALENDAR/.test(text)) fail_("Schoology sent something that isn't a calendar. Check the link.");
  const lines = text.replace(/\r\n?/g, '\n').replace(/\n[ \t]/g, '').split('\n');
  const events = [];
  const fields = {};
  let cur = null;
  lines.forEach(line => {
    if (/^BEGIN:(VEVENT|VTODO)$/i.test(line)) { cur = {}; return; }
    if (/^END:(VEVENT|VTODO)$/i.test(line)) { if (cur) events.push(cur); cur = null; return; }
    if (!cur) return;
    const colon = line.indexOf(':');
    if (colon < 0) return;
    const [name, ...params] = line.slice(0, colon).split(';');
    const key = name.toUpperCase();
    fields[key] = true;
    if (cur[key]) return; // first value wins
    const p = {};
    params.forEach(x => { const [k, v] = x.split('='); p[k.toUpperCase()] = v; });
    cur[key] = { params: p, value: line.slice(colon + 1) };
  });

  const out = [];
  events.forEach(e => {
    const text = k => (e[k] ? icsUnescape_(e[k].value).trim() : '');
    const uid = text('UID');
    const title = text('SUMMARY');
    if (!uid || !title) return;
    out.push({
      uid: uid.slice(0, 300),
      title: title.slice(0, 300),
      due: icsDate_(e.DTSTART || e.DUE),
      course: icsCourse_(e, text),
      link: icsLink_(text),
    });
  });
  return { events: out, fields: Object.keys(fields).sort() };
}

function icsDate_(prop) {
  if (!prop) return null;
  const v = prop.value.trim();
  const m = /^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?/.exec(v);
  if (!m) return null;
  if (m[7] === 'Z') {
    // a UTC time: convert to the local school day (11:59 PM here can be the next day in UTC)
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
    return Utilities.formatDate(d, TZ, 'yyyy-MM-dd');
  }
  return `${m[1]}-${m[2]}-${m[3]}`;
}

function icsCourse_(e, text) {
  for (const k of ['CATEGORIES', 'LOCATION', 'X-SCHOOLOGY-COURSE']) {
    const v = text(k);
    if (v) return v.slice(0, 120);
  }
  const m = /^(?:course|section|class)\s*:\s*(.+)$/im.exec(text('DESCRIPTION'));
  return m ? m[1].trim().slice(0, 120) : '';
}

function icsLink_(text) {
  let url = text('URL');
  if (!url) {
    const m = /https:\/\/\S+/.exec(text('DESCRIPTION'));
    url = m ? m[0].replace(/[).,]+$/, '') : '';
  }
  return url.indexOf('https://') === 0 ? url.slice(0, 500) : null;
}

function applyEvents_(events, today) {
  const db = db_();
  const cutoff = Utilities.formatDate(new Date(new Date(today + 'T12:00:00Z').getTime() - LOOKBACK_DAYS * 86400000), 'UTC', 'yyyy-MM-dd');
  const projects = db.load('projects');
  const byName = {};
  projects.forEach(p => { byName[p.name.toLowerCase()] = p; });
  const tasks = db.load('tasks');
  const items = db.load('sync_items');
  const known = {};
  items.forEach(s => { known[s.uid] = s; });
  const seen = {};
  let added = 0, updated = 0, skipped = 0;
  const newProjects = [];

  events.forEach(ev => {
    if (seen[ev.uid]) return; // duplicate UID within one feed
    seen[ev.uid] = true;
    const item = known[ev.uid];
    if (item) {
      const task = item.task_id !== null ? tasks.find(t => t.id === item.task_id) : null;
      if (!task) { skipped++; return; } // you deleted it, so leave it deleted
      let changed = false;
      ['title', 'due', 'link'].forEach(f => {
        if (ev[f] !== null && task[f] !== ev[f]) { task[f] = ev[f]; changed = true; }
      });
      if (changed) updated++;
      return;
    }
    if (ev.due && ev.due < cutoff) return; // old history; don't flood the list

    let project = null;
    if (ev.course) {
      project = byName[ev.course.toLowerCase()] || null;
      if (!project) {
        project = {
          id: db.nextId('projects'), name: ev.course, color: '#d4a017', status: 'active',
          next_step: '', notes: '', created_at: nowIso_(),
        };
        projects.push(project);
        byName[project.name.toLowerCase()] = project;
        newProjects.push(project.name);
      }
    }
    const task = {
      id: db.nextId('tasks'), project_id: project ? project.id : null, title: ev.title, priority: 1,
      due: ev.due, today: false, done: false, created_at: nowIso_(), completed_at: null,
      source: 'schoology', link: ev.link,
    };
    tasks.push(task);
    items.push({ uid: ev.uid, task_id: task.id });
    known[ev.uid] = items[items.length - 1];
    added++;
  });

  if (newProjects.length) db.save('projects', projects);
  if (added || updated) db.save('tasks', tasks);
  if (added) db.save('sync_items', items);
  return { added, updated, skipped_deleted: skipped, new_projects: newProjects };
}

function schoologySync_() {
  const url = props_().getProperty('schoology_url');
  if (!url) fail_("Schoology isn't connected yet.");
  const parsed = parseIcs_(fetchFeed_(url));
  const result = applyEvents_(parsed.events, todayLocal_());
  const syncedAt = nowIso_();
  props_().setProperty('schoology_last_sync', syncedAt);
  return Object.assign(result, {
    events_in_feed: parsed.events.length,
    with_course: parsed.events.filter(e => e.course).length,
    fields_seen: parsed.fields,
    synced_at: syncedAt,
  });
}
