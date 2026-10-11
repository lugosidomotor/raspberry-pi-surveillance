# Raspberry Pi hardware diagnostics

The hardware card is part of the overview page; `DESIGN.md` defines its visual
language (glass card, ring gauges, coral for high readings).

## Layout

On `/`, the "Raspberry Pi hardver" card spans the full width below the latest
events, with the detected model on the right of its title. Four ring gauges show
CPU usage, memory usage, disk usage of the data filesystem and CPU temperature
(drawn against 85 °C). Each gauge has a detail line: cores and frequency (or
"lassítva (gyenge táp)" on undervoltage), used / total memory, free / total disk,
and "CPU, °C" or "Magas". The footer shows the 1/5/15-minute load averages and the
host uptime. CPU temperature and free disk space also appear as metric tiles in the
first row.

Above 1240px the gauges sit in one row of four; between 760px and 1240px in two
rows of two; on phones in a single column.

## Behaviour

The card starts hidden and appears only when `/api/status` reports
`hardware.is_raspberry_pi: true`; it never shows on `/events` or on hosts without a
detected Raspberry Pi model. The model is read from device-tree files or the
read-only `/host/pi-model` mount; architecture alone does not enable the card.

The page refreshes every five seconds while visible; backend snapshots are cached
for five seconds. CPU utilization is derived from two `/proc/stat` samples, so the
first reading is "Mérés…". Missing sensors show a dash or "Nem elérhető" without
hiding the other readings.

CPU, memory, load and uptime describe the host rather than container limits. Disk
readings describe the filesystem containing `DATA_DIR`. Numbers use the Hungarian
locale.

Warnings appear in a `role="status"` line above the gauges: "Feszültségesés: gyenge
táp, a CPU lassít", "Magas CPU-hőmérséklet" (75 °C and above) and "Kevés szabad
tárhely (10% alatt)", joined with a middle dot. Gauges over their limit turn coral.

## Deployment

Docker needs access to the Pi model file for identification. The optional
`docker-compose.pi.yml` mounts `/sys/firmware/devicetree/base/model` at
`/host/pi-model` read-only. On the Pi,
`COMPOSE_FILE=docker-compose.yml:docker-compose.pi.yml` enables that override.

## Visual verification

Check the card at desktop, tablet and phone widths, including missing sensors, the
initial CPU sampling, high temperature, undervoltage and low disk space (the demo's
control panel can switch on the last three). Local review screenshots are not
committed: they may contain private host data or camera imagery.

Implementation sources: `app/hardware.py`, `app/web.py`, `app/static/index.html`,
`app/static/app.js`, `app/static/style.css` and `docker-compose.pi.yml`.
