# Egy Compose-stack: kameraforrás, MediaMTX és AI app

A Docker-szolgáltatások egyetlen `camera-stack` Compose-projektben futnak.
A `docker-compose.stream.yml` opcionális override adja hozzá a MediaMTX-et
és a publishert. Külső RTSP feedhez nem kell ez az override.
Az összefüggéseket az [architektúra-diagram](ARCHITECTURE.md) mutatja.

## Konfiguráció és indítás — Pi, tesztvideó

`.env` (az override-ok sorrendje számít; az NCNN legyen az utolsó):

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.pi.yml:docker-compose.stream.yml:docker-compose.ncnn.yml
COMPOSE_PROFILES=test-source
LIVE_STREAM_URL=http://<PI-IP>:8888/cam
WEBRTC_ADDITIONAL_HOSTS=<PI-IP>
APP_UID=1001
APP_GID=1001
```

Az UID/GID értékeket az `id -u` és `id -g` alapján állítsd be. Az app
`CAMERA_URL`-jét a stream override belső `rtsp://mediamtx:8554/cam` címre állítja.
A felvétel ugyanazt olvassa, kivéve ha külön `RECORDING_CAMERA_URL`-t adsz meg.
A böngészős `LIVE_STREAM_URL` mindig a kliens által elérhető hostcím legyen,
nem a Docker belső `mediamtx` neve.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f
docker compose restart publisher         # csak a tesztstream, újra az elejéről
docker compose restart mediamtx          # csak a videóelosztó
docker compose restart camera            # csak az elemző app
docker compose down                     # a teljes Docker-stack
```

A publisher ugyanazt az NCNN image-et használja, mint az app; nincs új nagy image.
FFmpeg `-re -stream_loop -1 -c:v copy`: valós idejű, végtelen lejátszás, újrakódolás
nélkül. Az app felületén, snapshotokon és értesítéseken a forrás **TESZT STREAM**.
Tesztvideó hiányában a publisher hibával újraindul; az app reconnectel, a web UI működik.
A restart-policy boot után is elindítja a három konténert.

Mac/Linux teszthez a Pi-hardvermountot hagyd el:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.stream.yml:docker-compose.ncnn.yml
COMPOSE_PROFILES=test-source
LIVE_STREAM_URL=http://127.0.0.1:8888/cam
```

## Tesztvideó előkészítése

A saját videó a Gitből kizárt `data/imports/` alá kerüljön. Az utolsó képkockát
3600 másodpercig kitartva az eredeti rész után fűzzük. A bemenet érintetlen marad.

```bash
docker compose build camera
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v "$PWD/tools:/tools:ro" --entrypoint python camera \
  /tools/prepare_test_video.py /data/imports/input.mp4 /data/test/source.mp4
```

Meglévő kimenetet nem ír felül. H.264 baseline, hang nélkül; az állóképes részt
rövid darabokból stream-copy-val építi, nem kódol végig egy órát. A teljes
mozgó rész + egyórás állókép ciklus ismétlődik. A tesztvideó/felvételek nem kerülnek Gitbe.

## Host-beállítás egy lépésben

Amit a Docker Compose nem tud (video csoport, linger, `camera-source` user szolgáltatás, hardveres
watchdog, `camera-watchdog` szolgáltatás, a `dhcpcd` távoltartása a Docker interfészeitől), azt a
`deploy/pi/install-host.sh` állítja be. A repó tulajdonosaként futtasd (nem rootként; ahol kell, `sudo`-t
használ). Többször is futtatható: csak az eltérést javítja, a módosított rendszerfájlról `.bak-<idő>`
mentést készít, és a végén összesít.

```bash
deploy/pi/install-host.sh --check   # csak ellenőriz; kilépési kód 0, ha minden rendben
deploy/pi/install-host.sh           # beállítja, ami hiányzik vagy eltér
deploy/pi/install-host.sh --skip-camera-source   # tesztvideó vagy külső RTSP-kamera esetén
```

A telepítő a CPU-órajelet is rögzíti (`cpu-performance.service`, `performance` governor, kihagyható:
`--skip-cpu-governor`). Az `ondemand` governor percenként többször kér órajelváltást a VideoCore-firmware-től;
a 2026-10-10-i firmware-lefagyás egy ilyen kérés közben történt, és ugyanaznap a kamera-pipeline többször
beragadt. A fix órajel ezeket a firmware-tranzakciókat szünteti meg.

A Docker stacket külön indítsd (`docker compose up -d`). Az alábbi kézi lépések referenciaként maradtak.

## Valódi CSI-kamera

Az egyetlen hostoldali kivétel a `rpicam-vid`/`libcamera-vid`: ez kapja meg a CSI
kamerát. Az app és a MediaMTX nem kap privileged jogosultságot; az app legfeljebb az
opcionális H.264-dekóder eszközét kapja meg (lásd lent).
Python 3.9+ kell a hoston. CSI szerelés előtt kapcsold ki a Pi-t.

