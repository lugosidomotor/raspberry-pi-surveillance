# Biztonság és publikálás

Az alkalmazás privát LAN/tailnet használatra készült. Nincs beépített
felhasználókezelés vagy hitelesítés. Az API eseményeket, kameraképeket és host
diagnosztikát mutat: ne publikáld közvetlenül az internetre. Két írási végpont van, a
képforgatás (`POST /api/settings/rotation`) és az automatikus újraindítás kapcsolója
(`POST /api/settings/watchdog`); csak JSON törzset fogadnak, így más weboldal a böngészőn
keresztül (CORS-preflight nélkül) nem tudja átállítani őket. A `camera-watchdog` rootként fut a
hoston, de csak újraindítani tud; a beállítást az appal közös `data/settings.json`-ból olvassa.

## Titkok és személyes adatok

- A `.env` helyi konfiguráció; Git és Docker build context kizárja.
- Csak az üres `.env.example` sablont commitold.
- A `data/` teljes tartalma privát: kameraképek, videók, SQLite, logok,
  rclone konfiguráció. Gitben csak `data/.gitkeep` szerepelhet.
- RTSP URL-t, ntfy topicot/tokent, SSH-kulcsot vagy távoli tárhelyadatot ne írj
  forráskódba, issue-ba, screenshotba vagy CI-logba.
- A natív FFmpeg hibakimenete és a processlista tartalmazhatja az RTSP URL-t.
  Log megosztása előtt ellenőrizd és takard ki az érzékeny adatokat.
- Egy nyilvános ntfy topic neve nem titkosított hozzáférésvédelem. Érzékeny
  kameraképhez használj hozzáférés-védett notification szolgáltatást.

## Hálózat és konténer

Alapértelmezett host port: `127.0.0.1:8000`. Privát távoli eléréshez a hoston
futó Tailscale Serve használható. `WEB_BIND_ADDRESS=0.0.0.0` a LAN-ra is kiteszi
a UI-t; a hozzáférést külön tűzfallal vagy tailnet szabállyal kell korlátozni.

A container nem root, nem privileged, eldobja a Linux capability-ket. A Pi
modellfájl opcionális mountja read-only. A Docker daemonhoz hozzáférő host
felhasználó ettől még képes elolvasni a konténer konfigurációját és adatait.

## Publikus GitHub push előtt

```bash
git status --short
git add --dry-run .
git ls-files
```

Ellenőrizd a tényleges diffet is. Az ignore nem szedi ki a korábban commitolt
titkokat a historyból. Ha titok valaha publikus commitba került, vond vissza
vagy cseréld le; a fájl törlése önmagában nem elég.

A projekt nem állít be helyetted távoli repositoryt, nem pushol és nem választ
automatikusan saját forráskódlicencet. A terjesztés előtt válassz megfelelő
licencet, és ellenőrizd az Ultralytics, a modellsúlyok és más függőségek
[hivatalos licencfeltételeit](https://www.ultralytics.com/license).
