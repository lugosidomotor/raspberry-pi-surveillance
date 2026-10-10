---
name: Camera Stack
description: Simple camera monitoring interface with a live viewer, system readings, and event evidence.
colors:
  bg: "#f3f5f6"
  paper: "#fff"
  ink: "#202f3c"
  muted: "#596773"
  line: "#dce2e6"
  accent: "#176b68"
  soft: "#e8f3f0"
  danger: "#a33435"
  viewer: "#17232b"
  viewer-bar: "#24343e"
typography:
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "16px"
    fontWeight: 400
  headline:
    fontSize: "28px"
    fontWeight: 650
    letterSpacing: "-.025em"
  title:
    fontSize: "16px"
    fontWeight: 650
  label:
    fontSize: "13px"
rounded:
  control: "6px"
  radius: "12px"
spacing:
  small: "12px"
  medium: "20px"
  large: "28px"
components:
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-hover:
    backgroundColor: "{colors.soft}"
  select:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  navigation:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.muted}"
    height: "76px"
  viewer:
    backgroundColor: "{colors.viewer}"
    rounded: "{rounded.radius}"
  state-rail:
    textColor: "{colors.ink}"
    width: "290px"
  event-row:
    textColor: "{colors.ink}"
    padding: "20px 0"
---

# Design System: Camera Stack

## Overview

A restrained utility interface: cool pale surfaces, slate text, teal interaction details, and a dark viewing area. The implementation is the visual authority; this document records the shipped plain HTML/CSS interface rather than proposing a new identity.

Source: `app/static/style.css`, `app/static/index.html`, and `app/static/app.js`.
Local review captures are not distributed with the repository because they
may contain private camera footage or host data.

Test sources use a flat amber status band above the page content, with the
explicit “TESZT STREAM” label and recorded-video context. It appears on both
routes; source identity comes from the API rather than inferred connection
state. Test events also retain a textual label in the event list.
The intentional test-status palette is foreground `#624609`, surface
`#fff3ce`, and outline `#cba24a`; it communicates simulation rather than
connection failure and keeps the incumbent typography and spacing.

## Colors

Primary: `accent` marks links, keyboard focus, active navigation, and confidence readings; `soft` provides button hover feedback. Neutrals: `bg` is the page canvas, `paper` the top bar and controls, `ink` primary text, `muted` secondary text, and `line` dividers. The viewer uses its own dark surface and bar. `danger` identifies an offline connection; connection text also names the state.

## Typography

Use the system sans stack with tabular numerals for readings. Page headings use `headline`; section and event titles use `title`. Supporting descriptions are smaller than the base text, while readings and actions use the compact label scale. Paragraph line height is (1.6). Mobile page headings reduce to (25px); event section headings remain (22px).

## Layout

The centered main container has a maximum width of (1500px), horizontal padding of (4%), and top padding of (38px). Desktop pairs a flexible viewer with a state rail and a gap from the large spacing step. Below it, events form a divided list rather than a card grid.

At (1000px) and below the rail narrows to (240px) and the workstation gap becomes (22px). At (760px) and below the workstation stacks, navigation moves to a second header line, headings and their actions stack, and page gutters become (5%). Event rows switch from three columns to two: thumbnail and text, with evidence actions below the text. The absent-stream surface has a mobile minimum height of (270px); a configured player retains a (16:9) aspect ratio.

## Elevation & Depth

No shadows. Separation comes from the pale canvas, white controls, fine dividers, and the dark viewer. Keep system readings directly on the page rather than adding nested containers.

## Shapes

Use the control radius for buttons, the select, and thumbnails. The larger radius contains the viewer and closes the event empty state. Status dots are circular; borders and row separators are thin and understated.

## Components

- Buttons: white, outlined controls, including the link styled as a button. Minimum height (42px); hover uses `soft`, active uses a slightly deeper pale teal. Loading disables refresh, filter, and pagination together; disabled controls have half opacity. Background transition is (0.18s ease-out), removed when reduced motion is requested.
- Object filter: a visibly labeled native select sharing button dimensions and shape. Retain the native interaction.
- Navigation: two text links in a white header; the active route gains ink text, a stronger weight, and a teal underline. Connection state sits at the opposite end and wraps naturally on mobile.
- Viewer: dark clipped surface with compact header and footer bars. The header carries a small outlined “↻ N°” rotate button; at 90°/270° the surface becomes a centered 9:16 portrait area. The actual player fills the image surface; otherwise a centered camera icon, explanation, and configuration hint appear.
- State rail: grouped definition lists with muted labels, right-aligned readings, and horizontal dividers. Long values wrap; mobile readings have more available width.
- Event row: a cropped evidence thumbnail, object label and confidence, timestamp and zone, then snapshot/video links or clip state. Empty results use one centered white message. Pagination remains a simple pair of buttons around the current page label.

All interactive elements use a visible teal outline (3px), offset (4px), for keyboard focus. Request errors appear above the content in a pale red alert; event updates use a polite live region.

## Do's and Don'ts

### Interface refinement

The existing light slate/teal identity remains unchanged. The navigation uses
small, matching line icons and `aria-current` for the selected page. The camera
brand mark sits on a flat teal-tinted surface. System readings are grouped in
compact sections with 13px headings and 12px readings, rather than nested cards.
Enabled integrations, online camera and healthy storage use teal text with a
small dot; errors use the existing danger color. Disabled functions retain
neutral text, and all states retain explicit labels.

At widths up to 900px, the readings flow below the viewer in two columns;
on narrow screens each label is above its reading. Event evidence links have
44px minimum touch targets and confidence uses a small pale-teal badge.
Event status lines (notification, video analysis, analysis notification, clip)
start with an emoji for at-a-glance scanning: ✅ done or sent, ❌ failed,
⏳ queued or in progress, ⏸️ cooldown, ➖ disabled; a completed analysis shows
🛑 for „Gyanús” and ✅ for „Nem gyanús”, matching the ntfy titles. The emoji is
`aria-hidden`; the text after it still names the state.
Hardware bars animate their transform, not their layout width. Reduced-motion
preferences disable transitions. Motion text inside the dark viewer uses
`#c3eedc` and a `#99dfbe` dot when active, both intentionally light for contrast.
The test-stream notice remains explicit and amber, in a compact 12px band.

- Do preserve clear labels, tabular readings, and compact evidence rows.
- Do retain the single-column mobile flow and visible keyboard focus.
- Don't add decorative shadows, gradients, or nested cards to this simple interface.
- Don't replace missing stream or empty event states with invented camera imagery or detection history.
