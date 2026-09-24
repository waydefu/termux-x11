// ELECTRON-BENCH-01 driver (evidence/session/gl/ELECTRON-BENCH-01-FREEZE.md). Drives one Cursor run over the
// DevTools protocol and records, at every phase boundary, CPU of the whole launch session + X3 + Activity and
// the device state. Output: one JSON file.
//   node electron_bench_drive.mjs <port> <sid> <x3pid> <actpid|""> <serial> <launch_epoch_ms> <out.json> <shot.png>
// Phases: settle 60 s (not scored) -> idle 60 s -> scroll 30 s (300 page keys, direction flips every 50)
//         -> type 30 s (600 chars, 50 ms apart). rAF frame intervals are recorded only during scroll/type
// (a rAF loop keeps the compositor busy, so it must not run during idle).
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const [port, sid, x3pid, actpid, serial, launchMs, outFile, shotFile] = process.argv.slice(2);
const ADB = ['/data/data/com.termux/files/usr/bin/adb', ['-P', '5038', '-s', serial]];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const out = { port, sid: Number(sid), x3pid: Number(x3pid), actpid: actpid || null, phases: {}, errors: [] };

// ---- measurements --------------------------------------------------------------------------------------
function statTicks(pid, withChildren) {
  try {
    const s = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    const f = s.slice(s.lastIndexOf(')') + 2).split(' '); // f[0] = field 3 (state)
    const v = Number(f[11]) + Number(f[12]) + (withChildren ? Number(f[13]) + Number(f[14]) : 0);
    return { ticks: v, session: Number(f[3]) };
  } catch { return null; }
}
function sessionTicks() {   // utime+stime(+reaped children) of every live process in the launch session
  let sum = 0, n = 0;
  for (const d of fs.readdirSync('/proc')) {
    if (!/^\d+$/.test(d)) continue;
    const t = statTicks(d, true);
    if (t && t.session === out.sid) { sum += t.ticks; n++; }
  }
  return { ticks: sum, procs: n };
}
function adb(args) {
  try { return execFileSync(ADB[0], [...ADB[1], 'shell', ...args], { encoding: 'utf8', timeout: 15000 }).replace(/\r/g, ''); }
  catch { return ''; }
}
function actTicks() {
  if (!out.actpid) return null;
  const s = adb(['cat', `/proc/${out.actpid}/stat`]);
  if (!s.includes(')')) return null;
  const f = s.slice(s.lastIndexOf(')') + 2).trim().split(' ');
  return Number(f[11]) + Number(f[12]);
}
function devState() {
  const w = adb(['dumpsys', 'window']);
  const p = adb(['dumpsys', 'power']);
  const focus = (w.match(/mCurrentFocus=.*/) || [''])[0];
  const kg = (w.match(/isKeyguardShowing=(\w+)/) || [])[1];
  const wake = (p.match(/mWakefulness=(\w+)/) || [])[1];
  return { focus: focus ? focus.includes('com.waydefu.x11gpu/') : null, awake: wake ? wake === 'Awake' : null,
           keyguard: kg === undefined ? null : kg === 'true' };
}
// Optional EXTRA_PIDS (comma separated, e.g. the proot tracer of the launch in PROOT-BENCH): their own
// utime+stime are recorded per snapshot as extra_ticks (null if any is unreadable). Unset -> null, and
// nothing else changes (ELECTRON-BENCH-01 runs never set it).
const EXTRA = (process.env.EXTRA_PIDS || '').split(',').filter(Boolean);
function extraTicks() {
  if (!EXTRA.length) return null;
  let sum = 0;
  for (const p of EXTRA) { const t = statTicks(p, false); if (!t) return null; sum += t.ticks; }
  return sum;
}
// adb calls take 1-2 s: at a phase START they run before the CPU counters are read, at a phase END after,
// so the adb time never falls inside the measured window. (Activity ticks are adb too: outside the window.)
function snapshot(isStart) {
  const pre = isStart ? { state: devState(), act_ticks: actTicks() } : null;
  const s = sessionTicks(); const x = statTicks(out.x3pid, false); const e = extraTicks(); const t = Date.now();
  const post = isStart ? pre : { act_ticks: actTicks(), state: devState() };
  return { t, session_ticks: s.ticks, session_procs: s.procs, x3_ticks: x ? x.ticks : null, extra_ticks: e, ...post };
}

// ---- DevTools --------------------------------------------------------------------------------------------
async function json(path) { return (await fetch(`http://127.0.0.1:${port}${path}`)).json(); }
function connect(url) {
  return new Promise((ok, bad) => {
    const ws = new WebSocket(url); let id = 0; const pend = new Map();
    ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } };
    ws.onerror = (e) => bad(new Error('ws error ' + url));
    ws.onopen = () => ok({
      send: (method, params = {}) => new Promise((r) => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); }),
      close: () => ws.close(),
    });
  });
}
async function waitUp(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) { try { return await json('/json/version'); } catch { await sleep(1000); } }
  throw new Error('devtools never came up');
}
async function evalPage(page, expr) {
  const r = await page.send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.error || r.result?.exceptionDetails) throw new Error('eval failed: ' + JSON.stringify(r.error || r.result.exceptionDetails).slice(0, 300));
  return r.result.result.value;
}
function frameStats(ts) {
  const iv = []; for (let i = 1; i < ts.length; i++) iv.push(ts[i] - ts[i - 1]);
  if (!iv.length) return { n: 0, p50: null, p95: null, over50: null };
  const s = [...iv].sort((a, b) => a - b); const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { n: iv.length, p50: q(0.5), p95: q(0.95), over50: iv.filter((x) => x > 50).length };
}
// Each recorder loop carries a generation number and stops as soon as a newer one starts (dry-run 01:
// a stopped loop kept running into the next phase and both phases reported identical frame counts).
const RAF_START = `(() => { const g = (window.__frgen = (window.__frgen || 0) + 1); window.__fr = { t: [] };
  const f = (ts) => { if (window.__frgen !== g) return; window.__fr.t.push(ts); requestAnimationFrame(f); };
  requestAnimationFrame(f); return g; })()`;
