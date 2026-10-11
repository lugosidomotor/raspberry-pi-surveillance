# Napi áttekintés: aktivitás, kördiagram és naptár

Az áttekintő oldal második sora a tárolt, validált események napi képe. A vizuális
rendszer forrása a [DESIGN.md](DESIGN.md). Az `/events` oldalon ezek a kártyák rejtettek.

## Adatok és időhatárok

A kiválasztott napot a böngésző saját időzónájában számított éjfél és a következő
éjfél határolja, így az óraátállítás napjai 23 vagy 25 órásak is lehetnek. A
`GET /api/timeline?start=…&end=…` epoch másodperceket fogad; a tartománynak végesnek,
pozitív hosszúságúnak és legfeljebb 26 órásnak kell lennie.

A válasz mezői: `start`, `end`, `bucket_seconds` (900), `total`, `labels` és
`buckets`. A `buckets` 15 perces csoportonként adja a pontos darabszámot és a csoport
legutóbbi eseményét (`index`, `count`, `event`). A `labels` a tartomány eseményeit
számolja objektumcímkénként, például `{"person": 12, "car": 3}`. Mindkettő a teljes
napi katalógusból számol, függetlenül az eseménynapló lapozásától és szűrőjétől.

## Kártyák

- **Aktivitás:** a nap nyolc 3 órás szakasza kapszulaként. A kitöltés a nap
  legnagyobb szakaszához viszonyít, a szám a kapszula tetején áll. Ha a kiválasztott
  nap a mai, a mostani szakasz lime színű. Ez validált eseménytörténet, nem
  folyamatos mozgásmérés; üres szakasz nulla marad.
- **Áttekintés:** kördiagram a nap eseményeiről címkénként (a `labels` alapján), a
  közepén az összes darabszám. A jelmagyarázat minden követett objektumot mutat,
  alatta a változással az előző naphoz képest (a mező: az előző nap `labels`-e).
- **Naptár:** a kiválasztott nap hete, hétfőtől vasárnapig. A mai nap alatt lime
  pont van, a jövőbeli napok le vannak tiltva. Egy napra kattintva az aktivitás és
  az áttekintés arra a napra vált; a nyilak hetet lépnek.
- **Utolsó találat:** a legutóbbi esemény bélyegképe, címkéje, zónája, ideje és
  állapotpirulája; a kép a snapshotot nyitja új lapon.

## Hozzáférhetőség és frissítés

A kapszulák listaelemek, mindegyik beszédes nevet kap (például „06–09 óra:
4 esemény”). A kördiagram `role="img"`, a neve felsorolja a darabszámokat; az
aktivitás összesítője `role="status"`. A naptár gombjai `aria-pressed` állapotot
és teljes dátumot kapnak.

A látható oldal öt másodpercenként frissül. Minden kártya csak adatváltozáskor
épül újra, így a fókusz és a képek megmaradnak.

| Fájl | Felelősség |
| --- | --- |
| `app/static/index.html` | A kártyák szerkezete és az SVG-szimbólumok. |
| `app/static/style.css` | Kapszulák, kördiagram, naptár, mobil elrendezés. |
| `app/static/app.js` | Helyi nap, lekérések, megjelenítés, változásalapú frissítés. |
| `app/web.py` | `/api/timeline` tartományellenőrzése és a `labels` mező. |
| `app/events.py` | Időszakonkénti és címkénkénti SQL-összesítés. |
