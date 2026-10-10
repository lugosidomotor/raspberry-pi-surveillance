# Raspberry Pi hardware diagnostics

This feature extends the existing Camera Stack interface. `PRODUCT.md` and
`DESIGN.md` remain authoritative; the hardware panel inherits the existing
palette, system typography, tabular readings, and flat divider treatment.
It introduces no design tokens or replacement visual identity.

## Layout

On `/`, the panel sits below the live viewer and system rail, before recent
events. Its heading is “Raspberry Pi hardver”, followed by the detected model
without an explanatory sampling caption. Seven readings are available:
CPU temperature, CPU usage, CPU frequency, 1/5/15-minute load averages, memory
usage, free space on the data filesystem, and host uptime.

The compact panel uses one flat outlined enclosure. Four metric groups show
temperature, CPU, memory and free disk space, with numeric readings and thin
teal usage bars. Temperature uses a 0–100 °C visual range; the other bars show
CPU, memory and disk utilization percentages. Red identifies high readings;
numeric values and textual warnings remain available independently of color.
Frequency and core count sit beneath CPU. Load and abbreviated uptime share
a footer. The model occupies the heading's right side on desktop.

Above 760px the metrics use four columns; on mobile they use two. The model
moves beneath the title on mobile, and the footer wraps. No explanatory
paragraph or sampling legend is shown, as requested. The metrics retain the
incumbent palette and tabular numerals, with no nested cards or shadows.

## Components

The section starts hidden and appears only when `/api/status` reports
`hardware.is_raspberry_pi: true`. It stays hidden on `/events` and on hosts
without a detected Raspberry Pi model. The model is read from device-tree
files or the read-only `/host/pi-model` mount; architecture alone does not
enable the panel.

The visible-page refresh runs every five seconds. Backend snapshots are
cached for five seconds. CPU utilization is derived from two `/proc/stat`
samples, so the initial value says “Mérés…”. A missing CPU utilization result
retains that waiting message; other main readings show a dash, with “Nem
elérhető” in the secondary line where available. Missing bars have zero width.
Missing sensor files do not prevent the other readings from
being returned.

CPU, memory, load and uptime describe the host rather than container resource
limits. Disk readings describe the filesystem containing `DATA_DIR`: free
and total GiB plus the percentage occupied. This scope is documented in the
README rather than in an explanatory UI paragraph. Numbers use the Hungarian
locale; frequency uses MHz, temperature uses °C, and uptime uses abbreviated
days, hours and minutes, omitting zero days.

Warnings appear in a textual `role="status"` region above the readings.
Temperature at or above 75°C produces “Magas CPU-hőmérséklet”; disk free space
below 10% produces “Kevés szabad tárhely (10% alatt)”. Multiple warnings are
joined with a middle dot. With no warnings, the region is hidden. Request
failures use the existing application alert.

## Deployment

Docker needs access to the Pi model file for identification. The optional
`docker-compose.pi.yml` mounts
`/sys/firmware/devicetree/base/model` at `/host/pi-model` read-only. On the Pi,
`COMPOSE_FILE=docker-compose.yml:docker-compose.pi.yml` enables that override.
The mount requires neither privileged mode nor a separate host agent.

## Visual verification

Check the panel at desktop, intermediate and mobile viewport widths, including
missing sensors, initial CPU sampling, high temperature and low disk space.
Local review screenshots are not committed: they may contain private host
data or camera imagery.

Implementation sources: `app/hardware.py`, `app/web.py`,
`app/static/index.html`, `app/static/app.js`, `app/static/style.css`, and
`docker-compose.pi.yml`.
