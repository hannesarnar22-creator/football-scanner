# ⚽ Football Arbitrage Scanner

Greinir arbitrage-tækifæri í fótbolta (eingöngu **pre-match**) með því að bera saman stuðla frá
**Coolbet**, **bet365** og **Epicbet** — þegar staðfestar API-tengingar eru tiltækar.

**Markaðir:** Over/Under 1.5, 2.5, 3.5 mörk og Both Teams To Score (Yes/No).
**Sjálfgefin heildarupphæð:** 100.000 ISK.

> Kerfið setur **aldrei** veðmál sjálfvirkt. Það er eingöngu greiningartól.

## Staða

| Áfangi | Lýsing | Staða |
|---|---|---|
| 1 | Verkefnauppbygging, DEMO-mælaborð, prófanir | ✅ Lokið |
| 2 | Arbitrage-reiknivél (Decimal, námundun, gjöld, mörk) | ✅ Lokið |
| 3 | Gagnalíkön, samræming leikja/liða, arbitrage-leit, 60 DEMO-leikir | ✅ Lokið |
| 4 | Framkvæmdarhæfni og veðmálamörk | ✅ Lokið |
| 5 | SQLite-gagnagrunnur, saga stuðla og greining | ✅ Lokið |
| 6 | Faglegt mælaborð, 5%+ flipi, áhættumat og viðvaranir | ✅ Lokið |
| 7–8 | Ákveðið í samráði | ⏳ |

## Mælaborð (áfangi 6)

- **Hliðarstika:** gagnahamur (DEMO/LIVE), „Uppfæra gögn“, staða gagnauppsprettu, heildarfjárhæð,
  viðvörunarmörk (sjálfgefið 5%), síur og „Endurstilla síur“.
- **Yfirlitsreitir efst** fylgja síunum og eru reiknaðir úr sömu línum og taflan.
- **Flipar:** Mælaborð · 5%+ tækifæri · Reiknivél · Saga og greining · Veðmálamörk.
- **Áhættustöður:** 🟢 Uppfyllir skráð skilyrði · 🟡 Óstaðfest · 🔴 Úrelt · 🔴 Uppfyllir ekki skilyrði ·
  ⛔ Ósambærilegir markaðir. Ekkert tækifæri er merkt öruggt eða áhættulaust.
- **Viðvaranir:** ein á hvert tækifæri (vistað í SQLite, tengt auðkenni tækifæris). Aðeins sýndar í
  forritinu – enginn tölvupóstur, SMS eða push.
- **Uppfærsla í DEMO** breytir aðeins tímanum; leikir og stuðlar haldast óbreyttir.

## Gagnagrunnur og saga (áfangi 5)

SQLite-skrá í `data/scanner.sqlite` (breytanlegt með `DATABASE_URL` í `.env`).

- **Saga stuðla:** ný lína aðeins þegar stuðull breytist. Óbreyttur stuðull uppfærir bara „síðast séð“.
- **Tækifæri:** stöðugt auðkenni úr leik, markaði, línu, valkostum, veðmálasíðum og uppgjörsreglum
  (ekki stuðlum). Skráð er hvenær það sást fyrst, síðast og hvort það sé enn virkt.
- **DEMO og LIVE** eru aðskilin í öllum töflum og fyrirspurnum. DEMO-gögnum er hafnað ef reynt er
  að vista þau sem LIVE.
- **Idempotent:** sami innlestur aftur, endurræsing Streamlit eða ný keyrsla sama dag býr ekki til ný tækifæri.
- Sagan miðast alltaf við sjálfgefna heildarfjárhæð (100.000 kr.) svo hún sé sambærileg.
- Til að byrja upp á nýtt: stöðvaðu Streamlit og eyddu `data/scanner.sqlite`.

## Framkvæmdarhæfni (áfangi 4)

Fyrir hvorn valkost eru skráð: hámarks- og lágmarksveðmál, tiltæk inneign, gjaldmiðill,
veðmálaskref, þóknun, föst gjöld, tími síðustu staðfestingar og uppruni (API / handvirkt / DEMO).
Vanti gildi er það **UNKNOWN** – aldrei áætlað.

| Staða | Merking |
|---|---|
| 🟢 Uppfyllir skráð fjárhæðarskilyrði | Öll gildi þekkt, innan marka, jákvæður hagnaður í báðum úrslitum |
| 🟡 Óstaðfest | Eitthvert gildi UNKNOWN, eða óviss samsvörun (fuzzy / upphafstími) |
| 🔴 Uppfyllir ekki fjárhæðarskilyrði | Brýtur hámark, lágmark eða inneign, eða hagnaður ≤ 0 |
| 🟠 Úrelt eða ósambærilegt | Mörk eldri en 24 klst., stuðlar ekki samtímis, eða vantar gengi |

„Uppfyllir skráð skilyrði“ er **ekki** trygging fyrir því að veðmálasíða samþykki veðmálið.

**Hæsta mögulega fjárhæð** er hæsta heildarfjárhæð þar sem jöfnuð skipting (sami hagnaður
óháð úrslitum, fyrir utan námundun) uppfyllir öll skráð skilyrði og skilar jákvæðum hagnaði.

## Samræming og leit (áfangi 3)

