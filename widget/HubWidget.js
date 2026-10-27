// Hub home-screen widget for iPhone, run by the Scriptable app.
// Shows every active project with a progress ring, open and overdue counts, and what's due next.
// Setup steps are in widget/README.md.

// Widget parameter (long-press the widget > Edit Widget > Parameter):
//   blank          all projects
//   Biology, Chem  only the projects whose names contain these words

const KEY_URL = "hub_widget_url";
const KEY_TOKEN = "hub_widget_token";
const CACHE_FILE = "hub-widget-cache.json";
const REFRESH_MINUTES = 15;

const C = {
  bg: Color.dynamic(new Color("#f6f4ef"), new Color("#16181d")),
  card: Color.dynamic(new Color("#ffffff"), new Color("#22252c")),
  text: Color.dynamic(new Color("#1c1d21"), new Color("#f2f2f4")),
  dim: Color.dynamic(new Color("#6b6e76"), new Color("#9a9ea8")),
  red: new Color("#e5534b"),
  amber: new Color("#d99a26"),
  green: new Color("#3fa865"),
  track: new Color("#8e8e93", 0.25),
};

// ---------- settings ----------

async function askSettings() {
  const a = new Alert();
  a.title = "Connect your Hub";
  a.message = "Paste your Hub address and the APP_TOKEN from Render (Environment tab).";
  a.addTextField("https://hub-xxxx.onrender.com", Keychain.contains(KEY_URL) ? Keychain.get(KEY_URL) : "");
  a.addSecureTextField("APP_TOKEN", Keychain.contains(KEY_TOKEN) ? Keychain.get(KEY_TOKEN) : "");
  a.addAction("Save");
  a.addCancelAction("Cancel");
  if ((await a.presentAlert()) === -1) return false;
  Keychain.set(KEY_URL, a.textFieldValue(0).trim().replace(/\/+$/, ""));
  Keychain.set(KEY_TOKEN, a.textFieldValue(1).trim());
  return true;
}

function hubUrl() {
  return Keychain.contains(KEY_URL) ? Keychain.get(KEY_URL) : "";
}

// ---------- data ----------

