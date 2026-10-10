/* Demo backend for the standalone page: answers the UI's /api calls from an in-browser simulation.
   tools/build_demo.py inlines this ahead of the unchanged app/static/app.js. */
(() => {
"use strict";

// ---------------------------------------------------------------------------------------------------------------
// Scene. Self-contained (no outer references): the live player iframe runs the same code via toString().
// ---------------------------------------------------------------------------------------------------------------
function sceneKit() {
  const W = 1280, H = 960, FENCE = 352, GATE = [722, 848];
  // Feet position y → person height in pixels (simple ground-plane perspective).
  const tall = y => 80 + (y - 330) * (560 / 630);
  function random(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), a | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const pick = (r, list) => list[Math.floor(r() * list.length)];
  function look(actor) {
    const r = random(actor.seed);
    if (actor.kind === "car") return {body: pick(r, ["#9c2f2f", "#2f4f7a", "#d9d9d4", "#3a3d40", "#6f7a3a", "#b0b6ba"])};
    if (actor.kind === "cat") return {fur: pick(r, ["#3a332e", "#c47a3a", "#7d7d7d", "#e8e2d6"])};
    return {
      jacket: pick(r, ["#3d5a80", "#8a3b3b", "#2f5d50", "#5b4b8a", "#9a6b2f", "#3a3f44", "#c9b458"]),
      pants: pick(r, ["#2d3439", "#3f4a5a", "#4a3f35", "#5c6066"]),
      skin: pick(r, ["#e0b598", "#c68e6a", "#8d5a3f", "#f0c8a8"]),
      hair: pick(r, ["#2b2420", "#6b4a2e", "#b8a088", "#1d1d1d", "#8a8a85"]),
      parcel: actor.variant === "visitor" && r() < 0.35,
    };
  }
  // Keyframes [seconds, x, y]; a repeated point is a pause.
  function route(actor) {
    if (actor._route) return actor._route;
    const right = actor.dir < 0, y = 336 + (actor.lane || 0);
    let points;
    if (actor.variant === "visitor") {
      const [inX, inT, outX, outT] = right ? [1350, 6, -70, 8] : [-70, 8, 1350, 6];
      points = [[0, inX, y], [inT, 785, 338], [1.2, 785, 425], [4, 720, 860], [3, 722, 862], [4, 785, 425], [1.2, 785, 338], [outT, outX, y]];
    } else if (actor.kind === "person") {
      points = right ? [[0, 1350, y], [13, -70, y]] : [[0, -70, y], [13, 1350, y]];
    } else if (actor.kind === "car") {
      points = right ? [[0, 1650, 292], [5.5, -370, 292]] : [[0, -370, 292], [5.5, 1650, 292]];
    } else {
      const a = 600 + (actor.lane || 0) * 4, b = 660 + (actor.lane || 0) * 6;
      points = right ? [[0, 1340, a], [5, 620, (a + b) / 2], [1.5, 621, (a + b) / 2], [5, -80, b]]
        : [[0, -80, a], [5, 640, (a + b) / 2], [1.5, 641, (a + b) / 2], [5, 1340, b]];
    }
    let time = 0;
    actor._route = points.map(([dt, x, py]) => [(time += dt), x, py]);
    return actor._route;
  }
  const duration = actor => route(actor).at(-1)[0];
  // The moment the detector would confirm the object: fully in frame, reasonably large.
  function detectAt(actor) {
    const keys = route(actor);
    if (actor.variant === "visitor") return keys[2][0] + 0.8;
    return duration(actor) * (actor.kind === "car" ? 0.45 : 0.5);
  }
  function pose(actor, now) {
    const keys = route(actor), t = (now - actor.start) / 1000;
    if (t < 0 || t > keys.at(-1)[0]) return null;
    let i = 1;
    while (i < keys.length - 1 && keys[i][0] < t) i++;
    const [t0, x0, y0] = keys[i - 1], [t1, x1, y1] = keys[i], k = t1 > t0 ? (t - t0) / (t1 - t0) : 1;
    const x = x0 + (x1 - x0) * k, y = y0 + (y1 - y0) * k;
    let dx = x1 - x0, dy = y1 - y0, j = i;
    while (Math.abs(dx) + Math.abs(dy) < 4 && j > 1) { j--; dx = keys[j][1] - keys[j - 1][1]; dy = keys[j][2] - keys[j - 1][2]; }
    const walking = Math.abs(x1 - x0) + Math.abs(y1 - y0) >= 4;
    return {x, y, h: tall(y), dir: dx < 0 ? -1 : 1, frontal: Math.abs(dy) > Math.abs(dx), toward: dy > 0, walking, phase: t * Math.PI * 3.4};
  }
  function box(actor, p) {
    let b;
    if (actor.kind === "car") b = [p.x - 152, p.y - 104, p.x + 152, p.y + 6];
    else if (actor.kind === "cat") { const l = p.h * 0.3; b = [p.x - l * 0.62, p.y - l * 0.72, p.x + l * 0.62, p.y + 2]; }
    else b = [p.x - p.h * 0.17, p.y - p.h * 0.99, p.x + p.h * 0.17, p.y + 2];
    return [Math.max(0, b[0]), Math.max(0, b[1]), Math.min(W, b[2]), Math.min(H, b[3])].map(Math.round);
  }
  function visible(actor, now) {
    const p = pose(actor, now);
    if (!p) return false;
    const [x1, y1, x2, y2] = box(actor, p);
    return x2 - x1 > 20 && y2 - y1 > 20;
  }
  function isNight(ms) {
    const d = new Date(ms), hour = d.getHours() + d.getMinutes() / 60;
    return hour < 6.5 || hour >= 19;
  }

  // --- static layers ---
  function layer(width, height, paint) {
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.scale(width / W, height / H);
    paint(ctx);
    return canvas;
  }
  function rect(ctx, color, x, y, w, h) { ctx.fillStyle = color; ctx.fillRect(x, y, w, h); }
  function far(ctx) {
    let g = ctx.createLinearGradient(0, 0, 0, 190);
    g.addColorStop(0, "#c9d7e0"); g.addColorStop(1, "#e7ecee");
    rect(ctx, g, 0, 0, W, 190);
    ctx.fillStyle = "#7f9878";
    for (const [x, y, r] of [[60, 90, 70], [180, 70, 85], [560, 80, 75], [1010, 70, 90], [1180, 95, 70]]) { ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); }
    for (const [x, w, h, wall, roof] of [[120, 260, 95, "#c4b5a5", "#7d6a60"], [440, 220, 80, "#aeb8bc", "#5f676c"], [760, 300, 105, "#d4c8b4", "#8b5e4b"]]) {
      rect(ctx, wall, x, 190 - h, w, h);
      ctx.fillStyle = roof; ctx.beginPath(); ctx.moveTo(x - 14, 190 - h); ctx.lineTo(x + w / 2, 190 - h - 52); ctx.lineTo(x + w + 14, 190 - h); ctx.fill();
      for (let wx = x + 28; wx < x + w - 40; wx += 70) rect(ctx, "#6f8290", wx, 190 - h + 24, 34, 30);
    }
    rect(ctx, "#8fa486", 0, 182, W, 14);
    g = ctx.createLinearGradient(0, 196, 0, 300);
    g.addColorStop(0, "#5f666a"); g.addColorStop(1, "#727a7e");
    rect(ctx, g, 0, 196, W, 104);
    for (let x = 20; x < W; x += 150) rect(ctx, "#d8d6c8", x, 244, 70, 5);
    rect(ctx, "#b8bdbf", 0, 298, W, 7);
    rect(ctx, "#cbcdc8", 0, 305, W, 50);
    ctx.strokeStyle = "#b3b6b0"; ctx.lineWidth = 2;
    for (let x = 40; x < W; x += 96) { ctx.beginPath(); ctx.moveTo(x, 305); ctx.lineTo(x - 6, 355); ctx.stroke(); }
    rect(ctx, "#5b4d40", 0, 352, W, 70);
    g = ctx.createLinearGradient(0, 400, 0, H);
    g.addColorStop(0, "#82a065"); g.addColorStop(1, "#6a8a52");
    rect(ctx, g, 0, 400, W, H - 400);
    ctx.fillStyle = "rgba(255,255,255,.035)";
    for (let i = 0; i < 7; i++) { ctx.beginPath(); ctx.moveTo(i * 220 - 80, 400); ctx.lineTo(i * 220 + 30, 400); ctx.lineTo(i * 300 - 60, H); ctx.lineTo(i * 300 - 210, H); ctx.fill(); }
    ctx.fillStyle = "#b9b0a3";
    ctx.beginPath(); ctx.moveTo(GATE[0] + 10, 405); ctx.lineTo(GATE[1] - 6, 405); ctx.lineTo(1015, H); ctx.lineTo(455, H); ctx.fill();
    ctx.strokeStyle = "#9d9488"; ctx.lineWidth = 2;
    for (let i = 1; i < 12; i++) {
      const y = 405 + (H - 405) * (i / 12) ** 1.6, k = (y - 405) / (H - 405);
      const left = GATE[0] + 10 + (455 - GATE[0] - 10) * k, right = GATE[1] - 6 + (1015 - GATE[1] + 6) * k;
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    }
    rect(ctx, "#7c5d44", 30, 790, 280, 110); rect(ctx, "#694d38", 30, 880, 280, 20);
    for (let i = 0; i < 9; i++) {
      ctx.fillStyle = ["#5d8048", "#4f7040"][i % 2]; ctx.beginPath(); ctx.arc(55 + i * 30, 790, 26, 0, 7); ctx.fill();
      ctx.fillStyle = ["#d9667a", "#f0c24b", "#e8e4f2"][i % 3]; ctx.beginPath(); ctx.arc(55 + i * 30, 778 - (i % 2) * 8, 8, 0, 7); ctx.fill();
    }
    rect(ctx, "#3d4246", 1146, 70, 9, 236);
    rect(ctx, "#3d4246", 1124, 62, 50, 12);
    rect(ctx, "#f1ead2", 1132, 74, 34, 8);
  }
  function fence(ctx) {
    for (let x = 0; x < W; x += 24) {
      if (x + 14 > GATE[0] && x < GATE[1]) continue;
      ctx.fillStyle = "#ebe6dc";
      ctx.beginPath(); ctx.moveTo(x, 360); ctx.lineTo(x + 7, 348); ctx.lineTo(x + 14, 360); ctx.lineTo(x + 14, 420); ctx.lineTo(x, 420); ctx.fill();
    }
    rect(ctx, "#d9d3c7", 0, 368, GATE[0], 9); rect(ctx, "#d9d3c7", GATE[1], 368, W - GATE[1], 9);
    rect(ctx, "#d9d3c7", 0, 400, GATE[0], 9); rect(ctx, "#d9d3c7", GATE[1], 400, W - GATE[1], 9);
    rect(ctx, "#cfc8ba", GATE[0] - 10, 336, 18, 86); rect(ctx, "#cfc8ba", GATE[1] - 4, 336, 18, 86);
    rect(ctx, "#4b5a66", GATE[1] + 22, 352, 36, 26); rect(ctx, "#3a4650", GATE[1] + 36, 378, 8, 40);
    ctx.fillStyle = "#56783f";
    for (const [x, y, r] of [[1170, 455, 60], [1240, 430, 70], [1110, 480, 44], [1265, 500, 50]]) { ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); }
  }
  function layers(cache, width, height) {
    const key = width + "x" + height;
    if (cache.key !== key) Object.assign(cache, {key, far: layer(width, height, far), fence: layer(width, height, fence)});
    return cache;
  }

  // --- actors ---
  function line(ctx, x1, y1, x2, y2) { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); }
  function ellipse(ctx, color, x, y, rx, ry) { ctx.fillStyle = color; ctx.beginPath(); ctx.ellipse(x, y, rx, ry, 0, 0, 7); ctx.fill(); }
  function person(ctx, p, l) {
    const {x, y, h} = p, swing = p.walking ? Math.sin(p.phase) : 0, stride = p.frontal ? h * 0.035 : h * 0.12;
    const hip = y - h * 0.47, shoulder = y - h * 0.79, bob = p.walking ? Math.abs(Math.cos(p.phase)) * h * 0.012 : 0;
    ellipse(ctx, "rgba(0,0,0,.18)", x, y, h * 0.15, h * 0.03);
    ctx.lineCap = "round"; ctx.strokeStyle = l.pants; ctx.lineWidth = h * 0.08;
    const spread = p.frontal ? h * 0.045 : h * 0.02;
    line(ctx, x - spread, hip - bob, x - spread + swing * stride, y - h * 0.03 - (p.frontal && swing > 0 ? swing * h * 0.03 : 0));
    line(ctx, x + spread, hip - bob, x + spread - swing * stride, y - h * 0.03 - (p.frontal && swing < 0 ? -swing * h * 0.03 : 0));
    ctx.fillStyle = l.jacket;
    ctx.beginPath(); ctx.roundRect(x - h * 0.115, shoulder - h * 0.03 - bob, h * 0.23, hip - shoulder + h * 0.08, h * 0.05); ctx.fill();
    ctx.strokeStyle = l.jacket; ctx.lineWidth = h * 0.055;
    const arm = p.frontal ? h * 0.02 : stride * 0.8;
    if (l.parcel) {
      line(ctx, x - h * 0.1, shoulder - bob, x - h * 0.04 + p.dir * h * 0.08, hip - h * 0.06);
      line(ctx, x + h * 0.1, shoulder - bob, x + h * 0.04 + p.dir * h * 0.08, hip - h * 0.06);
      rect(ctx, "#b88a55", x - h * 0.1 + p.dir * h * 0.08, hip - h * 0.17 - bob, h * 0.2, h * 0.14);
      rect(ctx, "#d8c08f", x - h * 0.1 + p.dir * h * 0.08, hip - h * 0.11 - bob, h * 0.2, h * 0.025);
    } else {
      line(ctx, x - h * 0.11, shoulder - bob, x - h * 0.12 - swing * arm, hip + h * 0.02);
      line(ctx, x + h * 0.11, shoulder - bob, x + h * 0.12 + swing * arm, hip + h * 0.02);
    }
    const head = y - h * 0.9 - bob, r = h * 0.075;
    ctx.fillStyle = l.skin; ctx.beginPath(); ctx.arc(x, head, r, 0, 7); ctx.fill();
    ctx.fillStyle = l.hair; ctx.beginPath();
    if (p.frontal && !p.toward) ctx.arc(x, head, r * 1.02, 0, 7);  // seen from behind
    else if (p.frontal) ctx.arc(x, head - r * 0.15, r * 1.02, Math.PI * 1.05, Math.PI * 1.95);
    else if (p.dir > 0) ctx.arc(x, head, r * 1.04, Math.PI * 0.6, Math.PI * 1.95);
    else ctx.arc(x, head, r * 1.04, Math.PI * 1.05, Math.PI * 0.4);
    ctx.fill();
  }
  function car(ctx, p, l) {
    const {x, y, dir} = p, L = 300;
    ellipse(ctx, "rgba(0,0,0,.25)", x, y + 2, L * 0.52, 12);
    ctx.fillStyle = l.body;
    ctx.beginPath(); ctx.roundRect(x - L / 2, y - 64, L, 50, 16); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x - dir * L * 0.32, y - 62); ctx.lineTo(x - dir * L * 0.2, y - 102); ctx.lineTo(x + dir * L * 0.12, y - 102); ctx.lineTo(x + dir * L * 0.28, y - 62); ctx.fill();
    ctx.fillStyle = "#a9bcc6";
    ctx.beginPath(); ctx.moveTo(x - dir * L * 0.28, y - 64); ctx.lineTo(x - dir * L * 0.19, y - 96); ctx.lineTo(x - dir * L * 0.05, y - 96); ctx.lineTo(x - dir * L * 0.05, y - 64); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x - dir * L * 0.01, y - 64); ctx.lineTo(x - dir * L * 0.01, y - 96); ctx.lineTo(x + dir * L * 0.1, y - 96); ctx.lineTo(x + dir * L * 0.23, y - 64); ctx.fill();
    rect(ctx, "#f3e7b0", dir > 0 ? x + L / 2 - 14 : x - L / 2, y - 54, 14, 10);
    rect(ctx, "#b23a3a", dir > 0 ? x - L / 2 : x + L / 2 - 10, y - 54, 10, 10);
    for (const wx of [x - L * 0.3, x + L * 0.3]) { ellipse(ctx, "#1f2326", wx, y - 12, 22, 22); ellipse(ctx, "#8a9095", wx, y - 12, 9, 9); }
  }
  function cat(ctx, p, l) {
    const {x, y, dir} = p, s = p.h * 0.3, swing = p.walking ? Math.sin(p.phase * 1.3) : 0;
    ellipse(ctx, "rgba(0,0,0,.16)", x, y, s * 0.5, s * 0.06);
    ctx.strokeStyle = l.fur; ctx.lineCap = "round"; ctx.lineWidth = s * 0.07;
    for (const [ox, sign] of [[-0.3, 1], [-0.2, -1], [0.22, -1], [0.32, 1]]) line(ctx, x + dir * s * ox, y - s * 0.3, x + dir * s * ox + swing * sign * s * 0.07, y);
    ellipse(ctx, l.fur, x, y - s * 0.38, s * 0.42, s * 0.16);
    ctx.lineWidth = s * 0.06; ctx.beginPath(); ctx.moveTo(x - dir * s * 0.38, y - s * 0.42);
    ctx.quadraticCurveTo(x - dir * s * 0.66, y - s * 0.5, x - dir * s * 0.6, y - s * 0.78); ctx.stroke();
    const hx = x + dir * s * 0.44, hy = y - s * 0.56;
    ellipse(ctx, l.fur, hx, hy, s * 0.14, s * 0.13);
    ctx.fillStyle = l.fur;
    for (const ox of [-0.09, 0.07]) { ctx.beginPath(); ctx.moveTo(hx + s * ox - s * 0.05, hy - s * 0.08); ctx.lineTo(hx + s * ox, hy - s * 0.22); ctx.lineTo(hx + s * ox + s * 0.05, hy - s * 0.08); ctx.fill(); }
  }
  function drawActor(ctx, actor, p) {
    const l = actor._look || (actor._look = look(actor));
    if (actor.kind === "car") car(ctx, p, l); else if (actor.kind === "cat") cat(ctx, p, l); else person(ctx, p, l);
  }
  // NoIR at night: monochrome, darker, lit near the camera by the porch light.
  function night(ctx, width, height) {
    ctx.save();
    ctx.globalCompositeOperation = "saturation"; ctx.fillStyle = "#808080"; ctx.fillRect(0, 0, width, height);
    ctx.globalCompositeOperation = "multiply"; ctx.fillStyle = "#737c86"; ctx.fillRect(0, 0, width, height);
    const g = ctx.createRadialGradient(width * 0.55, height * 1.05, 0, width * 0.55, height * 1.05, height * 0.95);
    g.addColorStop(0, "rgba(180,190,196,.6)"); g.addColorStop(1, "rgba(170,180,186,0)");
    ctx.globalCompositeOperation = "screen"; ctx.fillStyle = g; ctx.fillRect(0, 0, width, height);
    const lamp = ctx.createRadialGradient(width * 0.9, height * 0.08, 0, width * 0.9, height * 0.08, height * 0.2);
    lamp.addColorStop(0, "rgba(255,248,225,.75)"); lamp.addColorStop(1, "rgba(255,248,225,0)");
    ctx.fillStyle = lamp; ctx.fillRect(0, 0, width, height);
    ctx.restore();
  }
  function render(ctx, width, height, now, actors, cache, dark) {
    const base = layers(cache, width, height), shown = [];
    for (const actor of actors) { const p = pose(actor, now); if (p) shown.push([actor, p]); }
    shown.sort((a, b) => a[1].y - b[1].y);
    ctx.drawImage(base.far, 0, 0);
    ctx.save(); ctx.scale(width / W, height / H);
    for (const [actor, p] of shown) if (p.y < FENCE) drawActor(ctx, actor, p);
    ctx.restore();
    ctx.drawImage(base.fence, 0, 0);
    ctx.save(); ctx.scale(width / W, height / H);
    for (const [actor, p] of shown) if (p.y >= FENCE) drawActor(ctx, actor, p);
    ctx.restore();
    if (dark) night(ctx, width, height);
  }
  function grain() {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 128;
    const ctx = canvas.getContext("2d"), image = ctx.createImageData(128, 128);
    for (let i = 0; i < image.data.length; i += 4) {
      const v = Math.random() * 255;
      image.data[i] = image.data[i + 1] = image.data[i + 2] = v; image.data[i + 3] = 255;
    }
    ctx.putImageData(image, 0, 0);
    return canvas;
  }
  return {W, H, look, duration, detectAt, pose, box, visible, isNight, render, grain};
}

