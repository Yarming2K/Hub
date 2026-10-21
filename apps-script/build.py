"""Builds apps-script/Index.html from static/index.html.

The two pages are the same app; only how they talk to the server differs.
Run after changing static/index.html:  python apps-script/build.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src = (ROOT / "static" / "index.html").read_text()


def rep(old: str, new: str) -> None:
    global src
    count = src.count(old)
    if count != 1:
        raise SystemExit(f"build.py: expected 1 match, found {count}: {old[:70]!r}")
    src = src.replace(old, new)


# Calls go to Code.gs through google.script.run instead of fetch.
# Your Google sign-in protects the page, so there's no password screen.
rep('''class AuthError extends Error {}
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const tok = getToken();
  if (tok) headers.Authorization = "Bearer " + tok;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : "Check the fields and try again"; } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}''', '''class AuthError extends Error {}
function gs(fn, ...args) {
  return new Promise((resolve, reject) => {
    google.script.run
      .withSuccessHandler(resolve)
      .withFailureHandler(err => reject(new Error(String((err && err.message) || err).replace(/^(Hub)?Error:\\s*/, ""))))[fn](...args);
  });
}
async function api(path, opts = {}) {
  const out = await gs("api", opts.method || "GET", path, opts.body ? JSON.stringify(opts.body) : null);
  return out == null ? null : JSON.parse(out);
}''')

rep('''<h1>Offline</h1><p>${esc(e.message)}. Check your connection, then reload.</p>''',
    '''<h1>Problem</h1><p>${esc(e.message)}</p><p>Check your connection, then reload the page.</p>''')

# Photos come from Drive as base64; PDFs open in Drive's viewer
rep('''// Files need the auth header, so they're fetched as blobs instead of plain <img src>
const blobCache = new Map();
async function blobUrl(id) {
  if (!blobCache.has(id)) {
    blobCache.set(id, (async () => {
      const tok = getToken();
      const res = await fetch(`/api/attachments/${id}/file`, { headers: tok ? { Authorization: "Bearer " + tok } : {} });
      if (!res.ok) { blobCache.delete(id); throw new Error("Couldn't load that file"); }
      return URL.createObjectURL(await res.blob());
    })());
  }
  return blobCache.get(id);
}''', '''// Files live in your Drive; photos are fetched as data to show thumbnails here
const blobCache = new Map();
async function blobUrl(id) {
  if (!blobCache.has(id)) {
    blobCache.set(id, (async () => {
      try {
        const { type, data } = JSON.parse(await gs("getAttachmentData", id));
        const bin = atob(data);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return URL.createObjectURL(new Blob([bytes], { type }));
      } catch (e) { blobCache.delete(id); throw new Error("Couldn't load that file"); }
    })());
  }
  return blobCache.get(id);
}

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}''')

rep('''      const body = new FormData();
      body.append("file", await shrink(f));
      body.append(target.kind === "task" ? "task_id" : "project_id", target.id);
      const tok = getToken();
      const res = await fetch("/api/attachments", { method: "POST", body, headers: tok ? { Authorization: "Bearer " + tok } : {} });
      if (res.status === 401) return renderGate();
      if (!res.ok) { let m = "Upload failed"; try { m = (await res.json()).detail || m; } catch {} toast(`${f.name}: ${m}`); continue; }
      ok++;
    } catch { toast(`${f.name}: upload failed. Check your connection.`); }''', '''      const file = await shrink(f);
      if (file.size > 10 * 1024 * 1024) { toast(`${f.name}: File is over 10 MB. Try a smaller photo or PDF.`); continue; }
      await gs("uploadAttachment", target.kind, target.id, file.name, await toBase64(file));
      ok++;
    } catch (e) { toast(`${f.name}: ${e.message || "upload failed. Check your connection."}`); }''')

rep('''  let url;
  try { url = await blobUrl(id); } catch (e) { return toast(e.message); }
  if (a.content_type === "application/pdf") {
    const w = window.open(url, "_blank");
    if (!w) { const link = document.createElement("a"); link.href = url; link.download = a.filename; link.click(); }
    return;
  }''', '''  if (a.content_type === "application/pdf") { window.open(a.url, "_blank"); return; }
  let url;
  try { url = await blobUrl(id); } catch (e) { return toast(e.message); }''')
rep('''  const dl = document.getElementById("viewer-dl"); dl.href = url; dl.download = a.filename;''',
    '''  document.getElementById("viewer-dl").href = a.url;''')
rep('''<a id="viewer-dl" download>Download</a>''', '''<a id="viewer-dl" target="_blank" rel="noopener">Open in Drive</a>''')
rep('Big phone photos are shrunk before upload so they load fast and fit the free database',
    'Big phone photos are shrunk before upload so they load fast and save Drive space')

# Coach setup lives in the script's settings instead of Render
rep('''        <li>In Render, open the hub service → <strong>Environment</strong>.</li>
        <li>Add <code>ANTHROPIC_API_KEY</code> with the key, and save.</li>
      </ol>
      <p>The hub restarts by itself, then the coach is ready.</p>''', '''        <li>In Apps Script, open <strong>Project Settings</strong> (the gear) → <strong>Script properties</strong>.</li>
        <li>Add <code>ANTHROPIC_API_KEY</code> with the key, and save.</li>
      </ol>
      <p>Reload this page, then the coach is ready.</p>''')

out = ROOT / "apps-script" / "Index.html"
note = "<!-- Generated from static/index.html by apps-script/build.py. Edit that file instead. -->\n"
rep("<!doctype html>\n", "<!doctype html>\n" + note)
out.write_text(src)
print(f"wrote {out} ({len(src):,} chars)")
