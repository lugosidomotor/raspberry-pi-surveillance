"use strict";
const $ = id => document.getElementById(id);
const page = location.pathname.replace(/^\/+|\/+$/g, "") || "home";
const isEvents = page === "events", isTablet = page === "tablet";
const DAY_NAMES = ["V", "H", "K", "Sze", "Cs", "P", "Szo"];
const LABELS = {person: "Személy", car: "Autó", cat: "Macska", dog: "Kutya", bicycle: "Kerékpár"};
const COLORS = ["#e8c31c", "#2aa6e3", "#9aa3ab", "#b48cf0", "#ff8576"];
const LIMIT = 24;
const name = label => LABELS[label] || label;
const number = (value, digits = 1) => value.toLocaleString("hu-HU", {maximumFractionDigits: digits});
const clock = value => new Date(value).toLocaleTimeString("hu-HU", {hour: "2-digit", minute: "2-digit"});
const stamp = value => new Date(value).toLocaleString("hu-HU", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"});
const gib = value => `${number(value / 1024 ** 3)} GiB`;
const midnight = value => { const day = new Date(value); day.setHours(0, 0, 0, 0); return day; };
const addDays = (value, days) => { const day = new Date(value); day.setDate(day.getDate() + days); return day; };
const sameDay = (a, b) => midnight(a).getTime() === midnight(b).getTime();

let selectedDay = midnight(new Date()), rotation = 0, liveUrl, streamSize = "";
let watchdogAuto = true, loading = false, offset = 0, filter = "", tracked = [], wasOnline = null;
const rendered = {};

async function get(url) {
  const result = await fetch(url, {cache: "no-store", signal: AbortSignal.timeout(10000)});
  if (!result.ok) throw new Error("HTTP " + result.status);
  return result.json();
}
// The only writes: a JSON body forces a CORS preflight, so other websites cannot change settings.
async function post(url, body) {
  const result = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body), signal: AbortSignal.timeout(10000)});
  if (!result.ok) throw new Error("HTTP " + result.status);
  return result.json();
}
function node(tag, className, content) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (content != null) element.textContent = content;
  return element;
}
function text(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
// Re-render a region only when its data changed, so hover, focus and images survive the 5-second refresh.
function changed(key, data) { const signature = JSON.stringify(data); if (rendered[key] === signature) return false; rendered[key] = signature; return true; }
// Static SVG rings from numbers only (no API text), shared by the donut, gauges and progress spinners.
function arc(radius, width, parts, track = "rgba(255,255,255,.1)") {
  const size = (radius + width) * 2, length = 2 * Math.PI * radius;
  let start = 0, circles = `<circle cx="${size / 2}" cy="${size / 2}" r="${radius}" stroke="${track}" stroke-width="${width}" fill="none"/>`;
  for (const {share, color, gap = 0} of parts) {
    const span = Math.max(0.001, share * length - gap);
    circles += `<circle cx="${size / 2}" cy="${size / 2}" r="${radius}" stroke="${color}" stroke-width="${width}" fill="none" stroke-linecap="round" stroke-dasharray="${span} ${length}" stroke-dashoffset="${-start - gap / 2}"/>`;
    start += share * length;
  }
  return `<svg viewBox="0 0 ${size} ${size}" aria-hidden="true">${circles}</svg>`;
}

// --- live view ------------------------------------------------------------------------------------------------
// The viewer follows the stream's real aspect ratio (e.g. 4:3 full sensor), also when CSS rotates it.
function applyStreamSize(width, height) {
  streamSize = `${width}x${height}`;
  const style = $("frame").style;
  style.setProperty("--frame-aspect", `${width} / ${height}`);
  style.setProperty("--frame-ratio", String(width / height));
  style.setProperty("--frame-aspect-rotated", `${height} / ${width}`);
  style.setProperty("--portrait-factor", String(height / width));
  style.setProperty("--rot-width", `${width / height * 100}%`);
  style.setProperty("--rot-height", `${height / width * 100}%`);
}
function applyRotation(value) {
  rotation = value;
  $("frame").dataset.rotation = String(value);
  text("rotate-label", `${value}°`);
  $("rotate").setAttribute("aria-label", `Kép forgatása 90 fokkal, jelenleg ${value} fok`);
}
async function rotate() {
  $("rotate").disabled = true;
  try { applyRotation((await post("/api/settings/rotation", {rotation: (rotation + 90) % 360})).rotation); error(""); }
  catch { error("Nem sikerült elforgatni a képet. Próbáld újra."); }
  finally { $("rotate").disabled = false; }
}
// "{host}" or 0.0.0.0 in LIVE_STREAM_URL becomes the address this page was opened with (LAN IP, Tailscale IP
// or a name). A browser cannot reach a server at 0.0.0.0, so that host can only mean "this same machine".
function streamAddress(raw) {
  if (!raw) return raw;
  const url = raw.replaceAll("{host}", location.hostname);
  try {
    const parsed = new URL(url);
    if (parsed.hostname === "0.0.0.0" && location.hostname) { parsed.hostname = location.hostname; return parsed.href; }
  } catch { /* not a URL: leave it as configured */ }
  return url;
}
function updateLive(s) {
  const online = s.camera === "connected", starting = s.camera === "connecting";
  if (typeof s.rotation === "number" && s.rotation !== rotation) applyRotation(s.rotation);
  let [width, height] = [4, 3];
  if (Array.isArray(s.frame_size)) {
    // Reported frames are already turned by the app; the embedded player shows the unturned stream.
    [width, height] = s.rotation % 180 ? [s.frame_size[1], s.frame_size[0]] : s.frame_size;
    if (`${width}x${height}` !== streamSize) applyStreamSize(width, height);
  }
  // The camera state lives on the live view itself rather than in a page header.
  text("live-meta", online ? `${number(s.camera_fps)} fps · ${width}×${height}` : starting ? "Kamera indul…" : "Kamera offline");
  $("live-meta").dataset.tone = online ? "positive" : starting ? "muted" : "danger";
  text("motion", s.motion ? "Mozgás észlelve" : "Nincs mozgás");
  $("motion").classList.toggle("active", Boolean(s.motion));
  const d = s.last_detection;
  text("detection", d ? `AI: ${name(d.label)} · ${Math.round(d.confidence * 100)}% · ${clock(d.timestamp)}` : "AI: még nincs találat");
  // A player that lost its stream rarely recovers by itself: reload it when the camera comes back.
  if (online && wasOnline === false) { const player = $("frame").querySelector("iframe"); if (player) player.src = liveUrl; }
  wasOnline = online;
  const streamUrl = streamAddress(s.live_stream_url);
  if (streamUrl === liveUrl) return;
  liveUrl = streamUrl;
  $("frame").querySelector("iframe")?.remove();
  $("stream-empty").hidden = Boolean(liveUrl);
  if (liveUrl) {
    const frame = document.createElement("iframe");
    frame.src = liveUrl; frame.title = "Élő kamera lejátszó"; frame.allow = "autoplay; fullscreen; picture-in-picture"; frame.referrerPolicy = "no-referrer";
    $("frame").append(frame);
  }
}

// --- status cards -------------------------------------------------------------------------------------------------
function tile(id, value, unit, label, danger = false) {
  const element = $(id);
  element.querySelector("strong").replaceChildren(value, ...(unit ? [node("small", null, unit)] : []));
  element.querySelector(":scope > div > span").textContent = label;
  element.dataset.tone = danger ? "danger" : "";
}
function updateTiles(s) {
  const h = s.hardware || {};
  tile("tile-fps", `${number(s.camera_fps)} / ${number(s.motion_fps)}`, "fps", "Kamera / mozgás", s.camera !== "connected");
  tile("tile-ai", s.ai_inference_ms == null ? "—" : number(s.ai_inference_ms), s.ai_inference_ms == null ? "" : "ms",
    s.ai_inference_ms == null ? `AI · ${s.device} · várakozás mozgásra` : `AI-futásidő · ${s.device}${s.ai === "ready" ? "" : " · " + s.ai}`);
  tile("tile-temp", h.temperature_c == null ? "—" : number(h.temperature_c), h.temperature_c == null ? "" : "°C", "CPU-hőmérséklet", h.temperature_c >= 75);
  tile("tile-disk", h.disk ? number(h.disk.free_bytes / 1024 ** 3) : "—", h.disk ? "GiB" : "", s.storage_low ? "Kevés tárhely, a mentés szünetel" : "Szabad tárhely", Boolean(s.storage_low));
}
function fact(label, value, tone, icon) {
  const row = node("div"), dt = node("dt"), dd = node("dd", null, value);
  if (icon) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"), use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    svg.setAttribute("class", "fact-icon"); svg.setAttribute("aria-hidden", "true"); use.setAttribute("href", "#" + icon);
    svg.append(use); dt.append(svg);
  }
  dt.append(label);
  if (tone) dd.dataset.tone = tone;
  row.append(dt, dd);
  return row;
}
const UPLOADS = {idle: "várakozás", uploading: "feltöltés…", finished: "kész", failed: "hiba", queued: "sorban"};
function updateIntegrations(s) {
  const on = value => value ? ["Bekapcsolva", "positive"] : ["Kikapcsolva", "muted"];
  const upload = s.backup_enabled ? [UPLOADS[s.upload_status] || s.upload_status, s.upload_status === "failed" ? "danger" : "positive"] : on(false);
  const rows = [
    ["Értesítések", ...on(s.notifications_enabled), "i-bell"],
    ["Cloud backup", ...on(s.backup_enabled), "i-cloud"],
    ["Feltöltés", ...upload, "i-upload"],
    ["Eseményklip", ...on(s.record_events), "i-film"],
    ["24/7 rögzítés", ...on(s.continuous_recording), "i-rec"],
    ["Videóelemzés", ...on(s.activity_analysis_enabled), "i-spark"],
    ["Mentés állapota", s.storage_low ? "Kevés tárhely · szünetel" : "Rendben", s.storage_low ? "danger" : "positive", "i-disk"],
  ];
  text("retention", `${s.retention_days} nap megőrzés`);
  if (changed("integrations", rows)) $("integrations").replaceChildren(...rows.map(row => fact(...row)));
}
function ago(value) {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 60000));
  if (minutes < 1) return "most";
  if (minutes < 60) return `${minutes} perce`;
  return minutes < 1440 ? `${Math.floor(minutes / 60)} órája` : stamp(value);
}
function updateSpotlight(event) {
  text("spot-ago", event ? ago(event.timestamp) : "—");
  $("spot-pill").hidden = !event;
  $("spot-link").hidden = !event;
  if (!event) return;
  const state = eventState(event);
  text("spot-title", `${name(event.label)}${event.is_test ? " · TESZT" : ""} · ${event.zone}`);
  text("spot-meta", `${clock(event.timestamp)} · ${Math.round(event.confidence * 100)}% biztos`);
  text("spot-pill", state.pill); $("spot-pill").dataset.kind = state.kind;
  $("spot-link").href = event.snapshot_url;
  $("spot-link").target = "_blank"; $("spot-link").rel = "noopener";
  $("spot-link").setAttribute("aria-label", `Snapshot: ${name(event.label)}, ${event.zone}`);
  const src = event.thumbnail_url || event.snapshot_url;
  if ($("spot-image").getAttribute("src") !== src) { $("spot-link").classList.remove("missing"); $("spot-image").src = src; }
}