- **Liðsheiti** eru samræmd með liðakóða → alias-skrá ([data/teams.json](data/teams.json)) → varfærnu
  fuzzy matching innan sömu keppni. Óviss fuzzy-samsvörun fer á yfirferðarlista og er aldrei notuð sjálfkrafa.
  Örugg fuzzy-samsvörun er notuð en merkir tækifærið **Óstaðfest**.
- **Leikir** eru aðeins sameinaðir ef keppni, heimalið, útilið og upphafstími (UTC) eru nákvæmlega eins.
- **Markaðir** parast aðeins ef tegund, lína og uppgjörsreglur eru eins (90 mín. + uppbótartími,
  venjulegar línur). Fyrri hálfleikur, framlenging, Asian totals og aðrar línur eru útilokuð.
- **Stuðlar** eldri en 5 mín. eru útilokaðir. Ef tímastimplar parsins eru meira en 120 sek. í sundur
  er tækifærið merkt **Óstaðfest**. Tvítekin gögn: nýjasti stuðull gildir.
- **Arbitrage**: hæsti gildi stuðull á hvorn valkost, alltaf frá tveimur ólíkum veðmálasíðum,
  reiknað með reiknivélinni úr áfanga 2.

Til að bæta við liði eða nýju heiti: bættu því í `aliases` í `data/teams.json`.

## Arbitrage-reiknivél

Fyrir tvo andstæða stuðla A og B:

- `S = 1/A + 1/B` — arbitrage ef `S < 1`
- Fræðileg framlegð = `(1/S − 1) × 100`
- `Stake A = T × (1/A) / S`, `Stake B = T × (1/B) / S`

Fjárhæðir eru námundaðar í heilar krónur þannig að summan sé nákvæmlega T.
Prófað er að námunda A bæði niður og upp og valinn sá kostur sem gefur hæstan
lægsta hagnað. Raunframlegð er reiknuð út frá lægsta hagnaði eftir námundun og gjöld.

**Gjöld:** þóknun (%) af nettóvinningi og föst gjöld (kr.) á hvert veðmál.
**Mörk:** lágmarksveðmál, hámarksveðmál og tiltæk inneign.

| Framkvæmdarhæfni | Merking |
|---|---|
| Framkvæmanlegt | Hagnaður > 0, öll gildi þekkt og innan marka |
| Óstaðfest | Hagnaður > 0 en eitthvert gildi (gjöld/mörk/inneign) vantar |
| Ekki framkvæmanlegt | Fjárhæð brýtur lágmark, hámark eða inneign |
| Enginn hagnaður eftir námundun/gjöld | S < 1 en lægsti hagnaður ≤ 0 |
| Ekki arbitrage | S ≥ 1 |

Forritið keyrir nú í **DEMO-ham** — tölur á mælaborðinu eru sýnigögn.

## Uppbygging

```
football-arbitrage-scanner/
├── app.py                  # Streamlit mælaborð
├── scanner/
│   ├── arbitrage.py        # Reiknivél (Decimal)
│   ├── feasibility.py      # Framkvæmdarhæfnimat og hæsta mögulega fjárhæð
│   ├── db.py               # SQLAlchemy-töflur og tenging
│   ├── history.py          # Vistun sögu (idempotent)
│   ├── analytics.py        # Greining og CSV
│   ├── history_view.py     # Flipinn „Saga og greining“
│   ├── risk.py             # Áhættustöður og aldur stuðla
│   ├── alerts.py           # Viðvaranir (SQLite)
│   ├── dashboard.py        # Síur, röðun og yfirlitstölur
│   ├── sidebar.py          # Hliðarstika og síur
│   ├── high_margin_view.py # Flipinn „5%+ tækifæri“
│   ├── alerts_view.py      # Viðvaranir í Streamlit
│   ├── ui_common.py        # Sameiginlegir Streamlit-hlutar
│   ├── calculator_view.py  # Reiknivélarflipi í Streamlit
│   ├── config.py           # Stillingar (.env), veðmálasíður og markaðir
│   ├── models.py           # Pydantic-gagnalíkön
│   ├── teams.py            # Samræming liða- og keppnisheita
│   ├── matching.py         # Samsvörun leikja milli veðmálasíðna
│   ├── scan.py             # Arbitrage-leit yfir samræmd gögn
│   ├── demo_data.py        # 60 tilbúnir DEMO-leikir
│   ├── demo.py             # Samantekt fyrir mælaborðið
│   ├── opportunities_view.py # Tækifæratafla með síum
│   └── formatting.py       # Íslensk talna- og dagsetningasnið
├── tests/                  # pytest prófanir
├── .streamlit/config.toml  # Dökkt þema
├── requirements.txt
├── pyproject.toml          # pytest-stillingar
└── .env.example
```

## Uppsetning (Windows / PowerShell)

Python 3.14 þarf að vera uppsett.

```powershell
# 1. Búa til virtual environment (aðeins einu sinni)
& "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe" -m venv .venv

# 2. Virkja það
.\.venv\Scripts\Activate.ps1

# 3. Setja upp pakka
python -m pip install -r requirements.txt

# 4. Búa til .env (valfrjálst)
Copy-Item .env.example .env
```

Ef PowerShell neitar að keyra `Activate.ps1`:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Ræsing

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Mælaborðið opnast á <http://localhost:8501>.

## Prófanir

```powershell
.\.venv\Scripts\Activate.ps1
pytest
```