function localDate(d = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const fm = FileManager.local();
const cachePath = fm.joinPath(fm.documentsDirectory(), CACHE_FILE);

async function loadData() {
  const base = hubUrl();
  if (!base) return { error: "Open Scriptable and run this script to connect your Hub." };
  try {
    const req = new Request(`${base}/api/widget?today=${localDate()}`);
    req.headers = { Authorization: `Bearer ${Keychain.contains(KEY_TOKEN) ? Keychain.get(KEY_TOKEN) : ""}` };
    // The free Render plan sleeps; don't wait the full minute it takes to wake
    req.timeoutInterval = 20;
    const data = await req.loadJSON();
    const status = req.response && req.response.statusCode;
    if (status === 401) return { error: "Token was rejected. Run the script in Scriptable to fix it." };
    if (status !== 200 || !data || !data.projects) throw new Error(`HTTP ${status}`);
    data.fetchedAt = Date.now();
    fm.writeString(cachePath, JSON.stringify(data));
    return data;
  } catch (e) {
    // Hub asleep or offline: show the last good copy
    if (fm.fileExists(cachePath)) {
      const cached = JSON.parse(fm.readString(cachePath));
      cached.stale = true;
      return cached;
    }
    return { error: "Couldn't reach your Hub. It may be waking up; try again in a minute." };
  }
}

function filterProjects(projects, param) {
  const words = (param || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!words.length) return projects;
  return projects.filter((p) => words.some((w) => p.name.toLowerCase().includes(w)));
}

// ---------- drawing ----------

function hex(c) {
  return new Color((c || "#5b8def").replace("#", ""));
}

function progress(p) {
  const total = p.open + p.done;
  return total ? p.done / total : 0;
}

// Scriptable's Path has no arc, so build one from short line segments
function arcPath(cx, cy, r, from, to) {
  const path = new Path();
  const steps = Math.max(2, Math.ceil(((to - from) / (Math.PI * 2)) * 90));
  for (let i = 0; i <= steps; i++) {
    const a = from + ((to - from) * i) / steps;
    const pt = new Point(cx + r * Math.cos(a), cy + r * Math.sin(a));
    i === 0 ? path.move(pt) : path.addLine(pt);
  }
  return path;
}

function ring(fraction, color, size, width, label) {
  const ctx = new DrawContext();
  ctx.size = new Size(size, size);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const c = size / 2;
  const r = c - width / 2 - 1;
  const top = -Math.PI / 2;

  ctx.setLineWidth(width);
  ctx.setStrokeColor(C.track);
  ctx.addPath(arcPath(c, c, r, 0, Math.PI * 2));
  ctx.strokePath();

  if (fraction > 0) {
    ctx.setStrokeColor(color);
    ctx.addPath(arcPath(c, c, r, top, top + Math.PI * 2 * Math.min(fraction, 1)));
    ctx.strokePath();
    // round caps
    ctx.setFillColor(color);
    for (const a of [top, top + Math.PI * 2 * Math.min(fraction, 1)]) {
      const x = c + r * Math.cos(a);
      const y = c + r * Math.sin(a);
      ctx.fillEllipse(new Rect(x - width / 2, y - width / 2, width, width));
    }
  }

  if (label) {
    ctx.setFont(Font.boldRoundedSystemFont(size * 0.28));
    ctx.setTextColor(color);
    ctx.setTextAlignedCenter();
    const h = size * 0.36;
    ctx.drawTextInRect(label, new Rect(0, c - h / 2, size, h));
  }
  return ctx.getImage();
}

function bar(fraction, color, width, height) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const track = new Path();
  track.addRoundedRect(new Rect(0, 0, width, height), height / 2, height / 2);
  ctx.addPath(track);
  ctx.setFillColor(C.track);
  ctx.fillPath();
  if (fraction > 0) {
    const fill = new Path();
    fill.addRoundedRect(new Rect(0, 0, Math.max(height, width * Math.min(fraction, 1)), height), height / 2, height / 2);
    ctx.addPath(fill);
    ctx.setFillColor(color);
    ctx.fillPath();
  }
  return ctx.getImage();
}

// One bar split into each project's share of the open work
function stackedBar(projects, width, height) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, height);
  ctx.opaque = false;
  ctx.respectScreenScale = true;
  const total = projects.reduce((s, p) => s + p.open, 0);
  const clip = new Path();
  clip.addRoundedRect(new Rect(0, 0, width, height), height / 2, height / 2);
  ctx.addPath(clip);
  ctx.setFillColor(C.track);
  ctx.fillPath();
  if (total) {
    let x = 0;
    const gap = 2;
    const live = projects.filter((p) => p.open > 0);
    live.forEach((p, i) => {
      const w = (p.open / total) * width;
      const seg = new Path();
      seg.addRoundedRect(new Rect(x, 0, Math.max(height, w - (i < live.length - 1 ? gap : 0)), height), height / 2, height / 2);
      ctx.addPath(seg);
      ctx.setFillColor(hex(p.color));
      ctx.fillPath();
      x += w;
    });
  }
  return ctx.getImage();
}

// ---------- text helpers ----------

function dueLabel(iso, todayIso) {
  const days = Math.round((new Date(iso + "T12:00:00") - new Date(todayIso + "T12:00:00")) / 86400000);
  if (days < 0) return `${-days}d late`;
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days < 7) return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short" });
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function addText(stack, str, size, color, weight = "regular") {
  const t = stack.addText(str);
  t.font = weight === "bold" ? Font.boldRoundedSystemFont(size)
    : weight === "semibold" ? Font.semiboldRoundedSystemFont(size)
    : Font.systemFont(size);
  t.textColor = color;
  t.lineLimit = 1;
  return t;
}