const WATCHDOG = {ok: ["Figyel", "done"], warning: ["Figyelés", "go"], recovering: ["Helyreállítás", "go"], rebooting: ["Újraindítás", "go"], blocked: ["Letiltva", "warn"]};
const ACTIONS = {"restart-source": "kameraforrás újraindítva", reboot: "Pi újraindítva", "dry-run": "próba"};
function updateWatchdog(w) {
  const installed = Boolean(w?.installed);
  $("wd-toggle").hidden = !installed;
  if (!installed) {
    text("wd-pill", "Nincs telepítve"); $("wd-pill").dataset.kind = "muted";
    text("wd-message", "A helyreállító felügyelő nincs telepítve."); text("wd-reboots", "deploy/pi/install-host.sh");
    for (const id of ["wd-firmware", "wd-camera", "wd-checked", "wd-action"]) { text(id, "—"); delete $(id).dataset.tone; }
    return;
  }
  const [label, kind] = w.stale ? ["Nem jelentkezik", "warn"] : WATCHDOG[w.state] || [w.state, "muted"];
  text("wd-pill", label); $("wd-pill").dataset.kind = kind;
  $("felugyelo").dataset.tone = kind === "done" ? "" : "danger";
  text("wd-message", w.state === "ok" && !w.stale ? "Minden rendben" : w.message || "—");
  text("wd-reboots", `Újraindítás 24 órán belül: ${w.reboots_24h ?? 0} / 3`);
  text("wd-firmware", w.firmware_ok ? "Rendben" : "Nem válaszol"); $("wd-firmware").dataset.tone = w.firmware_ok ? "positive" : "danger";
  text("wd-camera", w.camera_ok ? "Van kép" : "Nincs kép"); $("wd-camera").dataset.tone = w.camera_ok ? "positive" : "danger";
  text("wd-checked", stamp(w.updated_at));
  text("wd-action", w.last_action ? `${ACTIONS[w.last_action.action] || w.last_action.action} · ${stamp(w.last_action.at)}` : "még nem volt");
  watchdogAuto = w.auto_reboot !== false;
  $("wd-toggle").setAttribute("aria-checked", String(watchdogAuto));
}
async function toggleWatchdog() {
  $("wd-toggle").disabled = true;
  try {
    watchdogAuto = (await post("/api/settings/watchdog", {auto_reboot: !watchdogAuto})).auto_reboot;
    $("wd-toggle").setAttribute("aria-checked", String(watchdogAuto)); error("");
  } catch { error("Nem sikerült menteni a helyreállítási beállítást. Próbáld újra."); }
  finally { $("wd-toggle").disabled = false; }
}
function updateHardware(h) {
  $("hardver").hidden = isEvents || !h?.is_raspberry_pi;
  if (!h?.is_raspberry_pi) return;
  text("hw-model", h.model.replace(/^Raspberry Pi /, "Pi "));
  const warnings = h.warnings || [];
  $("hw-warning").hidden = !warnings.length;
  text("hw-warning", warnings.join(" · "));
  const speed = h.undervoltage ? "lassítva (gyenge táp)" : h.cpu_frequency_mhz ? `${Math.round(h.cpu_frequency_mhz)} MHz` : "—";
  const gauges = [
    ["CPU", h.cpu_percent, h.cpu_percent == null ? "…" : `${Math.round(h.cpu_percent)}%`, h.cpu_percent == null ? "Mérés…" : `${h.cpu_cores || "?"} mag · ${speed}`, h.cpu_percent >= 90],
    ["Memória", h.memory?.used_percent, h.memory ? `${Math.round(h.memory.used_percent)}%` : "—", h.memory ? `${gib(h.memory.used_bytes)} / ${gib(h.memory.total_bytes)}` : "Nem elérhető", h.memory?.used_percent >= 90],
    ["Tárhely", h.disk?.used_percent, h.disk ? `${Math.round(h.disk.used_percent)}%` : "—", h.disk ? `${gib(h.disk.free_bytes)} szabad / ${gib(h.disk.total_bytes)}` : "Nem elérhető", Boolean(h.disk && h.disk.free_bytes < h.disk.total_bytes * 0.1)],
    ["Hőmérséklet", h.temperature_c == null ? null : h.temperature_c / 85 * 100, h.temperature_c == null ? "—" : `${Math.round(h.temperature_c)}°`, h.temperature_c == null ? "Nem elérhető" : h.temperature_c >= 75 ? "Magas" : "CPU, °C", h.temperature_c >= 75],
  ].map(([label, value, shown, detail, danger]) => [label, Math.round(value ?? 0), shown, detail, danger]);
  if (changed("gauges", gauges)) {
    $("gauges").replaceChildren(...gauges.map(([label, value, shown, detail, danger]) => {
      const item = node("li", "gauge"), ring = node("div", "gauge-ring"), body = node("div");
      item.dataset.tone = danger ? "danger" : "";
      ring.innerHTML = arc(31, 6, [{share: Math.min(100, value) / 100, color: danger ? "#ff8576" : "rgba(255,255,255,.88)"}]);
      ring.append(node("strong", null, shown));
      body.append(node("b", null, label), node("span", null, detail));
      item.append(ring, body);
      return item;
    }));
  }
  const minutes = Math.floor((h.uptime_seconds || 0) / 60);
  const uptime = h.uptime_seconds == null ? "—" : `${minutes >= 1440 ? `${Math.floor(minutes / 1440)} n ` : ""}${Math.floor(minutes % 1440 / 60)} ó ${minutes % 60} p`;
  const load = h.load_average ? h.load_average.map(v => v.toLocaleString("hu-HU", {minimumFractionDigits: 2, maximumFractionDigits: 2})).join(" / ") : "—";
  text("hw-foot", `Terhelés (1 / 5 / 15 p): ${load} · üzemidő: ${uptime}`);
}