const RAF_STOP = `(() => { window.__frgen = (window.__frgen || 0) + 1; return window.__fr.t; })()`;
const EDITOR_STATE = `(() => { const ln = document.querySelector('.monaco-editor .line-numbers'); const ae = document.activeElement;
  return { firstLine: ln ? ln.textContent.trim() : null, active: ae ? (ae.className || ae.tagName) + '' : null,
           editors: document.querySelectorAll('.monaco-editor').length, w: innerWidth, h: innerHeight,
           text: (document.querySelector('.monaco-editor .view-lines') || {}).textContent?.slice(0, 20000) || '' }; })()`;

async function key(page, k, code, vk) {
  const a = await page.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: k, code, windowsVirtualKeyCode: vk });
  const b = await page.send('Input.dispatchKeyEvent', { type: 'keyUp', key: k, code, windowsVirtualKeyCode: vk });
  return !a.error && !b.error;
}

async function main() {
  const v = await waitUp(90000);
  out.browser = v.Browser;
  const settleEnd = Number(launchMs) + 60000;
  if (Date.now() < settleEnd) await sleep(settleEnd - Date.now());

  const br = await connect(v.webSocketDebuggerUrl);
  const info = (await br.send('SystemInfo.getInfo')).result?.gpu || {};
  out.identity = { glRenderer: info.auxAttributes?.glRenderer ?? null, gpu_compositing: info.featureStatus?.gpu_compositing ?? null,
                   rasterization: info.featureStatus?.rasterization ?? null };
  const procs = async () => ((await br.send('SystemInfo.getProcessInfo')).result?.processInfo || []).map((p) => ({ type: p.type, id: p.id }));
  out.procs_start = await procs();

  const page0 = (await json('/json/list')).find((t) => t.type === 'page' && /workbench/.test(t.url));
  if (!page0) throw new Error('workbench page not found');
  const page = await connect(page0.webSocketDebuggerUrl);

  // Focus the editor's input element directly. Dry-run 01: a synthetic click in the middle of the editor moved
  // focus OFF the editor (keys then went nowhere), although the editor already had focus after opening the file.
  const FOCUS = `(() => { const el = document.querySelector('.monaco-editor textarea.inputarea, .monaco-editor .native-edit-context');
    if (el) el.focus(); const ae = document.activeElement; return !!(el && ae === el); })()`;
  const e0 = await evalPage(page, EDITOR_STATE);
  const focused = await evalPage(page, FOCUS);
  await sleep(1000);
  out.editor_before = { ...(await evalPage(page, EDITOR_STATE)), text: undefined, editor_found: e0.editors > 0, focused,
                        first_state: { ...e0, text: undefined } };

  // idle
  const i0 = snapshot(true); await sleep(60000); const i1 = snapshot(false);
  out.phases.idle = { start: i0, end: i1 };

  // scroll
  await evalPage(page, RAF_START);
  let lineAt50 = null; const s0 = snapshot(true); let acked = 0; const t0 = Date.now();
  for (let i = 0; i < 300; i++) {
    const down = Math.floor(i / 50) % 2 === 0;
    if (await key(page, down ? 'PageDown' : 'PageUp', down ? 'PageDown' : 'PageUp', down ? 34 : 33)) acked++;
    if (i === 49) lineAt50 = (await evalPage(page, EDITOR_STATE)).firstLine;   // must have moved off line 1
    const next = t0 + (i + 1) * 100; if (Date.now() < next) await sleep(next - Date.now());
  }
  const s1 = snapshot(false); const sf = await evalPage(page, RAF_STOP);
  const lineAfterScroll = (await evalPage(page, EDITOR_STATE)).firstLine;
  out.phases.scroll = { start: s0, end: s1, keys_sent: 300, keys_acked: acked, frames: frameStats(sf), first_line_at_50: lineAt50, first_line_after: lineAfterScroll };

  // type
  const MARK = 'gpu_bench_typing_probe_';
  const text = (MARK + '0123456789 ').repeat(40).slice(0, 600);
  out.phases.scroll.refocused_before_type = await evalPage(page, FOCUS);
  await evalPage(page, RAF_START);
  const y0 = snapshot(true); let chars = 0; const t1 = Date.now();
  for (let i = 0; i < 600; i++) {
    const r = await page.send('Input.insertText', { text: text[i] }); if (!r.error) chars++;
    const next = t1 + (i + 1) * 50; if (Date.now() < next) await sleep(next - Date.now());
  }
  const y1 = snapshot(false); const yf = await evalPage(page, RAF_STOP);
  const typedVisible = (await evalPage(page, EDITOR_STATE)).text.includes(MARK);
  out.phases.type = { start: y0, end: y1, chars_sent: 600, chars_acked: chars, frames: frameStats(yf), typed_text_visible: typedVisible };

  const shot = await page.send('Page.captureScreenshot', { format: 'png' });
  if (shot.result?.data) fs.writeFileSync(shotFile, Buffer.from(shot.result.data, 'base64'));
  out.screenshot = shot.result?.data ? shotFile : null;
  out.procs_end = await procs();
  page.close(); br.close();
}

main().catch((e) => out.errors.push(String(e && e.stack || e))).finally(() => {
  fs.writeFileSync(outFile, JSON.stringify(out, null, 1));
  console.log(`DRIVE_DONE errors=${out.errors.length}`);
  process.exit(0);
});