function header(w, data, title = "Hub") {
  const h = w.addStack();
  h.centerAlignContent();
  addText(h, title, 13, C.text, "bold");
  h.addSpacer();
  const note = data.stale ? "offline" : new Date(data.fetchedAt || Date.now()).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const t = addText(h, note, 10, data.stale ? C.amber : C.dim);
  t.textOpacity = 0.9;
}

function stat(stack, value, label, color) {
  const s = stack.addStack();
  s.layoutVertically();
  addText(s, String(value), 20, color, "bold");
  addText(s, label, 10, C.dim);
}

// ---------- layouts ----------

function small(w, data, projects) {
  header(w, data);
  w.addSpacer(6);
  const t = data.totals;
  const row = w.addStack();
  row.centerAlignContent();
  const done = t.done_week;
  const img = row.addImage(ring(done / Math.max(1, done + t.open), C.green, 58, 7, String(t.open)));
  img.imageSize = new Size(58, 58);
  row.addSpacer(10);
  const col = row.addStack();
  col.layoutVertically();
  addText(col, `${t.overdue} late`, 12, t.overdue ? C.red : C.dim, "semibold");
  addText(col, `${t.due_today} today`, 12, t.due_today ? C.amber : C.dim, "semibold");
  addText(col, `${done} done/wk`, 12, C.green, "semibold");
  w.addSpacer();
  w.addImage(stackedBar(projects, 130, 8)).imageSize = new Size(130, 8);
  w.addSpacer(4);
  const names = projects.filter((p) => p.open).slice(0, 2).map((p) => p.name).join(" · ");
  addText(w, names || `${projects.length} projects`, 10, C.dim);
}

function projectRow(w, p, today, { ringSize, showDetail, barWidth }) {
  const color = hex(p.color);
  const row = w.addStack();
  row.centerAlignContent();
  const img = row.addImage(ring(progress(p), color, ringSize, ringSize > 30 ? 4 : 3.5));
  img.imageSize = new Size(ringSize, ringSize);
  row.addSpacer(8);

  const col = row.addStack();
  col.layoutVertically();
  const top = col.addStack();
  top.centerAlignContent();
  addText(top, p.name, 13, C.text, "semibold");
  if (p.status === "paused") addText(top, "  paused", 10, C.dim);
  top.addSpacer();
  if (p.overdue) {
    addText(top, `${p.overdue} late `, 11, C.red, "semibold");
  }
  addText(top, `${p.open} open`, 11, C.dim);

  if (showDetail) {
    const sub = p.next_due
      ? `${p.next_due.title} · ${dueLabel(p.next_due.due, today)}`
      : p.next_step || `${p.done_week} done this week`;
    const t = addText(col, sub, 11, p.next_due && p.next_due.due < today ? C.red : C.dim);
    t.lineLimit = 1;
  }
  if (barWidth) {
    col.addSpacer(3);
    col.addImage(bar(progress(p), color, barWidth, 4)).imageSize = new Size(barWidth, 4);
  }
}

function medium(w, data, projects) {
  header(w, data);
  w.addSpacer(6);
  const body = w.addStack();
  const left = body.addStack();
  left.layoutVertically();
  left.size = new Size(78, 0);
  const t = data.totals;
  stat(left, t.open, "open", C.text);
  left.addSpacer(4);
  stat(left, t.overdue, "overdue", t.overdue ? C.red : C.dim);
  body.addSpacer(10);
  const right = body.addStack();
  right.layoutVertically();
  projects.slice(0, 4).forEach((p, i) => {
    if (i) right.addSpacer(5);
    projectRow(right, p, data.today, { ringSize: 22, showDetail: false, barWidth: 0 });
  });
  if (!projects.length) addText(right, "No active projects", 12, C.dim);
}

