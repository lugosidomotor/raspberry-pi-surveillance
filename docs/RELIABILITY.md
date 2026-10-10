# Megőrzés és helyreállítás

- `RETENTION_DAYS=14`: helyi event és lezárt média megőrzés, pozitív egész nap.
- `MIN_FREE_DISK_MB=512`: 10 másodpercenkénti ellenőrzés; küszöb alatt új snapshot
  és rögzítés szünetel. A rendszer nem törli a megőrzési időn belüli bizonyítékot.
- Óránkénti age-based cleanup; SQLite-sorok és fájlok együtt törlődnek.
  Az upload-claim és retention közös lockot használ, aktív `.tmp` nem törölhető.
  A távoli archívum megőrzését külön, a tárhelyen kell beállítani.
- Eseményidő UTC, SQLite-rendezés epoch szerint. A régi offsetes ISO-adatokat
  induláskor migráljuk; a fájlnevek és ID-k változatlanok maradnak.
  Régi offset nélküli adatoknál csak a host helyi időzónája ismert, a DST fold
  utólag nem állítható vissza biztosan.
- Fatális pipeline-hiba után 1, 2, 4, 8, 16, 30 másodperces retry. A web és
  diagnosztika közben él; `/health` 503 a hiba alatt, helyreálláskor ismét 200.
- Az uploader induláskor pótolja a lezárt, nem regisztrált fájlokat. Később a
  recorderek SQLite-katalógusát olvassa; nem járja újra percenként a tárhelyet.
  Hiányzó fájl terminális `missing` státusz. Az `uploading` sorok újrainduláskor
  `pending` státuszra állnak, a törölt fájlok nem próbálkoznak végtelenül.
- A manifest beolvasása inkrementális, másodpercenként legfeljebb egyszer;
  nem nő korlátlan `seen` halmaz. A manifest 4 MB felett új munkamenetet kap.
- Eseményklip pre-roll: külön, rövid stream-copy puffer, csak lezárt szegmensek.
  A kulcsképkockák szabják meg a pontosságot. Nincs frame-pontos transzkódolás.
  Ez egy plusz RTSP kapcsolat, nem a MediaMTX recordingra történő átállás.
  Összefűzés előtt minden rész azonos 90 kHz-es track timebase-re remuxolódik,
  továbbra is `-c copy` mellett, a segment/event muxerek időalap-eltérését kizárva.

## Reprodukálható Python csomagok

A Docker a `requirements-common.lock`, `requirements-pytorch.lock` és
`requirements-dev.lock` teljes, pontos verziós listákat használja, Python 3.11
Linux ARM64-en feloldva; az AMD64 build ellenőrzése a CI feladata.
A Python base image multiarch digestre rögzített. Az apt csomagok továbbra is a
Debian biztonsági repositoryból jönnek: ez nem bitazonos Debian snapshot build.
A YOLO súly buildkori upstream letöltés: teljes artifact-reprodukálhatósághoz
külön súly-checksum vagy saját artifact mirror is szükséges.

Frissítés (`uv` szükséges):

```bash
uv pip compile requirements-common.in --python-version 3.11 --python-platform aarch64-unknown-linux-gnu -o requirements-common.lock
uv pip compile requirements-pytorch.in --python-version 3.11 --python-platform aarch64-unknown-linux-gnu --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match -o requirements-pytorch.lock
uv pip compile requirements-dev.in --python-version 3.11 --python-platform aarch64-unknown-linux-gnu -o requirements-dev.lock
docker build --target test -t camera-stack:test .
docker run --rm --network none camera-stack:test
docker run --rm --entrypoint ruff camera-stack:test check app tests deploy tools
docker run --rm --entrypoint ruff camera-stack:test format --check app tests deploy tools
```

Hálózati bind/auth ehhez a javításhoz nem változott.
