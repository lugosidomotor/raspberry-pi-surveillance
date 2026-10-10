# Tesztelés

A tesztekhez nincs szükség kamerára, notification fiókra vagy backup tárhelyre.
Az integrációk hálózati hívásai mockoltak; a tesztimage internet nélkül futtatható.

## Dockeres unit tesztek

```bash
docker build --target test -t camera-stack:test .
docker run --rm --network none camera-stack:test
```

A `test` target a production függőségeivel épül, de a pytest, httpx és a tesztek
nem kerülnek a production image-be. A GitHub Actions ugyanezt a parancspárt
futtatja és ellenőrzi a nem root production runtime-ot.

Lefedés: kötelező kamera URL; hiányzó/részleges opcionális env; config
precedencia; person-only default; hibás konfiguráció; temporal validation;
zónák; mozgás és crop; snapshot/SQLite; notification; lezárt videók uploadja;
continuous segment closure; read-only web/API/media; offline health;
Pi hardverdiagnosztika és hiányzó szenzorok.
Tesztforrás: env és atomikus metadata jelzés, kamera nélküli probe,
tesztesemény/notification címkézés és a bemeneti videó felülírásának tiltása.
Backendek: a helyi/public reconnect cap, NCNN model-contract, CPU-thread és
precision opciók, confidence/class szűrés, NMS, letterbox koordináták és
inference hibakezelés. Az NCNN image-en külön valódi inference smoke-test fut.
Megbízhatóság: DST/UTC és régi SQLite migráció; retention és aktív upload-védelem;
kevés tárhely/helyreállás; thumbnail/cache; hiányzó upload terminális státusza;
CSI Docker stdin-parancs; pipeline retry és stale frame tiltás; inkrementális
manifest és throttling; valódi FFmpeg pre-roll összefűzés; rclone log redakció.
Videóelemzés: reasoning modellekhez elég tokenkeret, toleráns JSON-feldolgozás
(code fence, think tag), HTTP-hiba részletes logja és modell-fallback, a klip
felvétele nem vár a lassú LLM-elemzésre.
Mozgásmontázs: epizód a mozgás végéig, korlátos és egyenletes képgyűjtés, csatlakozó
események, maximális hossz, rácsméret, ntfy-csatolmány kódolt fejlécekkel, env-validáció,
web-link és retention.
Riasztási gyorsítás: magas megbízhatóságú egyképkockás gyorsút és határai, kicsinyített
értesítési kép, értesítő szálanként újrahasznosított HTTPS-kapcsolata.
Dekódolás: hardveres dekóder kiválasztása, visszaesés szoftveresre csak akkor, ha az működik,
stream-kiesésnél a beállítás megmarad; a hwdecode override csak a dekóder eszközt adja át; 85-ös JPEG.
Képforgatás: tartós beállítás és validáció, képkocka-forgatás új generációval, JSON-only API.
Csendes események: küszöbhatárok, kétszintű jelöltek, értesítés nélküli bizonyíték, átbillenés a
csendes cooldown alatt, klip elemzés nélkül, montázs-értesítés csak eszkalált epizódnál.
Átbillenéskor a csendes klip öröklése időablakkal, valódi FFmpeg concat újrakódolás nélkül, 20 s-os prompt.
Watchdog: döntési szabályok (türelmi idő, eszkaláció, ki-kapcsolás, gyakorisági korlátok), világ-
olvasható állapotfájl, a beállítások kulcsainak megőrzése, web-widget adatai és JSON-only kapcsoló.
Host-telepítő: futtatható és érvényes bash, kapcsolók, minden telepített fájl a repóban van, shellcheck
(ha elérhető). Élesben: `--check` és ismételt futás 0 változással, eltérő unit javítása mentéssel.
Watchdog-diagnosztika: pillanatkép mentése beavatkozás előtt, forgatás (az utolsó N marad), olvasható jogosultság.
A CI Ruff lintet és format-checket is futtat.

## Saját környezetben végzendő integrációs ellenőrzés

1. Friss RTSP URL-lel kapcsolódik és reconnectel a kamera.
2. Mozgó ember két pozitív AI frame-je után snapshot és esemény keletkezik.
3. Beállított ntfy esetén ténylegesen megérkezik a snapshot-értesítés.
4. Eseményklip és continuous recording tiszta leállítás után lezárt MP4-et ad.
5. Backup csak lezárt fájlt tölt fel; hiba után újrapróbálja; törlés csak
   sikeres upload után és csak videóra történik.
6. Pi-n a read-only modellmounttal látszik a hardverpanel; más hoston rejtett.
7. Desktop és mobil UI, lapozás, hiányzó szenzorok és offline kamera állapota.

A stream copy rögzítéshez a kamera codecje és több egyidejű RTSP kapcsolat
támogatása szükséges. A CI nem állítja, hogy a saját kamerád, Hetzner fiókod,
MediaMTX playered vagy többnapos működésed tesztelve van.