// --- selected day: overview donut, activity capsules, calendar --------------------------------------------------
function dayLabel(day) {
  if (sameDay(day, new Date())) return "Ma";
  if (sameDay(day, addDays(new Date(), -1))) return "Tegnap";
  return day.toLocaleDateString("hu-HU", {month: "long", day: "numeric"});
}
const range = (start, end) => `/api/timeline?start=${start.getTime() / 1000}&end=${end.getTime() / 1000}`;
async function updateDay() {
  // Browser-local midnights, so 23- and 25-hour DST days stay whole.
  const start = selectedDay, end = addDays(start, 1), previous = addDays(start, -1);
  const [day, before] = await Promise.all([get(range(start, end)), get(range(previous, start))]);
  renderOverview(day.labels || {}, before.labels || {});
  renderActivity(day, start, end);
  renderCalendar();
}
function renderOverview(today, before) {
  const labels = [...new Set([...tracked, ...Object.keys(today)])];
  const total = labels.reduce((sum, label) => sum + (today[label] || 0), 0);
  text("overview-day", dayLabel(selectedDay));
  if (!changed("overview", [labels, today, before])) return;
  const shown = labels.filter(label => today[label]);
  const parts = shown.map(label => ({share: today[label] / total, color: COLORS[labels.indexOf(label) % COLORS.length], gap: shown.length > 1 ? 22 : 0}));
  $("donut").innerHTML = arc(70, 18, parts);
  const center = node("div", "donut-center");
  center.append(node("strong", null, String(total)), node("span", null, "esemény"));
  $("donut").append(center);
  $("donut").setAttribute("aria-label", `${dayLabel(selectedDay)}: ${total} esemény. ` + labels.map(label => `${name(label)}: ${today[label] || 0}`).join(", "));
  if (!labels.length) { $("legend").replaceChildren(node("li", "legend-empty", "Nincs követett objektum.")); return; }
  $("legend").replaceChildren(...labels.map((label, index) => {
    const item = node("li"), title = node("span", "name"), dot = node("i"), diff = (today[label] || 0) - (before[label] || 0);
    dot.style.background = COLORS[index % COLORS.length];
    title.append(dot, name(label));
    const delta = node("span", "delta", diff > 0 ? `+${diff}` : diff < 0 ? `−${-diff}` : "±0");
    delta.setAttribute("aria-label", `${diff >= 0 ? "+" : "−"}${Math.abs(diff)} az előző naphoz képest`);
    item.append(title, node("strong", null, String(today[label] || 0)), delta);
    return item;
  }));
}
function renderActivity(timeline, start, end) {
  const slots = Array.from({length: 8}, () => 0), slotSeconds = (end - start) / 8000;
  for (const bucket of timeline.buckets) slots[Math.min(7, Math.floor(bucket.index * timeline.bucket_seconds / slotSeconds))] += bucket.count;
  const peak = Math.max(1, ...slots), now = Date.now();
  const current = now >= start.getTime() && now < end.getTime() ? Math.min(7, Math.floor((now - start.getTime()) / 1000 / slotSeconds)) : -1;
  text("activity-meta", `${dayLabel(selectedDay)} · ${timeline.total} esemény`);
  if (!changed("activity", [slots, current])) return;
  $("bars").setAttribute("aria-label", `Események 3 órás szakaszokban, ${dayLabel(selectedDay).toLowerCase()}`);
  $("bars").replaceChildren(...slots.map((count, index) => {
    const item = node("li", "bar" + (index === current ? " now" : "")), track = node("div", "track");
    const hours = `${String(index * 3).padStart(2, "0")}–${String(index * 3 + 3).padStart(2, "0")}`;
    track.style.setProperty("--h", `${count ? Math.max(14, count / peak * 100) : 0}%`);
    track.append(node("span", "fill"), node("span", "count", String(count)));
    item.setAttribute("aria-label", `${hours} óra: ${count} esemény${index === current ? " (most)" : ""}`);
    item.append(track, node("span", "slot", hours));
    return item;
  }));
}
function renderCalendar() {
  const today = midnight(new Date()), monday = addDays(selectedDay, -((selectedDay.getDay() + 6) % 7));
  const title = selectedDay.toLocaleDateString("hu-HU", {year: "numeric", month: "long"});
  text("calendar-heading", title.charAt(0).toUpperCase() + title.slice(1));
  $("week-next").disabled = addDays(monday, 7) > today;
  if (!changed("calendar", [monday.getTime(), selectedDay.getTime(), today.getTime()])) return;
  $("days").replaceChildren(...Array.from({length: 7}, (_, index) => {
    const day = addDays(monday, index), button = node("button", "day" + (sameDay(day, today) ? " today" : ""));
    button.type = "button";
    button.disabled = day > today;
    button.setAttribute("aria-pressed", String(sameDay(day, selectedDay)));
    button.setAttribute("aria-label", day.toLocaleDateString("hu-HU", {month: "long", day: "numeric", weekday: "long"}));
    button.append(node("small", null, DAY_NAMES[day.getDay()]), node("strong", null, String(day.getDate())));
    button.addEventListener("click", () => selectDay(day));
    return button;
  }));
}
function selectDay(day) {
  const today = midnight(new Date());
  selectedDay = midnight(day > today ? today : day);
  updateDay().catch(() => error("Nem sikerült betölteni a napot. Próbáld újra."));
}

