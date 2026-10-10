"use strict";
const $ = id => document.getElementById(id);
const isEvents = location.pathname === "/events";
$("live-view").hidden = isEvents;
$(isEvents ? "nav-events" : "nav-live").classList.add("active");
$(isEvents ? "nav-events" : "nav-live").setAttribute("aria-current", "page");
if (isEvents) { $("events-heading").textContent = "Eseménynapló"; document.title = "Camera Stack · Események"; }
let offset = 0, loading = false, liveUrl = null, eventCount = 0, eventsSignature = null;
const limit = 24;
const date = value => value ? new Date(value).toLocaleString("hu-HU") : "—";
const enabled = value => value ? "Bekapcsolva" : "Kikapcsolva";
const bytes = value => `${(value / 1024 ** 3).toLocaleString("hu-HU", {maximumFractionDigits:1})} GiB`;
const numeric = value => value.toLocaleString("hu-HU", {maximumFractionDigits:1});
function hardwareBar(id, value, warning = false) {
  const bar = $(id);
  bar.style.transform = `scaleX(${value == null ? 0 : Math.max(0, Math.min(100, value)) / 100})`;
  bar.classList.toggle("warning", warning);
}
function updateHardware(h) {
  $("hardware-panel").hidden = isEvents || !h?.is_raspberry_pi;
  if (!h?.is_raspberry_pi) return;
  text("hardware-model", h.model.replace(/^Raspberry Pi /, "Pi "));
  text("hw-temperature", h.temperature_c == null ? "—" : `${numeric(h.temperature_c)} °C`);
  text("hw-temperature-detail", h.temperature_c == null ? "Nem elérhető" : h.temperature_c >= 75 ? "Magas" : "CPU");
  text("hw-cpu", h.cpu_percent == null ? "Mérés…" : `${numeric(h.cpu_percent)}%`);
  text("hw-cores", h.cpu_cores == null ? "—" : `${h.cpu_cores} mag`);
  text("hw-frequency", h.undervoltage ? "lassítva (táp)" : h.cpu_frequency_mhz == null ? "—" : `${Math.round(h.cpu_frequency_mhz)} MHz`);
  text("hw-load", h.load_average ? h.load_average.map(v => v.toLocaleString("hu-HU", {minimumFractionDigits:2,maximumFractionDigits:2})).join(" / ") : "—");
  text("hw-memory", h.memory ? `${numeric(h.memory.used_percent)}%` : "—");
  text("hw-memory-detail", h.memory ? `${bytes(h.memory.used_bytes)} / ${bytes(h.memory.total_bytes)}` : "Nem elérhető");
  text("hw-disk", h.disk ? bytes(h.disk.free_bytes) : "—");
  text("hw-disk-detail", h.disk ? `${numeric(h.disk.used_percent)}% foglalt · ${bytes(h.disk.total_bytes)}` : "Nem elérhető");
  hardwareBar("hw-temperature-bar", h.temperature_c, h.temperature_c >= 75);
  hardwareBar("hw-cpu-bar", h.cpu_percent, h.cpu_percent >= 90);
  hardwareBar("hw-memory-bar", h.memory?.used_percent, h.memory?.used_percent >= 90);
  hardwareBar("hw-disk-bar", h.disk?.used_percent, Boolean(h.disk && h.disk.free_bytes < h.disk.total_bytes * .1));
  const minutes = Math.floor((h.uptime_seconds || 0) / 60);
  text("hw-uptime", h.uptime_seconds == null ? "—" : `${minutes >= 1440 ? `${Math.floor(minutes / 1440)} n ` : ""}${Math.floor(minutes % 1440 / 60)} ó ${minutes % 60} p`);
  $("hardware-warning").hidden = !h.warnings?.length;
  text("hardware-warning", h.warnings?.join(" · ") || "");
}
const stateNames = {silent:"csendes (alacsony megbízhatóság, nincs értesítés)", sent:"elküldve", failed:"hiba", finished:"kész", uploading:"feltöltés…", idle:"várakozás", queued:"sorban", cooldown:"cooldown", disabled:"kikapcsolva", recording:"rögzítés…", completed:"kész"};
// Emoji give an at-a-glance state; the adjacent text still names it, so they stay hidden from screen readers.
const stateIcons = {silent:"🔕", captured:"🎞️", sent:"✅", completed:"✅", finished:"✅", failed:"❌", queued:"⏳", recording:"⏳", analyzing:"⏳", uploading:"⏳", cooldown:"⏸️", disabled:"➖"};
const verdictIcons = {"Gyanús":"🛑", "Nem gyanús":"✅"};
function statusLine(tag, icon, label, className) { const line = node(tag, null, className); if (icon) { const mark = node("span", icon, "status-icon"); mark.setAttribute("aria-hidden", "true"); line.append(mark); } line.append(label); return line; }
function error(message) { $("error").textContent = message; $("error").hidden = !message; }
async function get(url) { const result = await fetch(url, {cache:"no-store", signal:AbortSignal.timeout(10000)}); if (!result.ok) throw new Error("HTTP " + result.status); return result.json(); }
function text(id, value) { $(id).textContent = value; }
function tone(id, value) { $(id).dataset.tone = value; }
let rotation = 0, streamSize = "";
// The viewer follows the stream's real aspect ratio (e.g. 4:3 full sensor), also when CSS rotates it.
function applyStreamSize(width, height) {
  streamSize = `${width}x${height}`;
  const style = $("video-surface").style;
  style.setProperty("--frame-aspect", `${width} / ${height}`);
  style.setProperty("--frame-aspect-rotated", `${height} / ${width}`);
  style.setProperty("--portrait-factor", String(height / width));
  style.setProperty("--rot-width", `${width / height * 100}%`);
  style.setProperty("--rot-height", `${height / width * 100}%`);
}
function applyRotation(value) {
  rotation = value;
  $("video-surface").dataset.rotation = String(value);
  text("rotate", `↻ ${value}°`);
  $("rotate").setAttribute("aria-label", `Kép forgatása, jelenleg ${value} fok`);
}
async function rotate() {
  const next = (rotation + 90) % 360;
  $("rotate").disabled = true;
  try {
    const result = await fetch("/api/settings/rotation", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({rotation:next}), signal:AbortSignal.timeout(10000)});
    if (!result.ok) throw new Error("HTTP " + result.status);
    applyRotation((await result.json()).rotation); error("");
  } catch { error("Nem sikerült elforgatni a képet. Próbáld újra."); }
  finally { $("rotate").disabled = false; }
}
const watchdogStates = {ok:["✅ Figyel, minden rendben", "positive"], warning:["⚠️ Probléma, figyelés", "danger"], recovering:["🔄 Helyreállítás folyamatban", "danger"], rebooting:["🔄 Újraindítás", "danger"], blocked:["⛔ Beavatkozás letiltva", "danger"]};
const watchdogActions = {"restart-source":"kameraforrás újraindítva", reboot:"Pi újraindítva", "dry-run":"próba"};
let watchdogAuto = true;
function updateWatchdog(w) {
  const installed = Boolean(w?.installed);
  $("wd-toggle").hidden = !installed;
  if (!installed) {
    text("wd-state", "Nincs telepítve"); tone("wd-state", "muted");
    for (const id of ["wd-firmware", "wd-camera", "wd-checked", "wd-action", "wd-reboots"]) text(id, "—");
    text("wd-message", ""); return;
  }
  const [label, stateTone] = w.stale ? ["⚠️ Nem jelentkezik", "danger"] : watchdogStates[w.state] || [w.state, "muted"];
  text("wd-state", label); tone("wd-state", stateTone);
  text("wd-firmware", w.firmware_ok ? "Rendben" : "Nem válaszol"); tone("wd-firmware", w.firmware_ok ? "positive" : "danger");
  text("wd-camera", w.camera_ok ? "Van kép" : "Nincs kép"); tone("wd-camera", w.camera_ok ? "positive" : "danger");
  text("wd-checked", date(w.updated_at));
  text("wd-action", w.last_action ? `${watchdogActions[w.last_action.action] || w.last_action.action} · ${date(w.last_action.at)}` : "még nem volt");
  text("wd-reboots", `${w.reboots_24h ?? 0} / 3`);
  text("wd-message", w.state === "ok" && !w.stale ? "" : w.message || "");
  watchdogAuto = w.auto_reboot !== false;
  text("wd-toggle", `Automatikus újraindítás: ${watchdogAuto ? "be" : "ki"}`);
  $("wd-toggle").setAttribute("aria-pressed", String(watchdogAuto));
}
async function toggleWatchdog() {
  $("wd-toggle").disabled = true;
  try {
    const result = await fetch("/api/settings/watchdog", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({auto_reboot:!watchdogAuto}), signal:AbortSignal.timeout(10000)});
    if (!result.ok) throw new Error("HTTP " + result.status);
    watchdogAuto = (await result.json()).auto_reboot;
    text("wd-toggle", `Automatikus újraindítás: ${watchdogAuto ? "be" : "ki"}`); $("wd-toggle").setAttribute("aria-pressed", String(watchdogAuto)); error("");
  } catch { error("Nem sikerült menteni a helyreállítási beállítást. Próbáld újra."); }
  finally { $("wd-toggle").disabled = false; }
}
async function updateStatus() {
  const s = await get("/api/status");
  if (typeof s.rotation === "number" && s.rotation !== rotation) applyRotation(s.rotation);
  if (Array.isArray(s.frame_size)) {
    // Reported frames are already turned by the app; the embedded player shows the unturned stream.
    const [width, height] = s.rotation % 180 ? [s.frame_size[1], s.frame_size[0]] : s.frame_size;
    if (`${width}x${height}` !== streamSize) applyStreamSize(width, height);
  }
  $("test-stream-banner").hidden = !s.source?.is_test;
  updateHardware(s.hardware);
  text("connection", s.camera === "connected" ? "Kamera online" : "Kamera offline");
  $("connection").className = "connection " + (s.camera === "connected" ? "online" : "offline");
  text("camera-state", s.camera === "connected" ? "Online" : "Újracsatlakozás…");
  tone("camera-state", s.camera === "connected" ? "positive" : "danger");
  text("motion-status", s.motion ? "Mozgás észlelve" : "Nincs aktív mozgás");
  $("motion-status").classList.toggle("motion-active", Boolean(s.motion));
  text("device", `${s.device} · ${s.ai}`); text("cpu", s.cpu || "—");
  text("inference", s.ai_inference_ms === null ? "Várakozás mozgásra" : `${s.ai_inference_ms} ms`);
  text("fps", `${s.camera_fps} / ${s.motion_fps}`); text("ai-fps", String(s.ai_fps)); text("latency", `${s.processing_latency_ms} ms`);
  text("notifications", enabled(s.notifications_enabled)); text("backup", enabled(s.backup_enabled));
  text("upload", s.backup_enabled ? (stateNames[s.upload_status] || s.upload_status) : "Kikapcsolva");
  text("recording", enabled(s.record_events)); text("continuous", enabled(s.continuous_recording));
  text("activity-analysis", enabled(s.activity_analysis_enabled));
  for (const [id, active] of [["notifications", s.notifications_enabled], ["backup", s.backup_enabled], ["recording", s.record_events], ["continuous", s.continuous_recording], ["activity-analysis", s.activity_analysis_enabled]]) tone(id, active ? "positive" : "muted");
  tone("upload", s.upload_status === "failed" && s.backup_enabled ? "danger" : s.backup_enabled ? "positive" : "muted");
  text("retention", `${s.retention_days} nap`);
  text("storage-status", s.storage_low ? "Kevés tárhely · mentés szünetel" : "Rendben");
  tone("storage-status", s.storage_low ? "danger" : "positive");
  updateWatchdog(s.watchdog);
  const classes = JSON.stringify(s.tracked_objects || []);
  if ($("filter").dataset.classes !== classes) {
    const selection = $("filter").value;
    $("filter").replaceChildren(new Option("Összes", ""), ...(s.tracked_objects || []).map(label => new Option(label, label)));
    $("filter").value = (s.tracked_objects || []).includes(selection) ? selection : "";
    $("filter").dataset.classes = classes;
  }
  text("last-detection", s.last_detection ? `AI: ${s.last_detection.label} · ${Math.round(s.last_detection.confidence * 100)}% · ${date(s.last_detection.timestamp)}` : "AI: még nincs találat");
  text("latest-event", s.last_event ? `${s.last_event.label} · ${Math.round(s.last_event.confidence * 100)}% · ${s.last_event.zone} · ${date(s.last_event.timestamp)}${s.last_event.activity_summary ? ` · ${s.last_event.activity_summary}` : ""}` : "Még nem történt validált esemény.");
  if (s.live_stream_url !== liveUrl) {
    liveUrl = s.live_stream_url;
    $("video-surface").querySelector("iframe")?.remove();
    $("stream-empty").hidden = Boolean(liveUrl);
    if (liveUrl) {
      const frame = document.createElement("iframe"); frame.src = liveUrl; frame.title = "Élő kamera lejátszó";
      frame.allow = "autoplay; fullscreen; picture-in-picture"; frame.referrerPolicy = "no-referrer"; frame.allowFullscreen = true;
      $("video-surface").append(frame); text("live-label", "Webes lejátszó");
    } else { text("live-label", "Nincs webes stream"); }
  }
  text("updated", `Frissítve: ${new Date().toLocaleTimeString("hu-HU")}`);
}
function node(tag, content, className) { const element = document.createElement(tag); if (content) element.textContent = content; if (className) element.className = className; return element; }
let timelineSignature = null;
let timelineTipTimer;
function hideTimelineTip() { clearTimeout(timelineTipTimer); $("timeline-tooltip").hidden = true; }
function scheduleTimelineTipHide() { clearTimeout(timelineTipTimer); timelineTipTimer = setTimeout(hideTimelineTip, 180); }
function showTimelineTip(anchor, bucket) {
  clearTimeout(timelineTipTimer);
  const event = bucket.event, tip = $("timeline-tooltip");
  const src = event.thumbnail_url || event.snapshot_url;
  if ($("timeline-image").getAttribute("src") !== src) $("timeline-image").src = src;
  $("timeline-image").alt = `${event.label} · ${event.zone}`;
  text("timeline-label", `${event.label}${event.is_test ? " · TESZT" : ""} · ${Math.round(event.confidence * 100)}%`);
  text("timeline-time", `${date(event.timestamp)} · ${bucket.count} esemény / 15 p`);
  tip.hidden = false;
  const r = anchor.getBoundingClientRect();
  tip.style.left = `${Math.max(8, Math.min(innerWidth - 208, r.left + r.width / 2 - 100))}px`;
  tip.style.top = `${r.top >= tip.offsetHeight + 12 ? r.top - tip.offsetHeight - 8 : Math.min(innerHeight - tip.offsetHeight - 8, r.bottom + 8)}px`;
}
async function updateTimeline() {
  if (isEvents) return;
  const now = new Date(), start = new Date(now), end = new Date(now);
  start.setHours(0, 0, 0, 0); end.setHours(24, 0, 0, 0);
  const startSeconds = start.getTime() / 1000, endSeconds = end.getTime() / 1000;
  const duration = endSeconds - startSeconds;
  const data = await get(`/api/timeline?start=${startSeconds}&end=${endSeconds}`);
  text("timeline-summary", `${now.toLocaleDateString("hu-HU", {month:"long", day:"numeric", weekday:"long"})} · ${data.total} esemény`);
  $("timeline-now").style.left = `${(now.getTime() / 1000 - startSeconds) / duration * 100}%`;
  $("timeline-empty").hidden = data.total !== 0;
  const signature = JSON.stringify(data);
  if (signature === timelineSignature) return;
  hideTimelineTip();
  const count = Math.ceil(duration / data.bucket_seconds);
  const counts = new Map(data.buckets.map(b => [b.index, b.count]));
  const peak = Math.max(1, ...counts.values());
  const bins = document.createDocumentFragment(), ticks = document.createDocumentFragment(), axis = document.createDocumentFragment(), links = document.createDocumentFragment();
  for (let i = 0; i < count; i++) {
    const bin = node("span"); bin.style.height = `${(counts.get(i) || 0) / peak * 100}%`; bins.append(bin);
  }
  for (const bucket of data.buckets) {
    const e = bucket.event, marker = node("a", null, "timeline-tick");
    marker.href = e.snapshot_url; marker.target = "_blank"; marker.rel = "noopener";
    marker.style.left = `${(new Date(e.timestamp).getTime() / 1000 - startSeconds) / duration * 100}%`;
    marker.setAttribute("aria-label", `${date(e.timestamp)} · ${bucket.count} esemény ebben a 15 percben; legutóbbi: ${e.label}, ${Math.round(e.confidence * 100)}%. Snapshot megnyitása.`);
    for (const type of ["mouseenter", "focus"]) marker.addEventListener(type, () => showTimelineTip(marker, bucket));
    for (const type of ["mouseleave", "blur"]) marker.addEventListener(type, scheduleTimelineTipHide);
    marker.addEventListener("keydown", event => { if (event.key === "Escape") hideTimelineTip(); });
    ticks.append(marker);
    const item = node("li"), link = node("a");
    link.href = e.snapshot_url; link.target = "_blank"; link.rel = "noopener";
    const time = new Date(e.timestamp).toLocaleTimeString("hu-HU", {hour:"2-digit", minute:"2-digit"});
    link.append(node("strong", `${time} · ${e.label}${e.is_test ? " · TESZT" : ""}`), node("span", `${bucket.count} esemény · ${Math.round(e.confidence * 100)}% ↗`));
    link.setAttribute("aria-label", marker.getAttribute("aria-label")); item.append(link); links.append(item);
  }
  for (let hour = 0; hour <= 24; hour += 3) {
    const tickDate = new Date(start); tickDate.setHours(hour);
    const label = node("span", String(hour).padStart(2, "0"));
    label.style.left = `${(tickDate.getTime() / 1000 - startSeconds) / duration * 100}%`; axis.append(label);
  }
  $("timeline-bins").replaceChildren(bins); $("timeline-ticks").replaceChildren(ticks); $("timeline-axis").replaceChildren(axis);
  $("timeline-links").replaceChildren(links); $("timeline-browse").hidden = !data.total;
  text("timeline-browse-label", `Találatok böngészése · ${data.buckets.length} időszak`);
  timelineSignature = signature;
}
window.addEventListener("scroll", hideTimelineTip, {passive:true});
window.addEventListener("resize", hideTimelineTip);
$("timeline-tooltip").addEventListener("mouseenter", () => clearTimeout(timelineTipTimer));
$("timeline-tooltip").addEventListener("mouseleave", scheduleTimelineTipHide);
document.addEventListener("keydown", event => { if (event.key === "Escape") hideTimelineTip(); });
async function updateEvents() {
  const filter = $("filter").value;
  const data = await get(`/api/events?limit=${limit}&offset=${offset}` + (filter ? `&label=${encodeURIComponent(filter)}` : ""));
  const signature = JSON.stringify([offset, filter, data.events]);
  eventCount = data.events.length;
  if (signature === eventsSignature) return;
  const fragment = document.createDocumentFragment();
  for (const event of data.events) {
    const row = node("article", null, "event-row");
    const thumbnail = node("a"); thumbnail.href = event.snapshot_url; thumbnail.target = "_blank"; thumbnail.rel = "noopener";
    const image = node("img"); image.src = event.thumbnail_url || event.snapshot_url; image.loading = "lazy"; image.width = 132; image.height = 80; image.alt = `${event.label} a(z) ${event.zone} zónában`; thumbnail.append(image);
    const body = node("div"); const heading = node("h2", `${event.label}${event.is_test ? " · TESZT" : ""}`); heading.append(node("span", `${Math.round(event.confidence * 100)}%`, "confidence"));
    const notification = statusLine("p", stateIcons[event.notification_status], `Értesítés: ${stateNames[event.notification_status] || event.notification_status}`, "event-notification");
    notification.dataset.tone = event.notification_status === "failed" ? "danger" : event.notification_status === "sent" ? "positive" : "muted";
    body.append(heading, node("p", `${event.zone} · ${date(event.timestamp)}`), notification);
    if (event.activity_status && event.activity_status !== "disabled") {
      const states = {queued:"sorban", recording:"10 mp-es klip készül…", analyzing:"elemzés folyamatban…", failed:"nem sikerült", captured:"klip rögzítve, elemzés nélkül (csendes esemény)"};
      const verdict = event.activity_verdict || Object.keys(verdictIcons).find(name => event.activity_summary?.startsWith(name));
      const activityIcon = event.activity_status === "completed" ? verdictIcons[verdict] || "ℹ️" : stateIcons[event.activity_status];
      body.append(statusLine("p", activityIcon, event.activity_summary ? `Videóelemzés: ${event.activity_summary}` : `Videóelemzés: ${states[event.activity_status] || event.activity_status}`, "event-activity"));
      if (event.activity_notification_status && event.activity_notification_status !== "disabled") {
        body.append(statusLine("p", stateIcons[event.activity_notification_status], `Elemzési értesítés: ${stateNames[event.activity_notification_status] || event.activity_notification_status}`, "event-notification"));
      }
    }
    const actions = node("div", null, "event-actions"); const snapshot = node("a", "Snapshot ↗"); snapshot.href = event.snapshot_url; snapshot.target = "_blank"; snapshot.rel = "noopener"; actions.append(snapshot);
    if (event.recording_url) { const video = node("a", "Videó ↗"); video.href = event.recording_url; video.target = "_blank"; video.rel = "noopener"; actions.append(video); }
    if (event.montage_url) { const montage = node("a", "Montázs ↗"); montage.href = event.montage_url; montage.target = "_blank"; montage.rel = "noopener"; actions.append(montage); }
    else if (event.montage_status === "recording") { actions.append(statusLine("span", stateIcons.recording, "Montázs: készül…")); }
    if (event.activity_video_url) { const video = node("a", event.activity_clip_seconds > 10 ? `Elemzett klip (${event.activity_clip_seconds} mp) ↗` : "Elemzett klip ↗"); video.href = event.activity_video_url; video.target = "_blank"; video.rel = "noopener"; actions.append(video); }
    else if (event.recording_status !== "disabled") { actions.append(statusLine("span", stateIcons[event.recording_status], `Klip: ${stateNames[event.recording_status] || event.recording_status}`)); }
    row.append(thumbnail, body, actions); fragment.append(row);
  }
  if (!data.events.length) fragment.append(node("p", filter ? "Ehhez az objektumhoz még nincs esemény." : "Még nincs esemény. Mozgás és legalább két megerősített AI-találat után itt jelennek meg a snapshotok.", "empty"));
  $("events-list").replaceChildren(fragment); eventCount = data.events.length;
  eventsSignature = signature;
  text("page-number", `${offset / limit + 1}. oldal`);
}
async function refresh() {
  if (loading) return;
  loading = true;
  for (const id of ["refresh", "filter", "previous", "next"]) $(id).disabled = true;
  text("refresh", "Frissítés…");
  try { await updateStatus(); await Promise.all([updateEvents(), updateTimeline()]); error(""); }
  catch { error("Nem sikerült frissíteni. Ellenőrizd az alkalmazás kapcsolatát, majd próbáld újra."); }
  finally {
    loading = false; $("refresh").disabled = false; $("filter").disabled = false;
    $("previous").disabled = offset === 0; $("next").disabled = eventCount < limit;
    text("refresh", "Frissítés");
  }
}
$("filter").addEventListener("change", () => { offset = 0; refresh(); }); $("refresh").addEventListener("click", refresh);
$("previous").addEventListener("click", () => { offset = Math.max(0, offset - limit); refresh(); });
$("next").addEventListener("click", () => { offset += limit; refresh(); });
$("rotate").addEventListener("click", rotate);
$("wd-toggle").addEventListener("click", toggleWatchdog);
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 5000);