A szalagkábel a **CAMERA** feliratú csatlakozóba kerüljön, ne a DISPLAY-be. A host
adaptert futtató felhasználónak a `video` csoport tagjának kell lennie; enélkül a
`libcamera` „No cameras available!” üzenetet ad, pedig a kernel látja a szenzort
(`sudo libcamera-vid --list-cameras` ilyenkor listázza). Csoport hozzáadása után a
felhasználói systemd-kezelőnek is új csoporttal kell indulnia (újraindítás vagy
`sudo systemctl restart user@$(id -u)`):

```bash
sudo usermod -aG video "$USER"
libcamera-vid --list-cameras   # új bejelentkezés után: 0 : imx219 ...
```

Állítsd a `.env`-ben a `COMPOSE_PROFILES=` értéket üresre, majd:

```bash
docker compose --profile test-source stop publisher
docker compose --profile test-source rm -f publisher
docker compose up -d mediamtx camera
python3 deploy/pi/source.py
```

A host adapter a capture H.264 stdoutját egy **ugyanahhoz a Compose-projekthez
tartozó** `publisher` one-off konténer FFmpeg stdin-jébe vezeti (`compose run
--interactive -T`). Nincs külön `docker run`, külön image vagy külön Docker-projekt.
A konténer neve `camera-stack-publisher-csi`; a Compose `ps -a` alatt látszik,
a logja `docker logs camera-stack-publisher-csi`.
A one-off konténert **nem** állítja le a sima `compose down`; a host adapter
leállítása végzi el a `docker stop`-ot, majd a `--rm` miatt automatikusan eltűnik.
A kép 1280×720, 12 FPS, 2 Mbit/s; `-c:v copy`. A szenzormódot a `libcamera` választja: IMX219-en ez egy
középső 1920×1080-as kivágás (kb. 39°×22°). A teljes szenzor (`CAMERA_SIZE=1280x960`,
`CAMERA_SENSOR_MODE=1640:1232:10:P`, kb. 62°×49°) 2026-10-10-én percek alatt kétszer is beragasztotta
a kamera-pipeline-t (firmware 1.20230405, libcamera 0.0.5), ezért csak firmware-/OS-frissítés után
érdemes kipróbálni. `CAMERA_TUNING_FILE`: NoIR modulhoz `imx219_noir`.

`CAMERA_ENCODER=software`: a `libcamera-vid` nyers képet ad (`--codec yuv420`, az ISP továbbra is a GPU-n),
a H.264-et a hoston egy FFmpeg x264 készíti (`ultrafast`, `zerolatency`: B-képkocka és előretekintés nélkül,
másodpercenként kulcskocka). Költsége 1280×960/12 FPS-en kb. 0,65 CPU-mag. 2026-10-10-én minden kamera-
lefagyás a GPU H.264-kódolójában (`bcm2835-codec`) indult; szoftveres kódolással a teljes szenzoros mód
(ami hardveres kódolóval 1–5 percen belül lefagyott) 68 percig hibátlanul futott, 0 GPU-hibával.
OS-/firmware-frissítés után a hardveres kódoló (`CAMERA_ENCODER=hardware`) újra kipróbálható. A detektálás legfeljebb 8 FPS-sel mintavételez,
így a 12 FPS elég, és az app dekódolási terhelése a 24 FPS-hez képest feleződik. Az encoder alapértéke (kb. 9–10 Mbit/s)
Pi 4-en a MediaMTX, az app és egy Tailscale-néző mellett telíti a CPU-t.

Host adapter automatikus indítása (projekt: `~/camera-stack`):

```bash
mkdir -p ~/.config/systemd/user
cp deploy/pi/camera-source.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now camera-source
# Boot utáni futáshoz, ha még nincs engedélyezve:
sudo loginctl enable-linger "$USER"
```

Valódi stream restart: `systemctl --user restart camera-source`.
Teljes leállításkor **előbb** `systemctl --user stop camera-source`, aztán
`docker compose down`; különben a host adapter újraindítaná a one-off publishert.
Teljes indításkor előbb `docker compose up -d`, aztán a host adapter indítása.
A host adapter kamera nélkül újrapróbál, nem kapcsol automatikusan tesztvideóra:
ez szándékos, hogy valós kamera hibáját ne fedje el a tesztkép.

## Hardveres H.264-dekódolás (Pi 4)