function large(w, data, projects) {
  header(w, data);
  w.addSpacer(8);
  const tiles = w.addStack();
  const t = data.totals;
  [[t.open, "open", C.text], [t.overdue, "overdue", t.overdue ? C.red : C.dim],
   [t.due_today, "today", t.due_today ? C.amber : C.dim], [t.done_week, "done / 7d", C.green]]
    .forEach(([v, label, color], i) => {
      if (i) tiles.addSpacer(6);
      const tile = tiles.addStack();
      tile.backgroundColor = C.card;
      tile.cornerRadius = 10;
      tile.setPadding(6, 8, 6, 8);
      tile.size = new Size(70, 0);
      stat(tile, v, label, color);
    });
  w.addSpacer(8);
  w.addImage(stackedBar(projects, 300, 6)).imageSize = new Size(300, 6);
  w.addSpacer(10);
  const shown = projects.slice(0, 6);
  shown.forEach((p, i) => {
    if (i) w.addSpacer(8);
    projectRow(w, p, data.today, { ringSize: 32, showDetail: true, barWidth: 0 });
  });
  if (projects.length > shown.length) {
    w.addSpacer(6);
    addText(w, `+${projects.length - shown.length} more`, 10, C.dim);
  }
  w.addSpacer();
}

// Lock screen
function accessory(w, data, family) {
  const t = data.totals;
  if (family === "accessoryCircular") {
    const img = w.addImage(ring(t.done_week / Math.max(1, t.done_week + t.open), Color.white(), 60, 6, String(t.open)));
    img.imageSize = new Size(60, 60);
  } else if (family === "accessoryInline") {
    addText(w, `${t.open} open · ${t.overdue} late`, 12, Color.white());
  } else {
    addText(w, "Hub", 13, Color.white(), "bold");
    addText(w, `${t.overdue} late · ${t.due_today} today`, 12, Color.white());
    const next = data.projects.map((p) => p.next_due).filter(Boolean).sort((a, b) => a.due.localeCompare(b.due))[0];
    addText(w, next ? `${next.title} · ${dueLabel(next.due, data.today)}` : `${t.done_week} done this week`, 12, Color.white());
  }
}

// ---------- main ----------

// Run inside Scriptable: connect on first run, then preview any size
let family = config.widgetFamily;
let proceed = true;
if (!config.runsInWidget) {
  let change = !hubUrl();
  family = "large";
  if (!change) {
    const a = new Alert();
    a.title = "Hub widget";
    ["Preview large", "Preview medium", "Preview small", "Change Hub address or token"].forEach((x) => a.addAction(x));
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    proceed = choice !== -1;
    change = choice === 3;
    family = ["large", "medium", "small"][choice] || "large";
  }
  if (change) proceed = await askSettings();
}

family = family || "large";
const data = proceed ? await loadData() : { error: "Not connected." };
const w = new ListWidget();
const isAccessory = family.startsWith("accessory");
if (!isAccessory) {
  w.backgroundColor = C.bg;
  w.setPadding(14, 14, 14, 14);
}
if (hubUrl()) w.url = hubUrl();
w.refreshAfterDate = new Date(Date.now() + REFRESH_MINUTES * 60000);

if (data.error) {
  addText(w, "Hub", 13, isAccessory ? Color.white() : C.text, "bold");
  const t = addText(w, data.error, 11, isAccessory ? Color.white() : C.dim);
  t.lineLimit = 4;
} else {
  const projects = filterProjects(data.projects, args.widgetParameter);
  if (isAccessory) accessory(w, data, family);
  else if (family === "small") small(w, data, projects);
  else if (family === "medium") medium(w, data, projects);
  else large(w, data, projects);
}

if (config.runsInWidget) {
  Script.setWidget(w);
} else if (proceed) {
  if (family === "small") await w.presentSmall();
  else if (family === "medium") await w.presentMedium();
  else await w.presentLarge();
}
Script.complete();
