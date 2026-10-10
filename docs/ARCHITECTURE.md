# Teljes architektúra és adatfolyam

```mermaid
flowchart TB
    subgraph PI["Raspberry Pi host · Raspberry Pi OS"]
        CSI["IMX219 kamera"] --> CAP["rpicam-vid / libcamera-vid<br/>Host: camera-source.service<br/>H.264 · 720p · 24 FPS"]
        TEST["data/test/source.mp4<br/>Tesztvideó + 1 óra állókép"]
        subgraph STACK["Docker Compose projekt: camera-stack"]
            PUB["publisher · camera-stack image<br/>FFmpeg · stream-copy<br/>Teszt: végtelen lejátszás<br/>CSI: host stdin pipe"]
            MTX["mediamtx · MediaMTX image<br/>RTSP / HLS / WebRTC elosztó"]
            APP["camera · camera-stack image<br/>Python · OpenCV · YOLO11n / NCNN<br/>FastAPI · eseménykezelés"]
            PUB -->|"RTSP/TCP · H.264"| MTX
            MTX -->|"rtsp://mediamtx:8554/cam"| APP
        end
        CAP -->|"H.264 stdin · Compose one-off"| PUB
        TEST -->|"test-source profil · végtelenítés"| PUB
        DATA["Host ./data ↔ /data<br/>Snapshotok · SQLite · lezárt klipek<br/>Retention: alap 14 nap"]
        APP --> DATA
        TS["Tailscale · hoston, nem Dockerben"]
    end
    MTX -->|"RTSP :8554/cam"| NAS["Synology Surveillance Station<br/>Saját rögzítés / riasztások"]
    MTX -->|"HLS :8888 / WebRTC :8889"| WEB["Böngésző · élőkép"]
    APP -->|"HTTP :8000 · UI / API / hardver"| WEB
    TS -.->|"Privát tailnet hozzáférés"| WEB
    APP -->|"Opcionális HTTPS · snapshot + üzenet"| NTFY["ntfy · értesítés"]
    APP -->|"Opcionális rclone worker<br/>csak lezárt média"| CLOUD["Hetzner Storage Box / más remote"]
```

A publisher és az app ugyanazt az image-et használja, más paranccsal.
A teszt és a CSI forrás alternatíva: nem publikálhatnak egyszerre a `cam` útvonalra.
Tesztmódban mindhárom szolgáltatást a Compose indítja. Valódi kameránál csak
a CSI capture marad a hoston; FFmpeg Compose one-off konténerben fut.

## Az elemző app belső folyamata

```mermaid
flowchart TD
    R["RTSP reader · külön szál<br/>Legfrissebb frame · reconnect"] --> M["OpenCV MOG2<br/>Resize · grayscale · blur · zajszűrés<br/>Max 8 motion frame/s"]
    M --> C{"Megerősített mozgás?"}
    C -->|"nem"| R
    C -->|"igen / aktív motion"| A["Motion ROI + padding<br/>AI_MAX_FPS sebességkorlát<br/>YOLO11n · NCNN CPU · 320×320"]
    A --> Z["Person confidence ≥ 0,75<br/>Zónaszabály · most teljes kép"]
    Z --> V{"2 pozitív az utolsó<br/>3 AI-frame-ből?"}
    V -->|"nem"| R
    V -->|"igen + cooldown engedi"| E["Valid esemény<br/>UTC idő · label · confidence · bbox · zóna"]
    E --> S["Annotált snapshot + thumbnail<br/>SQLite eseménylista"]
    E --> K["Opcionális eseményklip<br/>FFmpeg stream-copy · pre-roll"]
    E --> N["Notification worker<br/>ntfy · saját cooldown"]
    R -.-> F["Opcionális 24/7 recorder<br/>Külön FFmpeg RTSP-kapcsolat<br/>Lezárt szegmensek"]
    S --> U["Opcionális upload worker<br/>Tartós queue · rclone · retry"]
    K --> U
    F --> U
    S --> UI["FastAPI UI / API<br/>Státusz + eseménylista"]
    H["Host /proc · thermal · disk<br/>Hardveradatok · 5 s mintavétel"] --> UI
```

A motion/AI FPS-korlát nem változtatja meg az élőkép vagy a stream-copy
rögzítés minőségét/FPS-ét. A böngésző a MediaMTX videóját kapja, nem YOLO-képkockákat.
Eseményklip, 24/7 felvétel és backup párhuzamosan is működhet. Aktívan írt `.tmp`
videó nem kerül feltöltésre. A helyi retention nem törli a cloud archívumot.

A jelenlegi Pi-n tesztstream, person AI és ntfy aktív; eseményklip, 24/7
rögzítés és backup kikapcsolva. Üzemeltetés: [CAMERA_SOURCE.md](CAMERA_SOURCE.md).
