---
name: Camera Stack
description: visionOS-style glass dashboard for a home camera: live view, day overview, events, recovery and hardware.
colors:
  ink: "#fff"
  muted: "rgba(255,255,255,.64)"
  faint: "rgba(255,255,255,.42)"
  glass: "rgba(255,255,255,.07)"
  edge: "rgba(255,255,255,.09)"
  lime: "#c9d236"
  lime-ink: "#1e2306"
  green: "#23824a"
  coral: "#ff8576"
  coral-bg: "#b93a2b"
  series: ["#e8c31c", "#2aa6e3", "#9aa3ab", "#b48cf0", "#ff8576"]
typography:
  body:
    fontFamily: '"Manrope", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "16px"
    fontVariantNumeric: tabular-nums
  card-title:
    fontSize: "21px"
    fontWeight: 700
  reading:
    fontSize: "23px"
    fontWeight: 700
  label:
    fontSize: "13px"
rounded:
  window: "40px"
  card: "26px"
  inner: "18px"
  pill: "999px"
spacing:
  grid-gap: "22px"
  card-padding: "22px 24px"
---

# Design System: Camera Stack

## Overview

A dark, visionOS-style glass dashboard, after the "LifeStats – Health Dashboard" shot
by Sunny Rathod: translucent, strongly rounded panels float over an out-of-focus room
(blue wall, warm floor, plants, a lamp), with a floating icon dock, capsule charts, a
donut, a day strip, status pills and progress rings. Everything is on one overview
page; `/events` is the full event log.

Source: `app/static/index.html`, `app/static/style.css`, `app/static/app.js`. The
standalone demo (`demo/camera-stack-demo.html`) is built from these same files.

## Surfaces

- **Environment:** a fixed layer of radial and linear gradients with a vignette. It
  exists to give the glass something to blur; it carries no information.
- **Window:** one large panel (`rounded.window`) with a light-to-dark translucent
  gradient, `backdrop-filter: blur(40px) saturate(150%)`, a hairline border and an
  inner top highlight. There is no page header: the camera state sits on the live
  view, so the first row of cards starts at the top of the window.
- **Cards:** `glass` fill, `edge` border, `rounded.card`. Section titles without a
  card (calendar, latest events) sit directly on the window.
- **Dock:** a floating vertical pill left of the window (icons only; the label
  appears on hover or focus). Under 760px it becomes a horizontal pill at the bottom
  left. Items: overview, event log, and shortcuts to the hardware and recovery cards
  (from the event log they open the overview at that card).
- `prefers-reduced-transparency` switches the window, dock and cards to opaque
  slate fills; blur is also the most expensive effect on weak devices.

## Colour and state

White text on glass, `muted` for labels and `faint` for hints. Meaning is carried by
pills and dots, always together with text:

| Kind | Colour | Used for |
|---|---|---|
| go | lime pill, dark text | in progress: clip recording, analysis, queued, watchdog watching or recovering |
| done | green pill, white text | finished well: not suspicious, notified, watchdog OK |
| warn | coral pill, white text | needs attention: suspicious, failed, watchdog blocked or silent |
| muted | translucent pill | silent (low-confidence) events, disabled functions |

Coral text and dots mark offline camera, hung firmware, high temperature, low disk.
The donut and legend use the `series` colours in the order of `AI_TRACK_OBJECTS`.

## Layout

A 3-column grid (`1.55fr .8fr 1.2fr`, gap 22px) inside a 1320px window:

1. **Live view** (4:3 or the stream's real ratio; rotation turns the player, 90/270
   make it portrait), **metric tiles** (camera/motion FPS, AI time, CPU temperature,
   free disk), **overview** donut of the selected day by label with the change
   against the previous day.
2. **Activity**: the selected day in eight 3-hour capsules, the current one lime;
   **calendar** week strip (Monday first, future days disabled) that selects the
   day for the donut and the capsules, and the **last hit** card.
3. **Latest events** (6 rows: ring or check, thumbnail, label · zone, time and
   confidence, analysis summary, status pill, montage/clip/video links or their
   "készül…" state), beside the **recovery** card (state pill, message, reboots in
   24 h, firmware, camera, last check and action, auto-reboot switch) and the
   **integrations** card (notifications, cloud backup, upload, event clip, 24/7
   recording, video analysis, backup state; retention in the header).
4. **Raspberry Pi hardware**: four ring gauges (CPU, memory, disk, temperature
   against 85 °C) with details, load and uptime in the footer. Only on a detected Pi.

Below 1240px the grid has two columns; below 980px one. Under 760px the window
padding shrinks, tiles go 2×2, event rows put the pill and links under the text.

## Typography

Manrope (400–800), self-hosted in `app/static/fonts` under the SIL Open Font License,
so the UI makes no third-party requests and looks the same offline; the system UI
font is the fallback. Tabular numerals everywhere. Card titles 21px/700, readings
23px/700 with a 14px muted unit, labels 13px.

## Behaviour

- Refresh every 5 s while the page is visible. Each region re-renders only when its
  data changed, so focus, hover and images survive refreshes.
- Errors appear as a coral banner at the top of the window; the test source shows a
  lime "TESZT STREAM" banner, and test events keep a "TESZT" label.
- A missing thumbnail (archived, or lost in a crash) shows a "Nincs kép" placeholder.
- Keyboard focus: 2px white outline, offset 3px. Touch targets are at least 44px
  except the compact link chips (30px) inside event rows.
- `prefers-reduced-motion` removes transitions and the progress-ring spin.

## Do and don't

- Do keep every state readable as text; colour only reinforces it.
- Do keep the camera state on the live view instead of reintroducing a page header.
- Don't invent data: empty days show zero capsules and an empty donut, a missing
  stream shows the configuration hint, not an image.
- Don't load fonts, icons or scripts from third parties; the UI is host-local.
