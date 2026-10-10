# Napi idővonal és funkcióikonok

Az élőkép alatti „Ma” idővonal a tárolt, validált események napi áttekintése. A meglévő világos, palaszürke és teal felületet egészíti ki; a vizuális rendszer forrása továbbra is [DESIGN.md](DESIGN.md). Az `/events` oldalon az élőkép és az idővonal rejtett.

## Adatok és időhatárok

A böngésző saját időzónájában számított mai éjfél és következő éjfél határolja a napot. Az óraátállítás napjai ezért 23 vagy 25 órásak is lehetnek. A `GET /api/timeline?start=…&end=…` epoch másodperceket fogad; a tartománynak végesnek, pozitív hosszúságúnak és legfeljebb 26 órásnak kell lennie.

A SQLite-lekérdezés a kezdő időponttól számított, 900 másodperces csoportokba sorolja az eseményeket a `[start, end)` tartományban. Minden foglalt időszak pontos eseményszámát és legutóbbi eseményének bizonyítékát adja vissza. Az összesítés a teljes napi katalógust számolja, függetlenül az eseménylista 24 soros lapozásától és objektumszűrőjétől. A válasz mezői: `start`, `end`, `bucket_seconds`, `total`, valamint az `index`, `count`, `event` mezőket tartalmazó `buckets`.

A borostyán oszlopok az eseménysűrűséget mutatják, az aznapi legnagyobb értékhez viszonyítva. A teal jelölő minden foglalt időszak legutóbbi találatának tényleges időpontjára kerül. A szaggatott „most” vonal a jelenlegi időt jelzi. Ez validált eseménytörténet, nem folyamatos mozgásmérés; hiányzó adat helyére nem kerül kitalált aktivitás. Üres napnál szöveges üres állapot jelenik meg. A tesztforrásból származó találatok megtartják a „TESZT” címkét.

## Használat és hozzáférhetőség

A jelölő fölé vitt egér vagy billentyűzetes fókusz előnézetet nyit: thumbnail (ennek hiányában snapshot), objektum, megbízhatóság, időpont és az időszak eseményszáma. A kép `src` értéke csak másik bizonyíték megjelenítésekor változik. A jelölő kattintása a snapshotot új lapon nyitja meg, `noopener` kapcsolattal.

Az előnézetre át lehet vinni az egeret; az elhagyás után 180 ms türelmi idő marad. Az Escape, a görgetés és az ablakméret változása bezárja. A jelölők beszédes akadálymentes neve tartalmazza az időpontot, a darabszámot, a legutóbbi objektumot, a megbízhatóságot és a megnyitás célját; a meglévő teal fókuszkeret érvényes rájuk.

A „Találatok böngészése” natív `details` elem alapból zárt. Ugyanazokat a snapshotokat elérhető szöveges hivatkozásokkal kínálja, legalább 44 px magas összecsukó vezérlővel és linkekkel. Ez érintéshez és sűrű napokhoz is használható: egy szokásos nap 96 időszakát nem kell a keskeny grafikonon kiválasztani; a lista 240 px magas, görgethető területre korlátozott. Mobilon a fejléc és a jelmagyarázat egymás alá kerül. A grafikon dekoratív elemei `aria-hidden` jelölést kapnak, a napi összesítés `role="status"` állapotjelzés.

A látható oldal öt másodpercenként frissül. A teljes idővonalválasz aláírása csak adatváltozáskor engedi újraépíteni a jelölőket és a böngészőlistát, így változatlan adatoknál megmarad a fókusz és az előnézet képe. A „most” pozíciója minden sikeres lekéréskor frissül.

## Funkcióikonok és forrásfájlok

Az Integrációk öt sora és a Tárhely két sora kis, 15 px méretű vonalas SVG-ikont kapott: értesítés, cloud backup, feltöltés, eseményklip, folyamatos rögzítés, megőrzési idő és mentés állapota. A hét ikon helyi SVG-szimbólumot használ; `aria-hidden="true"` mellett a szöveges címke és állapot hordozza a jelentést. Külső ikoncsomag vagy generált raszterkép nem szükséges.

| Fájl | Felelősség |
| --- | --- |
| `app/static/index.html` | Idővonal, böngészőlista, előnézet és SVG-szimbólumok szerkezete. |
| `app/static/style.css` | Teal jelölők, borostyán sűrűség, előnézet, érintési célok és mobil elrendezés. |
| `app/static/app.js` | Helyi nap, API-lekérés, megjelenítés, előnézet és változásalapú frissítés. |
| `app/web.py` | `/api/timeline` tartományellenőrzése és publikus eseményadatok. |
| `app/events.py` | Pontos időszakonkénti SQL-összesítés és legutóbbi bizonyíték. |

A bővítés ellenőrzése: 61 sikeres Python-teszt, valamint 1440 px asztali és 390 px mobil böngészős ellenőrzés. A módosítás nem tartalmaz telepítést vagy hitelesítési változtatást.
