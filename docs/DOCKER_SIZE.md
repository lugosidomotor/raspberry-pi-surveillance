# Docker image és tárhely

Az alap `production` és a Pi `ncnn` target Torch nélküli, natív NCNN runtime.
A fejlesztői `test` target is ebből indul, és csak a teszteszközöket adja hozzá.
Az opcionális `runtime` target a nagyobb PyTorch implementáció.

## Mi kerül a kész image-be?

- Python, headless OpenCV, NumPy, NCNN, a web/API és notification függőségei;
- a 320-as YOLO11n export és az alkalmazás;
- célzottan fordított FFmpeg/ffprobe és rclone: a rögzítés és backup továbbra
  is ugyanabban az image-ben használható;
- szükséges futtatási könyvtárak, tanúsítványok és időzónák.

Nem kerül bele Torch, Ultralytics, PNNX, fordító, fejlécek, Mesa/LLVM, Qt,
GTK, X11 vagy a Debian teljes FFmpeg függőséglánca. Az OpenCV saját video-I/O
könyvtára továbbra is kell a Python RTSP-dekódoláshoz; a külön FFmpeg bináris
a stream publikálását és a lezárt klipfájlokat kezeli.

Az FFmpeg 8.0.3 forrásának hivatalos PGP-aláírását buildkor ellenőrizzük.
A célzott build H.264/H.265 RTSP-t, MP4/MKV/MPEG-TS stream-copy rögzítést,
szegmentálást és a projekt H.264/PNG tesztvideó-előkészítését támogatja.
Nem általános multimédiás munkaállomás: egzotikus kodek vagy új szűrő
használata előtt a `media-build` konfigurációját ki kell egészíteni.
FFmpeg/libx264 GPL licencei továbbra is érvényesek; image-disztribúciókor a
megfelelő forrást és licencinformációkat is biztosítani kell.

## Méretek értelmezése

`docker image ls` és `docker system df -v` tárolófüggően a tömörített és
kibontott rétegeket is számolhatja. Ez nem ugyanaz, mint egy registryből
letöltött archívum mérete. A korábbi ~300 MB-os adat nem a teljes helyi
Docker-tárhelyigény volt; ne hasonlítsd közvetlenül a helyi 1,15 GB-hoz.

A tagek gyakran ugyanazokat a rétegeket megosztják, ezért a sorokat nem lehet
összeadni. A `UNIQUE SIZE` megmutatja az egyedi részt, de régi közös alapok
csak az utolsó rájuk hivatkozó image/container/cache eltávolítása után szabadulnak fel.

## Biztonságos karbantartás

```bash
docker image ls
docker ps -a --format '{{.Names}} {{.Image}}'
docker system df -v
```

Csak ellenőrzött, nem használt `camera-stack` tageket törölj `docker image rm`
paranccsal, konkrét nevekkel. Futó vagy leállított konténer hivatkozását előbb
ellenőrizd. Ne használj `docker system prune -a --volumes` parancsot ezen a
hoston: más projektek és adatok is vannak rajta.

A build cache törlése később teljes újrafordítást okozhat. A Pi-n érdemes a
Macen készített ARM64 image-et betölteni, és nem a nehéz exportot ott futtatni.
Az alkalmazás `data/`, `.env` és kamerafelvételei nem image-rétegek, és a
régi image-ek eltávolítása nem törli ezeket.