// --- events ------------------------------------------------------------------------------------------------------
function eventState(event) {
  if (event.notification_status === "failed") return {pill: "Értesítés hiba", kind: "warn", ring: "done"};
  if (event.label === "person" && event.activity_status && event.activity_status !== "disabled") {
    const status = event.activity_status;
    if (status === "completed") {
      const suspicious = (event.activity_verdict || event.activity_summary || "").startsWith("Gyanús");
      return {pill: suspicious ? "Gyanús" : "Nem gyanús", kind: suspicious ? "warn" : "done", ring: "done"};
    }
    if (status === "captured") return {pill: "Csendes", kind: "muted", ring: "done"};
    if (status === "failed") return {pill: "Elemzés hiba", kind: "warn", ring: "done"};
    return {pill: status === "recording" ? "Klip készül" : status === "analyzing" ? "Elemzés…" : "Sorban", kind: "go", ring: "spin"};
  }
  const states = {sent: ["Értesítve", "done", "done"], silent: ["Csendes", "muted", "done"], queued: ["Sorban", "go", "spin"], cooldown: ["Szünetel", "muted", "done"], disabled: ["Rögzítve", "muted", "done"]};
  const [pill, kind, ring] = states[event.notification_status] || [event.notification_status, "muted", "done"];
  return {pill, kind, ring};
}
function ringIcon(state) {
  const wrap = node("span", "ring" + (state.ring === "spin" ? " spin" : ""));
  wrap.setAttribute("aria-hidden", "true");
  if (state.ring === "spin") wrap.innerHTML = arc(14, 3, [{share: 0.7, color: "#fff"}], "rgba(255,255,255,.14)");
  else {
    const color = state.kind === "warn" ? "#ff8576" : state.kind === "muted" ? "rgba(255,255,255,.5)" : "#fff";
    wrap.innerHTML = `<svg viewBox="0 0 34 34"><circle cx="17" cy="17" r="16" fill="rgba(255,255,255,.1)" stroke="none"/><path d="m11 17.5 4 4 8-8.5" stroke="${color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  return wrap;
}
function link(href, label) { const a = node("a", null, label); a.href = href; a.target = "_blank"; a.rel = "noopener"; return a; }
function eventRow(event) {
  const state = eventState(event), row = node("li", "event"), when = new Date(event.timestamp);
  const thumb = link(event.snapshot_url, null);
  thumb.className = "thumb";
  thumb.setAttribute("aria-label", `Snapshot: ${name(event.label)}, ${event.zone}, ${stamp(event.timestamp)}`);
  const image = node("img");
  image.src = event.thumbnail_url || event.snapshot_url; image.alt = ""; image.width = 76; image.height = 57; image.loading = "lazy";
  image.addEventListener("error", () => thumb.classList.add("missing"), {once: true});  // e.g. archived or lost in a crash
  thumb.append(image);
  const body = node("div"), heading = node("h3", null, name(event.label) + (event.is_test ? " · TESZT" : "") + " ");
  heading.append(node("span", null, `· ${event.zone}`));
  body.append(heading, node("p", null, `${sameDay(when, new Date()) ? clock(when) : stamp(when)} · ${Math.round(event.confidence * 100)}% biztos`));
  if (event.activity_summary) body.append(node("p", "summary", event.activity_summary.split(" — ").slice(1).join(" — ") || event.activity_summary));
  const end = node("div", "event-end"), links = node("div", "links");
  if (event.montage_url) links.append(link(event.montage_url, "Montázs"));
  else if (event.montage_status === "recording") links.append(node("span", null, "Montázs készül…"));
  if (event.activity_video_url) links.append(link(event.activity_video_url, event.activity_clip_seconds > 10 ? `Klip (${event.activity_clip_seconds} mp)` : "Klip"));
  if (event.recording_url) links.append(link(event.recording_url, "Videó"));
  else if (event.recording_status === "recording") links.append(node("span", null, "Videó készül…"));
  const pill = node("span", "pill", state.pill);
  pill.dataset.kind = state.kind;
  end.append(pill);
  if (links.children.length) end.append(links);
  row.append(ringIcon(state), thumb, body, end);
  return row;
}
function renderEvents(target, events, emptyText) {
  const fields = ["id", "activity_status", "notification_status", "montage_status", "recording_status", "activity_summary"];
  if (!changed(target, events.map(event => fields.map(field => event[field])))) return;
  $(target).replaceChildren(...(events.length ? events.map(eventRow) : [node("li", "empty", emptyText)]));
}
async function updateLatest() {
  const data = await get("/api/events?limit=6");
  renderEvents("latest", data.events, "Még nincs esemény. Mozgás és legalább két megerősített AI-találat után itt jelennek meg.");
}
function renderFilters() {
  const options = [["", "Összes"], ...tracked.map(label => [label, name(label)])];
  if (filter && !tracked.includes(filter)) filter = "";
  $("filters").replaceChildren(...options.map(([value, label]) => {
    const button = node("button", null, label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(value === filter));
    button.addEventListener("click", () => {
      filter = value; offset = 0;
      for (const other of $("filters").children) other.setAttribute("aria-pressed", String(other === button));
      refresh();
    });
    return button;
  }));
}
async function updateEventsView() {
  const data = await get(`/api/events?limit=${LIMIT}&offset=${offset}` + (filter ? `&label=${encodeURIComponent(filter)}` : ""));
  renderEvents("event-list", data.events, filter ? "Ehhez az objektumhoz még nincs esemény." : "Még nincs esemény. Mozgás és legalább két megerősített AI-találat után itt jelennek meg.");
  text("page-number", `${offset / LIMIT + 1}. oldal`);
  $("previous").disabled = offset === 0;
  $("next").disabled = data.events.length < LIMIT;
}

// --- tablet view: the essentials beside the stream, for a screen that stays on all day ------------------------------
function updateClock() {
  const now = new Date();
  text("t-time", now.toLocaleTimeString("hu-HU", {hour: "2-digit", minute: "2-digit"}));
  text("t-date", now.toLocaleDateString("hu-HU", {weekday: "long", month: "long", day: "numeric"}));
}
function updateTablet(s) {
  const online = s.camera === "connected", starting = s.camera === "connecting", h = s.hardware || {}, w = s.watchdog || {};
  text("t-camera", online ? "Kamera online" : starting ? "Kamera indul…" : "Kamera offline");
  $("t-camera").dataset.tone = online ? "positive" : starting ? "muted" : "danger";
  text("t-motion", s.motion ? "Mozgás észlelve" : "Nincs mozgás");
  $("t-motion").classList.toggle("active", Boolean(s.motion));
  const [label, kind] = !w.installed ? ["Nincs telepítve", "muted"] : w.stale ? ["Nem jelentkezik", "warn"] : WATCHDOG[w.state] || [w.state, "muted"];
  text("t-watchdog", label); $("t-watchdog").dataset.tone = kind === "done" ? "positive" : kind === "muted" ? "muted" : "danger";
  text("t-ai", s.ai_inference_ms == null ? "várakozás" : `${number(s.ai_inference_ms)} ms`);
  const hot = h.temperature_c >= 75;
  text("t-cpu", [h.cpu_percent == null ? null : `${Math.round(h.cpu_percent)}%`, h.temperature_c == null ? null : `${number(h.temperature_c)} °C`].filter(Boolean).join(" · ") || "—");
  $("t-cpu").dataset.tone = hot ? "danger" : "positive";
}
async function updateTabletDay() {
  const start = midnight(new Date()), end = addDays(start, 1), timeline = await get(range(start, end));
  const slots = Array.from({length: 8}, () => 0), slotSeconds = (end - start) / 8000;
  for (const bucket of timeline.buckets) slots[Math.min(7, Math.floor(bucket.index * timeline.bucket_seconds / slotSeconds))] += bucket.count;
  const peak = Math.max(1, ...slots), current = Math.min(7, Math.floor((Date.now() - start.getTime()) / 1000 / slotSeconds));
  text("t-count", String(timeline.total));
  if (!changed("t-bars", [slots, current])) return;
  $("t-bars").setAttribute("aria-label", "Mai események 3 órás szakaszokban");
  $("t-bars").replaceChildren(...slots.map((count, index) => {
    const item = node("li", index === current ? "now" : ""), fill = node("span");
    fill.style.setProperty("--h", `${count ? Math.max(12, count / peak * 100) : 0}%`);
    item.setAttribute("aria-label", `${String(index * 3).padStart(2, "0")}–${String(index * 3 + 3).padStart(2, "0")} óra: ${count} esemény`);
    item.append(fill);
    return item;
  }));
}
async function updateTabletEvents() {
  const events = (await get("/api/events?limit=3")).events;
  if (!changed("t-events", events.map(e => [e.id, e.activity_status, e.notification_status]))) return;
  $("t-events").replaceChildren(...(events.length ? events.map(event => {
    const state = eventState(event), item = node("li"), thumb = link(event.snapshot_url, null), image = node("img"), body = node("div");
    thumb.className = "thumb";
    thumb.setAttribute("aria-label", `Snapshot: ${name(event.label)}, ${event.zone}, ${stamp(event.timestamp)}`);
    image.src = event.thumbnail_url || event.snapshot_url; image.alt = ""; image.width = 84; image.height = 63;
    image.addEventListener("error", () => thumb.classList.add("missing"), {once: true});
    thumb.append(image);
    const pill = node("span", "pill", state.pill);
    pill.dataset.kind = state.kind;
    const when = new Date(event.timestamp);
    body.append(node("strong", null, `${name(event.label)} · ${event.zone}`), node("p", null, `${sameDay(when, new Date()) ? clock(when) : stamp(when)} · ${Math.round(event.confidence * 100)}%`), pill);
    item.append(thumb, body);
    return item;
  }) : [node("li", "empty", "Ma még nincs esemény.")]));
}
// Keep the screen on. Browsers allow this only on HTTPS (for example Tailscale Serve) or localhost.
let wakeLock = null;
async function keepAwake() {
  if (document.hidden || wakeLock) return;
  if (!("wakeLock" in navigator)) {
    $("t-wake").hidden = false;
    $("t-wake").textContent = window.isSecureContext ? "Ez a böngésző nem tudja ébren tartani a kijelzőt; kapcsold ki a tablet automatikus zárolását." : "A kijelző ébren tartásához HTTPS-cím kell (például Tailscale Serve), vagy kapcsold ki a tablet automatikus zárolását.";
    return;
  }
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
    $("t-wake").hidden = true;
  } catch {
    $("t-wake").hidden = false;
    $("t-wake").textContent = "A kijelző ébren tartását a böngésző most nem engedte; kapcsold ki a tablet automatikus zárolását.";
  }
}
function setupTablet() {
  document.body.classList.add("tablet");
  $("tablet-panel").hidden = false;
  document.title = "Camera Stack · Tablet";
  $("t-exit").setAttribute("href", home.getAttribute("href"));
  updateClock();
  setInterval(updateClock, 15000);
  keepAwake();
  document.addEventListener("visibilitychange", keepAwake);
  const root = document.documentElement, canFullscreen = document.fullscreenEnabled || document.webkitFullscreenEnabled;
  $("t-fullscreen").hidden = !canFullscreen;
  const fullscreen = () => document.fullscreenElement || document.webkitFullscreenElement;
  const label = () => { const value = fullscreen() ? "Kilépés a teljes képernyőből" : "Teljes képernyő"; $("t-fullscreen").setAttribute("aria-label", value); $("t-fullscreen").title = value; };
  $("t-fullscreen").addEventListener("click", () => {
    if (fullscreen()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else (root.requestFullscreen || root.webkitRequestFullscreen).call(root);
  });
  document.addEventListener("fullscreenchange", label);
  document.addEventListener("webkitfullscreenchange", label);
  // A few pixels of drift every few minutes, so a static layout does not burn into the screen.
  const drift = [[0, 0], [3, 2], [-2, 3], [2, -3], [-3, -2]];
  let step = 0;
  setInterval(() => { step = (step + 1) % drift.length; $("tablet-panel").closest(".window").style.translate = `${drift[step][0]}px ${drift[step][1]}px`; }, 4 * 60000);
  // A fresh page once a day keeps a long-running player and browser tab healthy.
  const reloadAt = new Date(); reloadAt.setHours(4, 0, 0, 0);
  if (reloadAt <= new Date()) reloadAt.setDate(reloadAt.getDate() + 1);
  setTimeout(() => location.reload(), reloadAt - new Date());
}

// --- page and refresh ---------------------------------------------------------------------------------------------
async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const s = await get("/api/status");
    $("test-banner").hidden = !s.source?.is_test;
    if (JSON.stringify(s.tracked_objects) !== JSON.stringify(tracked)) { tracked = s.tracked_objects || []; renderFilters(); }
    if (isEvents) await updateEventsView();
    else if (isTablet) {
      updateLive(s); updateTablet(s);
      await Promise.all([updateTabletEvents(), updateTabletDay()]);
    } else {
      updateLive(s); updateTiles(s); updateWatchdog(s.watchdog); updateIntegrations(s); updateHardware(s.hardware); updateSpotlight(s.last_event);
      await Promise.all([updateLatest(), updateDay()]);
    }
    error("");
  } catch {
    error("Nem sikerült frissíteni. Ellenőrizd az alkalmazás kapcsolatát, majd próbáld újra.");
  } finally {
    loading = false;
  }
}
window.refresh = refresh;  // the standalone demo's simulated backend calls this when something happens

$("dashboard").hidden = isEvents;
$("events-view").hidden = !isEvents;
if (isEvents) { text("title", "Eseménynapló"); document.title = "Camera Stack · Események"; }
const home = document.querySelector('.dock a[data-nav="home"]');
for (const item of document.querySelectorAll(".dock a")) {
  if (item.dataset.nav === page) item.setAttribute("aria-current", "page");
  // Section shortcuts live on the overview; from the event log they open it at that card.
  if (item.dataset.nav === "section") item.addEventListener("click", event => { if (isEvents) { event.preventDefault(); location.href = home.getAttribute("href") + item.getAttribute("href"); } });
}
if (isTablet) setupTablet();
$("spot-image").addEventListener("error", () => $("spot-link").classList.add("missing"));
$("rotate").addEventListener("click", rotate);
$("wd-toggle").addEventListener("click", toggleWatchdog);
$("week-prev").addEventListener("click", () => selectDay(addDays(selectedDay, -7)));
$("week-next").addEventListener("click", () => selectDay(addDays(selectedDay, 7)));
$("previous").addEventListener("click", () => { offset = Math.max(0, offset - LIMIT); refresh(); });
$("next").addEventListener("click", () => { offset += LIMIT; refresh(); });
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