// Runs inside the live player iframe (a data: URL document), driven by messages from this page.
function livePlayer(kit) {
  const canvas = document.querySelector("canvas"), ctx = canvas.getContext("2d"), offline = document.querySelector(".off");
  const noise = kit.grain(), cache = {};
  let actors = [], last = 0;
  addEventListener("message", event => {
    const data = event.data;
    if (!data || data.type !== "camera-demo-state") return;
    actors = data.actors;
    offline.hidden = !data.offline;
    canvas.style.visibility = data.offline ? "hidden" : "visible";
  });
  function resize() {
    const ratio = Math.min(devicePixelRatio || 1, 2), width = Math.min(innerWidth, innerHeight * 4 / 3);
    canvas.width = Math.max(320, Math.min(kit.W, Math.round(width * ratio)));
    canvas.height = Math.round(canvas.width * 0.75);
  }
  addEventListener("resize", resize);
  resize();
  function frame(stamp) {
    requestAnimationFrame(frame);
    if (stamp - last < 1000 / 12 || canvas.style.visibility === "hidden") return;  // the camera runs at 12 fps
    last = stamp;
    const now = Date.now(), width = canvas.width, height = canvas.height;
    kit.render(ctx, width, height, now, actors, cache, kit.isNight(now));
    ctx.save();
    ctx.globalAlpha = 0.07; ctx.fillStyle = ctx.createPattern(noise, "repeat");
    ctx.translate(Math.random() * -128, Math.random() * -128); ctx.fillRect(0, 0, width + 128, height + 128);
    ctx.restore();
    ctx.font = `600 ${Math.round(width / 70)}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    ctx.textAlign = "right"; ctx.fillStyle = "rgba(255,255,255,.62)";
    ctx.fillText("DEMÓ · szimulált kamerakép", width - width / 60, width / 40);
  }
  requestAnimationFrame(frame);
  parent.postMessage({type: "camera-demo-ready"}, "*");
}

const kit = sceneKit();
const LIVE_URL = "data:text/html;charset=utf-8," + encodeURIComponent(`<!doctype html><html lang="hu"><head><meta charset="utf-8"><title>Élőkép (demó)</title>
<style>html,body{margin:0;height:100%;background:#0b1013;overflow:hidden}canvas{display:block;width:100%;height:100%;object-fit:contain}
.off{position:fixed;inset:0;display:grid;place-content:center;justify-items:center;gap:8px;color:#b7c4cc;font:13px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;text-align:center}
.off[hidden]{display:none}.off b{color:#eaf0f3;font-size:15px;font-weight:600}.spin{width:26px;height:26px;border:3px solid #33454f;border-top-color:#9eb2be;border-radius:50%;animation:s 1s linear infinite}
@keyframes s{to{transform:rotate(1turn)}}@media(prefers-reduced-motion:reduce){.spin{animation:none}}</style></head>
<body><canvas></canvas><div class="off" hidden><div class="spin"></div><b>Nincs kép a kamerától</b><span>A lejátszó újracsatlakozik…</span></div>
<script>const kit = (${sceneKit})(); (${livePlayer})(kit);<\/script></body></html>`);

// ---------------------------------------------------------------------------------------------------------------
// Simulated camera stack: events, status, recovery watchdog, hardware.
// ---------------------------------------------------------------------------------------------------------------
const SECOND = 1000, MINUTE = 60 * SECOND, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
const loadedAt = Date.now();
const LABELS = {visitor: "person", passerby: "person", car: "car", cat: "cat"};
const ZONES = {visitor: "kapu", passerby: "utca", car: "utca", cat: "udvar"};
const SUMMARIES = {
  visitor: [
    "Nem gyanús — Egy személy a kapun át a bejárati ajtóig sétál, rövid ideig ott marad, majd távozik. Indok: Egyenletes tempó, nem nézelődik, nem próbálkozik a zárral.",
    "Nem gyanús — Egy személy belép a kapun, az ajtó előtt megáll, majd visszamegy az utcára. Indok: Valószínűleg csengetett; rövid, célirányos látogatás.",
    "Gyanús — Egy személy belép az udvarra, az ajtó előtt hosszan áll és körbenéz. Indok: Többször a ház felé fordul, nem csenget, a mozgása tétova.",
  ],
  parcel: ["Nem gyanús — Futár csomagot visz az ajtóhoz, majd távozik. Indok: Csomagot tart a kezében, rövid ideig marad, egyenesen távozik."],
  passerby: ["Nem gyanús — Járókelő halad el a ház előtt a járdán. Indok: Nem lép be a telekre, egyenletes tempóban halad tovább."],
};
let seedCounter = 1;
const sim = {
  records: [],
  actors: [],
  rotation: 0,
  autoReboot: true,
  flags: {test: false, undervoltage: false, hot: false, storage: false, nostream: false},
  fault: null,
  bootAt: loadedAt - 16_760 * SECOND,
  reboots: [],
  history: [],
  lastDetection: null,
  inference: 203.5,
  nextSpawn: loadedAt + 40 * SECOND,
  offline: false,
};
function defaults() {
  sim.reboots = [loadedAt - 5.3 * HOUR];
  sim.history = [
    {at: iso(loadedAt - 5.3 * HOUR), action: "reboot", reason: "A GPU-firmware nem válaszol (firmware transaction timeout). Újraindítás."},
    {at: iso(loadedAt - 2.1 * HOUR), action: "restart-source", reason: "Nincs kép 3 perce: a kameraforrás újraindítása.", result: "ok"},
  ];
}
function iso(ms) { return new Date(ms).toISOString().replace(/\.\d+Z$/, "+00:00"); }
function isoMillis(ms) { return new Date(ms).toISOString().replace("Z", "+00:00"); }
const pad = (value, size = 2) => String(value).padStart(size, "0");
function eventId(ms, label, confidence, r) {
  const d = new Date(ms);
  const stamp = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}_${pad(d.getUTCHours())}-${pad(d.getUTCMinutes())}-${pad(d.getUTCSeconds())}-${pad(d.getUTCMilliseconds(), 3)}${pad(Math.floor(r() * 1000), 3)}`;
  return `${stamp}_${label}_${confidence.toFixed(2)}_${Math.floor(r() * 2 ** 32).toString(16).padStart(8, "0")}`;
}
function random(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), a | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function makeActor(variant, start, r) {
  return {id: `a${seedCounter++}`, kind: LABELS[variant] === "person" ? "person" : variant, variant, start, dir: r() < 0.5 ? 1 : -1, lane: Math.round(r() * 10 - 5), seed: Math.floor(r() * 2 ** 31)};
}
function confidenceFor(variant, r) {
  const [low, high] = {visitor: [0.58, 0.93], passerby: [0.36, 0.84], car: [0.7, 0.95], cat: [0.45, 0.82]}[variant];
  return Math.round((low + (high - low) * r()) * 10000) / 10000;
}
function record(actor, t, r) {
  const label = LABELS[actor.variant], confidence = confidenceFor(actor.variant, r), alert = confidence >= 0.5;
  let summary = null;
  if (kit.look(actor).parcel) summary = SUMMARIES.parcel[0];
  else if (actor.variant === "visitor") summary = SUMMARIES.visitor[r() < 0.12 ? 2 : Math.floor(r() * 2)];
  else if (actor.variant === "passerby") summary = SUMMARIES.passerby[0];
  return {
    id: eventId(t, label, confidence, r), t, actor, label, confidence, alert, zone: ZONES[actor.variant],
    rotation: sim.rotation, isTest: sim.flags.test, summary,
    montageFrames: actor.variant === "visitor" ? 8 + Math.floor(r() * 6) : 4 + Math.floor(r() * 4),
  };
}
// A believable recent history: busier mornings and evenings, quiet nights.
function seedHistory(now) {
  const midnight = new Date(now); midnight.setHours(0, 0, 0, 0);
  const weights = [0.1, 0.05, 0.05, 0.05, 0.1, 0.3, 1.2, 2, 1.6, 0.8, 0.7, 0.8, 1.1, 1, 0.8, 0.9, 1.3, 1.8, 2, 1.5, 1, 0.7, 0.4, 0.2];
  for (let day = 3; day >= 0; day--) {
    const start = midnight.getTime() - day * DAY, r = random(Math.floor(start / DAY) * 7919);
    for (let hour = 0; hour < 24; hour++) {
      const count = Math.floor(weights[hour] * 1.5 + r());
      for (let i = 0; i < count; i++) {
        const t = start + hour * HOUR + Math.floor(r() * HOUR), roll = r();
        const variant = roll < 0.3 ? "visitor" : roll < 0.62 ? "passerby" : roll < 0.86 ? "car" : "cat";
        if (t > now - 2 * MINUTE) continue;
        const actor = makeActor(variant, 0, r);
        actor.start = t - kit.detectAt(actor) * SECOND;
        sim.records.push(record(actor, t, r));
      }
    }
  }
  sim.records.sort((a, b) => b.t - a.t);
}

function spawn(variant, delay = 0) {
  const r = random(Date.now() ^ (seedCounter * 2654435761));
  const actor = makeActor(variant, Date.now() + delay, r);
  sim.actors.push(actor);
  pushLive();
  return actor;
}
function cameraState(now) {
  const fault = sim.fault;
  if (!fault) return "connected";
  if (fault.type === "camera") return "reconnecting";
  if (fault.bootedAt) return now - fault.bootedAt < 7 * SECOND ? "connecting" : "connected";
  return "reconnecting";
}
const unreachable = now => Boolean(sim.fault?.rebootAt && now - sim.fault.rebootAt >= 3 * SECOND && !sim.fault.bootedAt);
const online = now => cameraState(now) === "connected" && !unreachable(now);

// Watchdog policy, as in deploy/pi/watchdog.py, on a compressed clock (demo seconds instead of minutes).
function rebootBlock(now) {
  const recent = sim.reboots.filter(t => now - t < DAY);
  if (!sim.autoReboot) return "Az automatikus újraindítás ki van kapcsolva.";
  if (recent.length && now - Math.max(...recent) < 30 * MINUTE) return "Az előző újraindítás túl friss (30 perces korlát).";
  if (recent.length >= 3) return `Már ${recent.length} újraindítás volt 24 órán belül.`;
  return null;
}
function advanceFault(now) {
  const fault = sim.fault;
  if (!fault) return;
  const t = (now - fault.at) / SECOND;
  if (fault.type === "camera") {
    if (t >= 10 && !fault.restarted) {
      fault.restarted = true;
      sim.history.push({at: iso(now), action: "restart-source", reason: "Nincs kép 3 perce: a kameraforrás újraindítása.", result: "ok"});
      refreshSoon();
    }
    if (t >= 18) { sim.fault = null; pushLive(); refreshSoon(); }
    return;
  }
  if (!fault.rebootAt) {
    const blocked = t >= 4 ? rebootBlock(now) : null;
    if (blocked !== (fault.blocked ?? null)) { fault.blocked = blocked; refreshSoon(); }
    if (t >= 4 && !fault.blocked) {
      fault.rebootAt = now;
      sim.reboots.push(now);
      sim.history.push({at: iso(now), action: "reboot", reason: "A GPU-firmware nem válaszol (firmware transaction timeout). Újraindítás."});
      refreshSoon();
    }
    return;
  }
  if (!fault.bootedAt && now - fault.rebootAt >= 23 * SECOND) {
    fault.bootedAt = now;
    sim.bootAt = now - 20 * SECOND;
    refreshSoon();
  }
  if (fault.bootedAt && now - fault.bootedAt >= 7 * SECOND) { sim.fault = null; pushLive(); refreshSoon(); }
}
function watchdog(now) {
  const fault = sim.fault, base = now - ((now - loadedAt) % (30 * SECOND));
  let state = "ok", message = "Minden rendben.", firmware = true, camera = online(now);
  if (fault?.type === "camera") {
    const t = (now - fault.at) / SECOND;
    [state, message] = fault.restarted ? ["recovering", "A kameraforrás újraindítva; várakozás a képre."]
      : ["warning", `Nincs kép ${Math.min(179, Math.round(t * 18))} mp óta; figyelés.`];
  } else if (fault && !fault.bootedAt) {
    firmware = false;
    const reason = "A GPU-firmware nem válaszol (firmware transaction timeout).";
    if (fault.rebootAt) [state, message] = ["rebooting", reason + " Újraindítás."];
    else if (fault.blocked) [state, message] = ["blocked", `${reason} ${fault.blocked}`];
    else [state, message, firmware, camera] = ["ok", "Minden rendben.", true, true];  // not checked yet
  } else if (fault?.bootedAt && !camera) {
    [state, message] = ["warning", "Nincs kép; indulás utáni türelmi idő."];
  }
  return {
    state, message, firmware_ok: firmware, camera_ok: camera,
    reboots_24h: sim.reboots.filter(t => now - t < DAY).length,
    installed: true, updated_at: iso(fault ? now : base), stale: false,
    last_action: sim.history.at(-1) || null, auto_reboot: sim.autoReboot,
  };
}
function wave(now, period, phase = 0) { return Math.sin(now / period + phase); }
function hardware(now, busy) {
  const total = 29_938_429_952, free = sim.flags.storage ? 2_310_000_000 : 5_143_371_776 - (now - loadedAt) * 9;
  const temperature = sim.flags.hot ? 78.4 + 0.8 * wave(now, 9000) : 65.2 + 1.6 * wave(now, 47000) + 0.6 * wave(now, 13000) + (busy ? 1.4 : 0);
  const cpu = 41 + 4 * wave(now, 23000) + 3 * wave(now, 7000, 1) + (busy ? 11 : 0);
  const used = 861_847_552 + 18_000_000 * wave(now, 61000);
  const warnings = [];
  if (sim.flags.undervoltage) warnings.push("Feszültségesés: gyenge táp, a CPU lassít");
  if (temperature >= 75) warnings.push("Magas CPU-hőmérséklet");
  if (free < total * 0.1) warnings.push("Kevés szabad tárhely (10% alatt)");
  const load = cpu / 100 * 4.6;
  return {
    is_raspberry_pi: true, model: "Raspberry Pi 4 Model B Rev 1.1", scope: "host", sampled_at: now / 1000,
    temperature_c: Math.round(temperature * 10) / 10, cpu_percent: Math.round(cpu * 10) / 10, cpu_cores: 4,
    cpu_frequency_mhz: sim.flags.undervoltage ? 600 : 1500, undervoltage: sim.flags.undervoltage,
    load_average: [load, load * 0.92 + 0.1, load * 0.95].map(v => Math.round(v * 100) / 100),
    memory: {total_bytes: 3_793_408_000, available_bytes: 3_793_408_000 - used, used_bytes: used, used_percent: Math.round(used / 3_793_408_000 * 1000) / 10},
    disk: {total_bytes: total, free_bytes: free, used_bytes: total - free, used_percent: Math.round((total - free) / total * 1000) / 10, path: "/data"},
    uptime_seconds: (now - sim.bootAt) / 1000, warnings,
  };
}

// Media paths look like the real ones; clicks on them open the in-page viewer instead.
function mediaBase(r) { return `events/${r.id.slice(0, 10)}/${r.id}`; }
function publicEvent(r, now) {
  const age = (now - r.t) / SECOND, person = r.label === "person", base = mediaBase(r);
  const gone = (r.actor.start + kit.duration(r.actor) * SECOND - r.t) / SECOND;
  let activity = "disabled", note = "disabled", summary = null;
  if (person) {
    activity = age < 10 ? "recording" : !r.alert ? "captured" : age < 24 ? "analyzing" : "completed";
    if (activity === "completed") summary = r.summary;
    note = r.alert ? (activity === "completed" && age >= 26 ? "sent" : "queued") : "disabled";
  }
  const clip = ["analyzing", "completed", "captured"].includes(activity), montage = person && age > gone + 15;
  const event = {
    id: r.id, timestamp: isoMillis(r.t), label: r.label, confidence: r.confidence, is_test: r.isTest,
    box: snapshotBox(r), zone: r.zone, action: "snapshot",
    snapshot_path: base + ".jpg", thumbnail_path: base + "_thumb.jpg", recording_path: null, recording_status: "disabled",
    alert: r.alert, notification_status: r.alert ? (age < 2 ? "queued" : "sent") : "silent",
    activity_status: activity, activity_notification_status: note, activity_summary: summary,
    activity_clip_path: clip ? base + "_activity.mp4" : null,
    montage_status: person ? (montage ? "completed" : "recording") : "disabled",
    montage_path: montage ? base + "_montage.jpg" : null,
    snapshot_url: "/media/" + base + ".jpg", thumbnail_url: thumbnail(r), recording_url: null,
    montage_url: montage ? "/media/" + base + "_montage.jpg" : null,
    activity_video_url: clip ? "/media/" + base + "_activity.mp4" : null,
  };
  if (summary) event.activity_verdict = summary.split(" — ")[0];
  if (clip) event.activity_clip_seconds = 10;
  if (montage) event.montage_frames = r.montageFrames;
  return event;
}

// --- images ---
const imageCache = new Map(), sceneCache = {};
function remember(key, make, limit = 160) {
  if (imageCache.has(key)) return imageCache.get(key);
  const value = make();
  imageCache.set(key, value);
  if (imageCache.size > limit) imageCache.delete(imageCache.keys().next().value);
  return value;
}
function rotateBox([x1, y1, x2, y2], rotation) {
  const {W, H} = kit;
  if (rotation === 90) return [H - y2, x1, H - y1, x2];
  if (rotation === 180) return [W - x2, H - y2, W - x1, H - y1];
  if (rotation === 270) return [y1, W - x2, y2, W - x1];
  return [x1, y1, x2, y2];
}
function snapshotBox(r) {
  const p = kit.pose(r.actor, r.t);
  return p ? rotateBox(kit.box(r.actor, p), r.rotation) : [0, 0, 0, 0];
}
// One frame as the backend saves it: turned by the rotation setting, then annotated (app/events.py annotate).
function frameCanvas(r, at, scale, annotate) {
  const {W, H} = kit, width = Math.round(W * scale), height = Math.round(H * scale);
  const scene = document.createElement("canvas");
  scene.width = width; scene.height = height;
  kit.render(scene.getContext("2d"), width, height, at, [r.actor], sceneCache[width] || (sceneCache[width] = {}), kit.isNight(at));
  const turned = r.rotation % 180 !== 0, out = document.createElement("canvas");
  out.width = turned ? height : width; out.height = turned ? width : height;
  const ctx = out.getContext("2d");
  ctx.save();
  if (r.rotation === 90) { ctx.translate(height, 0); ctx.rotate(Math.PI / 2); }
  else if (r.rotation === 180) { ctx.translate(width, height); ctx.rotate(Math.PI); }
  else if (r.rotation === 270) { ctx.translate(0, width); ctx.rotate(-Math.PI / 2); }
  ctx.drawImage(scene, 0, 0);
  ctx.restore();
  if (!annotate) return out;
  const [x1, y1, x2, y2] = snapshotBox(r).map(v => v * scale);
  ctx.strokeStyle = "rgb(70,220,50)"; ctx.lineWidth = Math.max(1, 2 * scale); ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
  ctx.font = `600 ${20 * scale}px "Helvetica Neue", Arial, sans-serif`;
  ctx.fillStyle = "rgb(70,220,50)";
  ctx.fillText(`${r.label} ${r.confidence.toFixed(2)} ${r.zone}`, x1, Math.max(24 * scale, y1 - 8 * scale));
  ctx.fillStyle = "#fff";
  ctx.fillText(iso(r.t), 10 * scale, out.height - 15 * scale);
  if (r.isTest) {
    ctx.fillStyle = "rgb(255,200,0)"; ctx.fillRect(0, 0, 260 * scale, 40 * scale);
    ctx.fillStyle = "rgb(40,30,20)"; ctx.fillText("TESZT STREAM", 10 * scale, 28 * scale);
  }
  return out;
}
const thumbnail = r => remember(r.id + ":thumb", () => frameCanvas(r, r.t, 0.25, true).toDataURL("image/jpeg", 0.75));
const snapshot = r => remember(r.id + ":full", () => frameCanvas(r, r.t, 1, true).toDataURL("image/jpeg", 0.85), 12);
function montage(r) {
  return remember(r.id + ":montage", () => {
    const start = r.actor.start + 0.6 * SECOND, end = r.actor.start + (kit.duration(r.actor) - 0.6) * SECOND, count = r.montageFrames;
    const times = Array.from({length: count}, (_, i) => start + (end - start) * (i / Math.max(1, count - 1)));
    const tiles = times.map(t => [t, frameCanvas(r, t, 0.375, false)]);
    const width = tiles[0][1].width, height = tiles[0][1].height, columns = Math.min(4, count), rows = Math.ceil(count / columns);
    const canvas = document.createElement("canvas"), ctx = canvas.getContext("2d");
    canvas.width = columns * width; canvas.height = 40 + rows * height;
    ctx.fillStyle = "rgb(24,24,24)"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    const time = (t, date) => { const d = new Date(t); return (date ? `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` : "") + `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
    ctx.font = '600 20px "Helvetica Neue", Arial, sans-serif'; ctx.fillStyle = "rgb(235,235,235)";
    ctx.fillText(`${r.label} | ${r.zone} | ${time(times[0], true)} - ${time(times.at(-1))} | ${count} frames${r.isTest ? " | TEST STREAM" : ""}`, 12, 27);
    tiles.forEach(([t, tile], index) => {
      const top = 40 + Math.floor(index / columns) * height, left = (index % columns) * width;
      ctx.drawImage(tile, left, top);
      ctx.fillStyle = "#000"; ctx.fillRect(left, top + height - 30, 112, 30);
      ctx.fillStyle = "#fff"; ctx.font = '600 17px "Helvetica Neue", Arial, sans-serif'; ctx.fillText(time(t), left + 8, top + height - 9);
    });
    return canvas.toDataURL("image/jpeg", 0.8);
  }, 6);
}

// --- API ---
function status(now) {
  const live = online(now), shown = live ? sim.actors.filter(a => kit.visible(a, now)) : [];
  const motion = shown.length > 0, seen = shown.find(a => a.record);
  if (seen) sim.lastDetection = {label: seen.record.label, confidence: Math.min(0.97, Math.max(0.3, seen.record.confidence + (Math.random() - 0.5) * 0.08)), timestamp: new Date(now).toISOString()};
  if (motion) sim.inference = Math.round((192 + Math.random() * 30) * 10) / 10;
  const camera = unreachable(now) ? "reconnecting" : cameraState(now), booting = camera === "connecting";
  const turned = sim.rotation % 180 !== 0;
  return {
    camera, motion, ai: booting ? "loading" : "ready", ai_backend: "ncnn", tracked_objects: ["person", "car", "cat"],
    retention_days: 14, min_free_disk_mb: 512, storage_low: sim.flags.storage, device: booting ? "pending" : "cpu",
    last_detection: sim.lastDetection, last_event: sim.records[0] ? publicEvent(sim.records[0], now) : null,
    ai_inference_ms: sim.inference, camera_fps: live ? 12.0 : 0, motion_fps: live ? 6.0 : 0,
    ai_fps: motion ? Math.round((2.2 + Math.random() * 0.5) * 10) / 10 : 0.0,
    processing_latency_ms: live ? Math.round((10 + Math.random() * 3) * 10) / 10 : 0,
    notifications_enabled: true, notification_status: "sent", activity_notification_status: "sent",
    backup_enabled: false, upload_status: "idle", upload_last_file: null, record_events: false,
    activity_analysis_enabled: true, continuous_recording: false, recording_status: "idle",
    pipeline: booting ? "starting" : "running", frame_size: turned ? [960, 1280] : [1280, 960],
    started_at: sim.bootAt / 1000 + 25, cpu: "aarch64 / Linux", hardware: hardware(now, motion),
    source: sim.flags.test ? {mode: "file", is_test: true, publisher: "publishing"} : {mode: "camera", is_test: false, publisher: "publishing"},
    live_stream_url: sim.flags.nostream ? null : LIVE_URL,
    rotation: sim.rotation, watchdog: watchdog(now),
  };
}
function events(params, now) {
  const limit = Number(params.get("limit") || 24), offset = Number(params.get("offset") || 0), label = params.get("label");
  if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) return [422, {detail: "Invalid paging"}];
  const rows = sim.records.filter(r => !label || r.label === label).slice(offset, offset + limit);
  return [200, {events: rows.map(r => publicEvent(r, now)), limit, offset}];
}
function timeline(params, now) {
  const start = Number(params.get("start")), end = Number(params.get("end"));
  if (!Number.isFinite(start) || !Number.isFinite(end) || !(end - start > 0 && end - start <= 26 * 3600)) return [422, {detail: "Timeline range must be finite and at most 26 hours"}];
  const buckets = new Map();
  for (const r of sim.records) {
    if (r.t < start * 1000 || r.t >= end * 1000) continue;
    const index = Math.floor((r.t / 1000 - start) / 900), bucket = buckets.get(index);
    if (bucket) bucket.count++; else buckets.set(index, {index, count: 1, event: publicEvent(r, now)});  // newest first
  }
  const list = [...buckets.values()].sort((a, b) => a.index - b.index);
  return [200, {start, end, bucket_seconds: 900, total: list.reduce((sum, b) => sum + b.count, 0), buckets: list}];
}
function respond(code, body) {
  return new Response(JSON.stringify(body), {status: code, headers: {"Content-Type": "application/json"}});
}
const realFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const url = new URL(typeof input === "string" ? input : input.url, "http://demo.invalid");
  if (!url.pathname.startsWith("/api/")) return realFetch(input, init);
  await new Promise(resolve => setTimeout(resolve, 60 + Math.random() * 90));
  if (init.signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
  const now = Date.now();
  if (unreachable(now)) throw new TypeError("Failed to fetch");  // the Pi is rebooting
  const method = (init.method || "GET").toUpperCase();
  let body = null;
  try { body = init.body ? JSON.parse(init.body) : null; } catch { return respond(422, {detail: "Invalid JSON"}); }
  if (method === "GET" && url.pathname === "/api/status") return respond(200, status(now));
  if (method === "GET" && url.pathname === "/api/events") return respond(...events(url.searchParams, now));
  if (method === "GET" && url.pathname === "/api/timeline") return respond(...timeline(url.searchParams, now));
  if (method === "POST" && url.pathname === "/api/settings/rotation") {
    if (![0, 90, 180, 270].includes(body?.rotation)) return respond(422, {detail: "rotation must be 0, 90, 180 or 270"});
    sim.rotation = body.rotation;
    return respond(200, {rotation: sim.rotation});
  }
  if (method === "POST" && url.pathname === "/api/settings/watchdog") {
    if (typeof body?.auto_reboot !== "boolean") return respond(422, {detail: "auto_reboot must be true or false"});
    sim.autoReboot = body.auto_reboot;
    updatePanel();
    return respond(200, {auto_reboot: sim.autoReboot});
  }
  return respond(404, {detail: "Not Found"});
};

// --- live player link and simulation clock ---
const players = new Set();
function pushLive() {
  const now = Date.now();
  const actors = sim.actors.filter(a => a.start + kit.duration(a) * SECOND > now - SECOND)
    .map(({id, kind, variant, start, dir, lane, seed}) => ({id, kind, variant, start, dir, lane, seed}));
  for (const player of players) {
    if (player.closed) { players.delete(player); continue; }
    player.postMessage({type: "camera-demo-state", actors, offline: !online(now)}, "*");
  }
}
window.addEventListener("message", event => {
  if (event.data?.type === "camera-demo-ready" && event.source) { players.add(event.source); pushLive(); }
});
let refreshTimer;
function refreshSoon() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => { if (typeof window.refresh === "function") window.refresh(); }, 120);
}
function tick() {
  const now = Date.now();
  advanceFault(now);
  if (now >= sim.nextSpawn) {
    const roll = Math.random();
    spawn(roll < 0.4 ? "visitor" : roll < 0.7 ? "passerby" : roll < 0.88 ? "car" : "cat");
    sim.nextSpawn = now + (45 + Math.random() * 45) * SECOND;
  }
  for (const actor of sim.actors) {
    const at = actor.start + kit.detectAt(actor) * SECOND;
    if (actor.checked || now < at) continue;
    actor.checked = true;
    if (!online(at)) continue;
    actor.record = record(actor, at, random(actor.seed ^ 0x5bd1e995));
    sim.records.unshift(actor.record);
    refreshSoon();
  }
  sim.actors = sim.actors.filter(a => a.start + kit.duration(a) * SECOND > now - 2 * MINUTE);
  const offline = !online(now);
  if (offline !== sim.offline) { sim.offline = offline; pushLive(); }
  updatePanel();
}

// ---------------------------------------------------------------------------------------------------------------
// Demo chrome: control panel and media viewer.
// ---------------------------------------------------------------------------------------------------------------
const panel = document.createElement("aside");
panel.className = "demo-panel";
panel.setAttribute("aria-label", "Demó vezérlő");
panel.innerHTML = `
  <div class="demo-body" id="demo-body" hidden>
    <div class="demo-head"><h2>Demó mód</h2><p>A valódi felület kódja, szimulált Raspberry Pi-vel. Nincs szerver: minden adat a böngészőben készül.</p></div>
    <section aria-labelledby="demo-spawn-heading"><h3 id="demo-spawn-heading">Valaki megjelenik a képen</h3>
      <div class="demo-grid"><button type="button" data-spawn="visitor">Látogató</button><button type="button" data-spawn="passerby">Járókelő</button><button type="button" data-spawn="car">Autó</button><button type="button" data-spawn="cat">Macska</button></div>
      <p class="demo-hint">Kb. 8 mp múlva esemény lesz belőle; közben magától is jön valaki.</p></section>
    <section aria-labelledby="demo-fault-heading"><h3 id="demo-fault-heading">Hibahelyzet</h3>
      <div class="demo-grid"><button type="button" data-fault="camera">Kamerakiesés</button><button type="button" data-fault="firmware">GPU-firmware lefagy</button></div>
      <p class="demo-status" id="demo-status" role="status"></p></section>
    <section aria-labelledby="demo-flag-heading"><h3 id="demo-flag-heading">Állapotok</h3>
      <label class="demo-switch"><span>Teszt stream</span><input type="checkbox" data-flag="test"></label>
      <label class="demo-switch"><span>Gyenge táp</span><input type="checkbox" data-flag="undervoltage"></label>
      <label class="demo-switch"><span>Magas hőmérséklet</span><input type="checkbox" data-flag="hot"></label>
      <label class="demo-switch"><span>Kevés tárhely</span><input type="checkbox" data-flag="storage"></label>
      <label class="demo-switch"><span>Nincs élőkép-cím</span><input type="checkbox" data-flag="nostream"></label></section>
    <div class="demo-foot"><button type="button" id="demo-reset">Alaphelyzet</button><span>A hibáknál az idő gyorsítva telik.</span></div>
  </div>
  <button type="button" class="demo-toggle" aria-expanded="false" aria-controls="demo-body"><span class="demo-dot" aria-hidden="true"></span>Demó<span class="demo-toggle-hint">vezérlő</span></button>`;
document.body.append(panel);
const $demo = selector => panel.querySelector(selector);
function setOpen(open) {
  $demo("#demo-body").hidden = !open;
  $demo(".demo-toggle").setAttribute("aria-expanded", String(open));
}
let stored = null;
try { stored = localStorage.getItem("camera-demo-panel"); } catch { /* storage unavailable */ }
setOpen(stored === "open");  // closed at first: the page itself comes first
$demo(".demo-toggle").addEventListener("click", () => {
  const open = $demo("#demo-body").hidden;
  setOpen(open);
  try { localStorage.setItem("camera-demo-panel", open ? "open" : "closed"); } catch { /* storage unavailable */ }
});
panel.addEventListener("keydown", event => { if (event.key === "Escape" && !$demo("#demo-body").hidden) { setOpen(false); $demo(".demo-toggle").focus(); } });
for (const button of panel.querySelectorAll("[data-spawn]")) button.addEventListener("click", () => spawn(button.dataset.spawn, 300));
for (const button of panel.querySelectorAll("[data-fault]")) {
  button.addEventListener("click", () => {
    if (sim.fault) return;
    sim.fault = {type: button.dataset.fault, at: Date.now()};
    pushLive(); refreshSoon(); updatePanel();
  });
}
for (const input of panel.querySelectorAll("[data-flag]")) {
  input.addEventListener("change", () => { sim.flags[input.dataset.flag] = input.checked; refreshSoon(); });
}
$demo("#demo-reset").addEventListener("click", () => {
  sim.fault = null; sim.autoReboot = true; sim.rotation = 0; defaults();
  for (const input of panel.querySelectorAll("[data-flag]")) { input.checked = false; sim.flags[input.dataset.flag] = false; }
  pushLive(); refreshSoon(); updatePanel();
});
function updatePanel() {
  const now = Date.now(), fault = sim.fault, line = $demo("#demo-status");
  let text = "Nincs hiba. Próbáld ki: a felügyelő (jobb oldalt lent) magától helyreállítja a rendszert.", tone = "muted";
  if (fault?.type === "camera") {
    const t = (now - fault.at) / SECOND;
    text = fault.restarted ? "A felügyelő újraindította a kameraforrást; a kép mindjárt visszajön."
      : `A kamera nem ad képet (${Math.min(179, Math.round(t * 18))} mp). 3 perc után a felügyelő újraindítja a kameraforrást (itt kb. 10 mp).`;
    tone = "danger";
  } else if (fault) {
    tone = "danger";
    if (fault.bootedAt) text = "A Pi újraindult; a kamera és az AI éppen elindul.";
    else if (fault.rebootAt) text = unreachable(now) ? "A Pi újraindul, az oldal addig nem éri el (kb. 20 mp)." : "A felügyelő újraindítja a Pi-t.";
    else if (fault.blocked) text = `A felügyelő nem indítja újra a Pi-t: ${fault.blocked} Kapcsold be újra az automatikus újraindítást, vagy nyomd meg az Alaphelyzetet.`;
    else text = "A GPU-firmware lefagyott, a kamera megállt. A felügyelő a következő ellenőrzésnél észreveszi.";
  }
  if (line.textContent !== text) line.textContent = text;
  line.dataset.tone = tone;
  for (const button of panel.querySelectorAll("[data-fault]")) button.disabled = Boolean(fault);
}

const viewer = document.createElement("dialog");
viewer.className = "demo-media";
viewer.setAttribute("aria-labelledby", "demo-media-title");
viewer.innerHTML = `<div class="demo-media-bar"><div><strong id="demo-media-title"></strong><span id="demo-media-meta"></span></div><button type="button" class="demo-media-close" aria-label="Bezárás">×</button></div>
  <div class="demo-media-body"></div><p class="demo-media-note">Demó: a böngészőben rajzolt kép, nem valódi felvétel.</p>`;
document.body.append(viewer);
let playing = 0;
viewer.querySelector(".demo-media-close").addEventListener("click", () => viewer.close());
viewer.addEventListener("click", event => { if (event.target === viewer) viewer.close(); });
viewer.addEventListener("close", () => { cancelAnimationFrame(playing); viewer.querySelector(".demo-media-body").replaceChildren(); });
function playClip(r, canvas) {
  const ctx = canvas.getContext("2d"), started = performance.now(), cache = {};
  let last = 0;
  const frame = stamp => {
    playing = requestAnimationFrame(frame);
    if (stamp - last < 1000 / 12) return;
    last = stamp;
    const offset = (stamp - started) % (10 * SECOND), at = r.t + offset;
    kit.render(ctx, canvas.width, canvas.height, at, [r.actor], cache, kit.isNight(at));
    ctx.fillStyle = "rgba(0,0,0,.55)"; ctx.fillRect(0, canvas.height - 6, canvas.width, 6);
    ctx.fillStyle = "#99dfbe"; ctx.fillRect(0, canvas.height - 6, canvas.width * offset / (10 * SECOND), 6);
    ctx.font = '600 18px "Helvetica Neue", Arial, sans-serif'; ctx.fillStyle = "#fff";
    ctx.fillText(`${(offset / 1000).toFixed(1)} / 10 mp`, 14, canvas.height - 18);
  };
  playing = requestAnimationFrame(frame);
}
function openMedia(r, kind) {
  const body = viewer.querySelector(".demo-media-body"), when = new Date(r.t).toLocaleString("hu-HU");
  const titles = {snapshot: "Snapshot", montage: "Montázs", clip: "Elemzett klip (10 mp)"};
  viewer.querySelector("#demo-media-title").textContent = `${titles[kind]} · ${r.label}`;
  viewer.querySelector("#demo-media-meta").textContent = `${r.zone} · ${Math.round(r.confidence * 100)}% · ${when}`;
  if (kind === "clip") {
    const canvas = document.createElement("canvas");
    canvas.width = 960; canvas.height = 720;
    canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", `Szimulált 10 másodperces klip: ${r.label}, ${when}`);
    body.replaceChildren(canvas);
    playClip(r, canvas);
  } else {
    const image = new Image();
    image.alt = `${titles[kind]}: ${r.label} a(z) ${r.zone} zónában, ${when}`;
    image.src = kind === "montage" ? montage(r) : snapshot(r);
    body.replaceChildren(image);
  }
  if (!viewer.open) viewer.showModal();
}
function mediaClick(event) {
  const link = event.target.closest?.("a[href^='/media/']");
  if (!link || event.type === "auxclick" && event.button !== 1) return;
  event.preventDefault();  // there is no server behind /media/: show the generated image in place
  const match = link.getAttribute("href").match(/\/media\/events\/[^/]+\/(.+?)(_montage\.jpg|_activity\.mp4|_thumb\.jpg|\.jpg)$/);
  const r = match && sim.records.find(item => item.id === match[1]);
  if (r) openMedia(r, match[2] === "_montage.jpg" ? "montage" : match[2] === "_activity.mp4" ? "clip" : "snapshot");
}
document.addEventListener("click", mediaClick, true);
document.addEventListener("auxclick", mediaClick, true);

defaults();
seedHistory(loadedAt);
spawn("visitor", 2500);
setInterval(tick, 250);
updatePanel();
})();