Az app alapból szoftveresen dekódolja a kamerastreamet. Pi 4-en a `docker-compose.hwdecode.yml`
override a `bcm2835-codec` dekóder (`/dev/video10`) eszközét adja a konténernek a host `video`
csoportjával (GID 44), és `CAMERA_DECODER=h264_v4l2m2m`-et állít be. Mérve 720p/12 FPS-en a
dekódolás CPU-ideje kb. 30%-kal csökken; a maradékot az RTSP-fogadás és a színtér-konverzió adja.
Ha a dekóder nem nyitható meg, az app szoftveres dekódolásra vált és ezt naplózza; a log
`stream connected (… decoder …)` sora mutatja a használtat. Pi 5-ön nincs H.264-dekóder,
ott ezt az override-ot ne add hozzá.

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.pi.yml:docker-compose.hwdecode.yml:docker-compose.stream.yml:docker-compose.ncnn.yml
```

## Automatikus helyreállítás (watchdog)

A kameraforrás egy olvasószálon át mindig üríti a `libcamera-vid` kimenetét: ha a publisher lemarad vagy
elakad, adatot dob el és csak a publishert indítja újra, így a hardveres kódoló sosem marad ürítetlen
pufferekkel (ez a kernel kamera-driverét is beragaszthatja). 10 s kameraadat nélkül a forrás újraindul.
5 percenként állapotsort ír a naplóba (`pipeline ok: … MiB, last data … s ago, … dropped`), a publisher
kilépésekor pedig a kilépési kódot is.

Egy lefagyott VideoCore-firmware (`Firmware transaction timeout` a kernel logban) a kamerát, az ISP-t
és a kodekeket is magával viszi; csak újraindítás oldja fel. Két réteg védi a rendszert:

1. **Hardveres watchdog** (`deploy/pi/hardware-watchdog.conf`): a systemd eteti a Pi watchdogját; ha a
   rendszer lefagy, vagy egy újraindítás 2 percnél tovább akad el, a hardver indítja újra (nem kell
   áramtalanítani).
2. **`camera-watchdog` szolgáltatás** (`deploy/pi/watchdog.py`, root): 30 s-onként figyeli a kernel logot
   és az app kamerájának állapotát. Firmware-lefagyásnál újraindítja a Pi-t; ha 3 percig nincs kép, előbb
   a `camera-source`-t indítja újra, ha az sem segít (vagy maga az újraindítás akad el, mert a kamera a
   kernelben ragadt: ekkor azonnal), a Pi-t. A helyreállító újraindítás `systemctl reboot --force`: a
   beragadt folyamatokra nem vár perceket. Bootkor 5 perc türelmi idő (a firmware-
   lefagyásra nem vonatkozik); újraindítás legfeljebb 30 percenként és 24 óra alatt 3-szor. Minden
   beavatkozás előtt diagnosztikai pillanatképet ment (`data/diagnostics/`: kernel-log, folyamatállapotok,
   órajel-statisztika, a kameraforrás naplója; az utolsó 30 marad). Az állapotot a
   `data/watchdog-status.json`-ba írja; a UI „Automatikus helyreállítás” widgetje mutatja, és onnan az
   automatikus újraindítás ki-be kapcsolható (`data/settings.json`).

Telepítés: `deploy/pi/install-host.sh` (fent). Kézzel ugyanez:

```bash
sudo install -m 644 deploy/pi/hardware-watchdog.conf /etc/systemd/system.conf.d/camera-hardware-watchdog.conf
sudo systemctl daemon-reexec
sudo python3 deploy/pi/watchdog.py --dry-run   # egy kör próba, majd Ctrl+C
sed "s#@ROOT@#$PWD#" deploy/pi/camera-watchdog.service | sudo tee /etc/systemd/system/camera-watchdog.service
sudo systemctl daemon-reload && sudo systemctl enable --now camera-watchdog
```

## Végpontok és hálózat

- RTSP: `rtsp://<PI-IP>:8554/cam` (Synology is ezt használja, RTSP/TCP).
- HLS player: `http://<PI-IP>:8888/cam`.
- WebRTC player: `http://<PI-IP>:8889/cam` (+ UDP 8189; ICE-beállítás kellhet).
- App: `http://<PI-IP>:8000` (`WEB_BIND_ADDRESS` szerint).

A Docker belső bridge hálózaton a MediaMTX neve `mediamtx`. A 8554/8888/8889/8189
portok a hostra vannak publikálva; a korábbi LAN/tailnet hozzáférés megmarad.
WebRTC-hez a `WEBRTC_ADDITIONAL_HOSTS` a böngészőből elérhető Pi IP/DNS legyen;
ezzel a MediaMTX nem csak a belső bridge címet hirdeti ICE-jelöltként.
Ez protokollátjárás, nem több felbontás vagy újrakódolás. Nincs beépített auth:
ne publikáld ezeket a portokat az internetre. Tailscale a hoston marad.

## Átállás a régi, külön stackről

Előbb állítsd le a régi host publishert, különben port/forrásütközés lesz.

```bash
systemctl --user disable --now camera-source
docker stop camera-source-mediamtx-1
docker rm camera-source-mediamtx-1
```

Ez csak a régi, konkrét MediaMTX-konténert távolítja el, nem a felvételeket.
Utána állítsd be a fenti `COMPOSE_FILE`/`COMPOSE_PROFILES` értékeket és indítsd
a közös stacket. A `deploy/pi/compose.yml` és `compose.local-stream.yml` megszűnt.
Valódi kameramódban az új unitot másold be, és csak akkor engedélyezd újra.
