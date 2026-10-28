# Reel + Story Otomatik Paylaşım — Nihai Rehber ve Implementasyon Planı

> **Hedef:** Onaylanan her içerik Telegram'dan tek dokunuşla **Reel**, **Story** ya da
> **ikisi birden** olarak işaretlenir. Her tür kendi saat listesine ve günlük
> limitine göre otomatik yayınlanır. İstenirse tek bir içeriğe elle saat verilir.
>
> Bu doküman üç parçadan oluşuyor:
> **A — Hesap kurulumu** (kod yazmadan önce, ~45 dk) ·
> **B — Implementasyon** (ticket'lar, kabul kriterleriyle) ·
> **C — Devreye alma ve günlük kullanım**

---

## Mevcut durum (başlangıç noktası)

| Parça | Durum |
|---|---|
| `instagram.publish_reel()` / `publish_image()` | ✅ Çalışıyor, `runner.publish_item` kullanıyor |
| `instagram.publish_story()` | ⚠️ Yazılmış, hiçbir yerden çağrılmıyor |
| `render_story()` / `render_story_image()` + `STORY` frame | ⚠️ Yazılmış, pipeline çağırmıyor |
| `RENDER_STORY` ayarı | ⚠️ `config.py`'de var, hiçbir yerde okunmuyor |
| Planlama | Tek slot listesi (`PUBLISH_SLOTS`) + tek limit (`MAX_PER_DAY`), içerik başına tek yayın |
| Şema değişikliği | **Migration yok** — `init-db` sadece `create_all()` çalıştırıyor. Mevcut tabloya kolon **eklenemez**, yeni tablo eklenebilir |
| `publish.yml` cron | Günde 3 kez (slot saatleri) — elle saat vermek için yetersiz |
| Workflow'lar | `RENDER_*` değişkenleri `worker.yml`'a geçirilmiyor |

---

# A — Hesap kurulumu

Kod tarafına başlamadan bunlar hazır olsun; B'deki testlerin son adımı gerçek
hesapla yapılıyor.

### A1. Instagram hesabı → **Business**
Instagram → Ayarlar → Hesap türü ve araçlar → **Profesyonel hesaba geç → İşletme**.

> ⚠️ Creator değil **Business** seç. API üzerinden Story paylaşımı Meta
> dokümantasyonunda business hesaplar için tanımlı; Creator'da Reel çalışıp
> Story'nin hata vermesi en olası kurulum sorunu. Facebook sayfası gerekmiyor.

### A2. Meta uygulaması
1. [developers.facebook.com](https://developers.facebook.com) → *Create app* → tür: **Business**
2. *Add product* → **Instagram** → *API setup with Instagram login*
3. **Instagram App ID** + **Instagram App Secret** → `IG_APP_ID`, `IG_APP_SECRET`
   (Facebook App ID değil, Instagram bölümündeki olanlar)
4. **App roles → Roles → Instagram Testers** → `komedi.molasii` ekle
5. Instagram uygulamasında: Ayarlar → Uygulamalar ve web siteleri → **Tester davetleri** → kabul et

> Uygulama *Development* modunda kalabilir. Sadece kendi hesabına paylaşım
> yaptığın için App Review gerekmiyor.

### A3. Token
1. Instagram ürün sayfasında *Generate access tokens* → hesabı ekle → izinler:
   `instagram_business_basic`, `instagram_business_content_publish`
2. Çıkan token kısa ömürlü (1 saat). **Hemen** 60 günlüğe çevir — `DATABASE_URL`
   bulut DB'yi gösterirken:
   ```bash
   uv run smauto exchange-token KISA_OMURLU_TOKEN
   ```
   Token DB'ye yazılır; haftalık yenileme bu kaydı günceller.
3. Kullanıcı ID'si:
   ```bash
   curl "https://graph.instagram.com/me?fields=id,username,account_type&access_token=TOKEN"
   ```
   `id` → `IG_USER_ID`. `account_type` **BUSINESS** olmalı.

### A4. Cloudflare R2
Instagram medyayı bizim verdiğimiz public URL'den kendisi çekiyor.

1. Cloudflare → **R2** → *Create bucket* (ör. `smauto-media`) → `R2_BUCKET`
2. Bucket → Settings → **Public Development URL → Enable** → `https://pub-xxxx.r2.dev` → `R2_PUBLIC_BASE_URL`
3. R2 ana sayfa → **Manage API Tokens** → *Object Read & Write*, sadece bu bucket
   → `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`; sayfadaki Account ID → `R2_ACCOUNT_ID`
4. Bucket → Settings → **Object lifecycle rules** → *30 gün sonra sil*

### A5. Kontrol
Yerel `.env`:
```env
DELIVERY_MODE=instagram
STORAGE_BACKEND=r2
AUTO_PUBLISH=false          # B bitene kadar kapalı
IG_USER_ID=...
IG_APP_ID=...
IG_APP_SECRET=...
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=smauto-media
R2_PUBLIC_BASE_URL=https://pub-xxxx.r2.dev
```
```bash
uv run smauto doctor     # "ig token" ve "depolama" ✓
```

---

# B — Implementasyon

## Tasarım kararları

**1. Yeni tablo: `publications`.** Bir içerik artık birden fazla yayına
dönüşebiliyor (reel + story), her birinin kendi saati, durumu, IG media id'si ve
hatası var. Bunu `items` tablosuna kolon ekleyerek çözmek hem migration
gerektirir (yok) hem de iki yayını tek satıra sıkıştırır. Yeni tablo `create_all()`
ile mevcut Postgres'te sorunsuz oluşur.

```
publications
  id, item_id → items.id (CASCADE)
  kind            "reel" | "story"
  status          "pending" | "scheduled" | "publishing" | "published" | "failed" | "cancelled"
  scheduled_for   UTC
  pinned          bool     # true = kullanıcı elle saat verdi, planner dokunmaz
  ig_media_id, ig_permalink, published_at
  attempts, last_error, next_attempt_at
  created_at, updated_at
  UNIQUE(item_id, kind)
```

**2. `Item.status` akışı aynen kalıyor**, sadece anlamı netleşiyor:
`APPROVED` = yayınları oluşturuldu, `SCHEDULED` = en az biri saat aldı,
`PUBLISHED` = bekleyen yayını kalmadı ve en az biri yayınlandı. Böylece
`states.py` ve mevcut testler bozulmuyor. `Item.ig_media_id` / `ig_permalink`
reel için doldurulmaya devam ediyor (geri uyumluluk, `/status`, `show`).

**3. "Reel" hedefi görsel içerikte feed post olur.** Kaynak fotoğrafsa reel
render edilemiyor; o durumda `kind="reel"` yayını mevcut `publish_image` ile
1080×1350 feed post olarak çıkar. Kullanıcı için buton yine "🎬 Reel/Post".

**4. Story ayrı render edilir**, reel dosyası kullanılmaz. İkisi de 1080×1920
ama güvenli alanlar farklı (`STORY` frame: üst/alt 250 px). Story'de caption
yok, takip kartı (follow) Story'de **çizilmez** — Story arayüzünde zaten profil
adı görünüyor ve alt bant yanıt kutusuyla çakışıyor.

**5. Story süresi 60 sn.** Reel üst sınırı 90 sn; story için ayrı sınır
gerekiyor, yoksa API container'ı `ERROR` döner.

**6. Zamanlama hassasiyeti cron'la belirlenir.** `publish.yml` her 15 dakikada
çalışacak; elle verilen saat ±15 dk (+ GitHub'ın birkaç dakikalık gecikmesi)
içinde yayınlanır. Public repoda Actions dakikası sınırsız.

## Yeni ayarlar

```env
# Telegram'daki ✅ butonunun varsayılanı: reel | story | both
DEFAULT_TARGETS=reel

# Reel / feed post
REEL_SLOTS=12:30,18:30,21:30
MAX_REELS_PER_DAY=3

# Story
STORY_SLOTS=09:30,13:30,17:00,20:00,23:00
MAX_STORIES_PER_DAY=5

# Aynı türden iki yayın arasında en az bu kadar dakika (elle saatlerde uyarı)
MIN_GAP_MINUTES=45

# Story render'ı (her içerik için story dosyası da üretilsin mi)
RENDER_STORY=true
```

`PUBLISH_SLOTS` / `MAX_PER_DAY` geri uyumluluk için `REEL_SLOTS` /
`MAX_REELS_PER_DAY`'in eski adları olarak okunmaya devam eder (yeni ad
doluysa o kazanır).

> Instagram sınırı: API ile **24 saatte 100 yayın**, reel + story toplamı.
> Varsayılanlar (3 + 5 = 8/gün) bunun çok altında; `doctor` `MAX_REELS + MAX_STORIES > 50`
> olursa uyarır.

---

## Ticket'lar

Sıra önemli: her ticket bir öncekinin üzerine kuruluyor ve **her biri tek PR**.

### P7-1 · `publications` tablosu + repo fonksiyonları
**Dosyalar:** `db/models.py`, `db/repo.py`, `db/states.py`

- `Publication` modeli (yukarıdaki şema), `Item.publications` ilişkisi (`selectin`, cascade)
- `db/states.py`: `PUB_PENDING`, `PUB_SCHEDULED`, `PUB_PUBLISHING`, `PUB_PUBLISHED`,
  `PUB_FAILED`, `PUB_CANCELLED` + geçiş tablosu `PUB_TRANSITIONS`
- `repo.py`:
  - `ensure_publications(session, item, kinds)` — idempotent, `UNIQUE(item_id, kind)`
  - `transition_pub(session, pub, new_status, message)` — `Event` loglar (`stage="publish:<kind>"`)
  - `mark_pub_failed(...)` — `attempts`, `next_attempt_at` (mevcut `mark_failed` backoff'unu yeniden kullan)
  - `sync_item_status(session, item)` — yayınların durumundan `Item.status`'u türetir
- **Backfill:** `init-db` sonunda `backfill_publications()` — `SCHEDULED`/`APPROVED`
  durumundaki ve hiç yayını olmayan item'lara `kind="reel"` yayın açar,
  `scheduled_for`'u kopyalar. Bu sayede geçiş anında kuyrukta olan içerik kaybolmaz.

**Kabul:**
- `uv run smauto init-db` iki kez çalıştırılınca hata yok, tablo bir kez oluşuyor
- Eski şemalı bir SQLite DB üzerinde `init-db` sonrası `SCHEDULED` item'ların her birinin tam bir `reel` yayını var
- `ensure_publications(item, ["reel","story"])` iki kez çağrılınca 2 satır (4 değil)

### P7-2 · Ayarlar
**Dosyalar:** `config.py`, `.env.example`, `doctor.py`

- `default_targets: Literal["reel","story","both"] = "reel"`
- `reel_slots`, `story_slots`, `max_reels_per_day`, `max_stories_per_day`, `min_gap_minutes`
- `slots_for(kind) -> list[time]`, `max_for(kind) -> int`
- Eski `publish_slots` / `max_per_day` → reel'e fallback (validator içinde)
- `doctor`: iki slot listesini ve günlük limitleri tek satırda gösterir; story
  açıksa `account_type == BUSINESS` kontrolü (`/me?fields=account_type`)

**Kabul:**
- Sadece `PUBLISH_SLOTS=10:00` verilince `settings.slots_for("reel") == [10:00]`
- `STORY_SLOTS=25:00` → başlangıçta açık hata mesajı
- `test_config.py`'ye 4 yeni test

### P7-3 · Story render'ı pipeline'a bağla
**Dosyalar:** `pipeline.py`, `render/video.py`, `render/image.py`

- `render_story(source, card, dest)` için `max_duration_s=60` parametresi
  (`_duration_plan`'a geçirilir); not metni "Story için 60 sn'ye kısaltıldı"
- `pipeline.render`: `settings.render_story` açıksa
  - video → `story.mp4` (`render_story`, follow kartı **yok**)
  - görsel → `story.jpg` (`render_story_image`)
  - `add_asset(variant="story")`
- Kart yüksekliği: story için `max_card_height(STORY, 0)` ile ayrı hesap
  (aynı `card.png` reel için daha kısa sınırla çizildiyse yeniden kullanılabilir;
  daha uzunsa story'ye özel `card_story.png` çizilir)
- `DELIVERY_MODE=instagram` iken story dosyası da render anında R2'ye yüklenir
  (reel'le aynı mekanizma)

**Kabul (ffprobe ile):**
- `story.mp4`: `1080x1920`, `h264`, `yuv420p`, `30fps`, AAC ses stream'i var, süre ≤ 60.0 sn
- 75 sn'lik kaynakta `reel.mp4` 75 sn, `story.mp4` 60 sn
- `RENDER_STORY=false` iken hiçbir `story` asset'i yok (mevcut testler aynen geçer)
- `test_render_real.py`'ye Türkçe + emoji içeren story kartı vakası

### P7-4 · Planner'ı tür bazlı yap
**Dosyalar:** `schedule/planner.py`

- `next_free_slot(session, kind, now)` — sadece aynı türün dolu slotlarına ve
  `max_for(kind)` limitine bakar
- `schedule_pub(session, pub, now)` — `pinned` ise dokunmaz
- `pin(session, pub, when)` — elle saat; geçmiş saat → hata; aynı türde
  `min_gap_minutes`'tan yakın yayın varsa **uyarı döner, engellemez**; günlük
  limit aşılıyorsa yine uyarı döner (kullanıcının açık isteği limitin önündedir)
- `due(session, now, limit)` — `scheduled` ve vakti gelmiş yayınlar, `next_attempt_at` saygılı
- Reel ve story aynı saate düşebilir (farklı yüzeyler), ama aynı içeriğin
  story'si reel'inden **en az 1 slot sonra** planlanır — story "yeni reel var"
  duyurusu gibi çalışsın

**Kabul:**
- `MAX_REELS_PER_DAY=3`, 5 reel onaylanınca: 3'ü bugün, 2'si yarın
- Aynı anda 5 story + 3 reel: story'ler story slotlarına, reel'ler reel slotlarına
- `pin` edilmiş yayın sonraki planlama turunda yer değiştirmiyor
- Saat dilimi: `TIMEZONE=Europe/Istanbul`, slot `21:30` → DB'de `18:30Z`

### P7-5 · Runner: yayın bazlı publish
**Dosyalar:** `publish/runner.py`, `publish/instagram.py`

- `run()`:
  1. `APPROVED` item'lar → `pending` yayınları planla → item `SCHEDULED`
  2. `AUTO_PUBLISH` kapalıysa dur (bugünkü davranış)
  3. `planner.due()` → her yayın için `publish_publication()`
  4. Her yayından sonra `sync_item_status()`
- `publish_publication(pub)`:
  - `reel` → `reel` asset varsa `publish_reel(..., cover_url=feed)`, yoksa `publish_image(feed)`
  - `story` → `story` asset'i; video ise `publish_story(url, is_video=True)`
  - Story asset'i yoksa (eski içerik) → yayın `failed`, mesaj: "Story render'ı yok — 🔁 ile yeniden çiz"
- **Kota kontrolü:** turun başında bir kez
  `GET /{ig-user-id}/content_publishing_limit?fields=quota_usage,config`;
  kalan kota < yayınlanacak sayı ise fazlası bir sonraki tura kalır, Telegram'a bildirim
- Bildirim: `🚀 #12 Reel yayınlandı` / `📱 #12 Story yayınlandı` + permalink
- Hata ayrımı: `instagram.py`'deki `retryable` bayrağı → `mark_pub_failed`;
  kalıcı hatada (ör. format reddi) yayın `failed`, diğer tür etkilenmez

**Kabul:**
- Reel başarılı + story başarısız → item `PUBLISHED`, story yayını `failed`, Telegram'a tek hata mesajı
- `AUTO_PUBLISH=false` → hiçbir HTTP çağrısı yapılmıyor (`respx` ile doğrula)
- Kota dolu (`quota_usage=100`) → 0 yayın, bildirim gidiyor
- Tüm testler `respx`/fixture ile, gerçek API çağrısı yok

### P7-6 · Telegram: hedef seçimi + saat
**Dosyalar:** `telegram/delivery.py`, `telegram/intake.py`

Önizleme mesajındaki yeni klavye:
```
[ 🎬 Reel ] [ 📱 Story ] [ 🎬+📱 İkisi ]
[ 🕒 Saat  ] [ ✏️ Caption ] [ 🔁 ] [ ❌ ]
```
- Callback'ler: `a:<id>:r`, `a:<id>:s`, `a:<id>:b`, `a:<id>:t` (64 bayt sınırının çok altında)
- Hedef butonuna basınca: `ensure_publications` → item `APPROVED`, mesaj
  "Onaylandı ✅ Reel: 18:30 · Story: 20:00" (planner'dan tahmini saat)
- `🕒 Saat` → bot "Saati yaz: `19:45`, `yarın 09:00`, `25.10 21:00`" der;
  bir sonraki düz metin mesajı bu item'a bağlanır (mevcut `✏️ Caption` bekleme
  mekanizmasıyla aynı desen, `KeyValue`'da `pending_time:<chat_id>`)
- Saat girildikten sonra hangi tür için olduğu sorulur (`🎬` / `📱` / `ikisi`) — tek tür seçiliyse sorulmaz

Yeni komutlar:
| Komut | İş |
|---|---|
| `/plan` | Önümüzdeki 48 saatin yayın listesi: `21.10 18:30 🎬 #12`, `21.10 20:00 📱 #12` |
| `/saat 12 story 19:45` | Kısa yol: butonsuz elle saat |
| `/iptal 12 story` | Tek bir yayını iptal et (`cancelled`), diğeri kalır |
| `/simdi 12 reel` | Sıradaki publish turunda yayınla (saat = şimdi, `pinned`) |

Saat ayrıştırıcı (`schedule/parse.py`, saf fonksiyon): `HH:MM` (bugün; geçtiyse
hata, otomatik yarına **kaydırmaz**), `yarın HH:MM`, `GG.AA HH:MM`. Hepsi `TIMEZONE`'da.

**Kabul:**
- Her callback ve komut için birim test (`test_intake.py`)
- Yetkisiz kullanıcı yeni butonlara basınca "Yetkin yok." (mevcut kontrol)
- `/saat 12 story 03:00` (geçmiş saat) → açıklayıcı hata, DB değişmiyor
- `HELP_TEXT` güncel

### P7-7 · Unpublish + show
**Dosyalar:** `publish/runner.py`, `cli.py`

- `unpublish`: item'ın tüm `published` yayınlarını siler. Story'ler 24 saatte
  kendiliğinden kaybolduğu için silme hatası story'de **uyarıya** düşürülür
- `smauto show <id>`: yayın tablosu (tür, durum, saat, permalink, son hata)
- `smauto plan`: `/plan`'ın CLI karşılığı

### P7-8 · Workflow'lar
**Dosyalar:** `.github/workflows/publish.yml`, `worker.yml`

`publish.yml`:
```yaml
on:
  schedule:
    - cron: "*/15 * * * *"      # yayın turu
    - cron: "7 4 * * 1"         # haftalık token yenileme
  workflow_dispatch:
```
- Token adımının koşulu `github.event.schedule == '7 4 * * 1'` olarak güncellenir
- Yeni env'ler (her iki workflow): `DEFAULT_TARGETS`, `REEL_SLOTS`, `STORY_SLOTS`,
  `MAX_REELS_PER_DAY`, `MAX_STORIES_PER_DAY`, `MIN_GAP_MINUTES`, `RENDER_STORY`,
  `RENDER_REEL`, `RENDER_FEED` → hepsi `vars.X || '<varsayılan>'`
- Erken çıkış: vakti gelen yayın yoksa `smauto publish` saniyeler içinde biter;
  her tur `uv sync` cache'iyle ~40 sn

**Kabul:** `workflow_dispatch` ile elle çalıştırma, vakti gelen yayın yokken yeşil ve < 2 dk

### P7-9 · Dokümanlar
`KULLANIM.md` Bölüm 6, `SETUP.md`, `SPEC.md` §0 (sapma kaydı: "tek yayın → yayın
tablosu"), `ROADMAP.md`'de P6-3'ü P7'ye taşı, `README.md` komut listesi.

---

## Test özeti

| Dosya | Yeni test |
|---|---|
| `test_states_and_repo.py` | publication geçişleri, `ensure_publications` idempotent, backfill, `sync_item_status` |
| `test_config.py` | slot/limit ayrıştırma, eski ad fallback'i |
| `test_schedule_and_publish.py` | tür bazlı slot dağıtımı, pin, kota, kısmi başarısızlık |
| `test_intake.py` | yeni butonlar, `/saat`, `/plan`, `/iptal`, `/simdi`, saat ayrıştırıcı |
| `test_render_real.py` (slow) | story.mp4 ffprobe kriterleri, 60 sn kısaltma |

```bash
uv run pytest -m "not slow" && uv run pytest -m slow
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
```

---

# C — Devreye alma ve günlük kullanım

### C1. GitHub'a ekle
Repo → Settings → Secrets and variables → Actions

**Secrets:**
`IG_USER_ID`, `IG_ACCESS_TOKEN`, `IG_APP_ID`, `IG_APP_SECRET`,
`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_BASE_URL`

**Variables:**
| Ad | Değer |
|---|---|
| `DELIVERY_MODE` | `instagram` |
| `STORAGE_BACKEND` | `r2` |
| `RENDER_STORY` | `true` |
| `DEFAULT_TARGETS` | `both` (ya da `reel`) |
| `REEL_SLOTS` | `12:30,18:30,21:30` |
| `STORY_SLOTS` | `09:30,13:30,17:00,20:00,23:00` |
| `MAX_REELS_PER_DAY` | `3` |
| `MAX_STORIES_PER_DAY` | `5` |
| `AUTO_PUBLISH` | **en son**, C2'den sonra `true` |

### C2. Kademeli açılış
1. **Gün 1 — render kontrolü.** `AUTO_PUBLISH` yok. 2–3 içerik gönder.
   Telegram'da önizleme gelsin, R2 bucket'ında `<id>/reel.mp4` ve `<id>/story.mp4`
   görünsün. `smauto show <id>` → iki asset'in de `remote_url`'i dolu.
2. **Gün 1 — tek elle yayın.** Bir içeriğe `🎬+📱`, sonra `/simdi <id> reel`.
   Actions → **publish** → *Run workflow*. Instagram'da Reel'i gör, sonra aynısını story için yap.
3. **Gün 2 — otomatik.** `AUTO_PUBLISH=true`. `/plan` ile sırayı izle.
4. **İlk hafta.** Her akşam `/plan` + `/stats`. Pazartesi token yenileme
   koşusunun yeşil olduğunu gör (Actions → publish → `7 4 * * 1` tetiklemesi).

### C3. Günlük kullanım
```
Telefonda Paylaş → Telegram → bot
        ↓  (~5 dk, worker)
Önizleme: video + caption + butonlar
        ↓
🎬 Reel  │ 📱 Story │ 🎬+📱 İkisi     → sıradaki boş slotlara yerleşir
🕒 Saat  → "19:45" yaz → tür seç       → tam o saate (±15 dk) sabitlenir
        ↓
"🚀 #12 Reel yayınlandı" / "📱 #12 Story yayınlandı"
```

| İhtiyaç | Ne yap |
|---|---|
| Bugün ne çıkacak? | `/plan` |
| Bir story'yi vazgeç | `/iptal 12 story` |
| Hemen paylaş | `/simdi 12 reel` |
| Belirli saat | `/saat 12 story 22:15` |
| Yanlış/şikayet | `smauto unpublish 12` |
| Slotları değiştir | GitHub Variables → `REEL_SLOTS` / `STORY_SLOTS` (cron'a dokunmak **gerekmiyor**, her 15 dk çalışıyor) |

### C4. Sorun giderme
| Belirti | Olası neden | Çözüm |
|---|---|---|
| Reel çıkıyor, story `failed` | Hesap Creator | Instagram'da hesap türünü **İşletme** yap; `doctor` `account_type` satırı |
| Story container `ERROR` | Video > 60 sn veya ses yok | `ffprobe story.mp4`; P7-3 kriterleri |
| Eski içerikte "Story render'ı yok" | `RENDER_STORY` açılmadan render edilmiş | Önizlemede `🔁 Yeniden çiz` |
| Elle saat 20+ dk gecikti | GitHub cron gecikmesi (yoğun saatlerde olur) | Normal; kritikse saati 15 dk erken ver |
| "Kota doldu" bildirimi | 24 saatte 100 yayın | Limitleri düşür; ertesi tur otomatik devam eder |
| Hiçbir şey yayınlanmıyor | `AUTO_PUBLISH`/`DELIVERY_MODE` variable eksik ya da token bitti | `smauto doctor`; `smauto refresh-tokens` |
| Container `ERROR`, her tür | R2 URL public değil | Tarayıcıda `R2_PUBLIC_BASE_URL/<id>/reel.mp4` açılıyor mu |

### C5. Bakım
| Sıklık | İş |
|---|---|
| Haftalık (otomatik) | Token yenileme |
| Haftalık (sen) | `/stats`, `doctor` token gün sayısı |
| Aylık | R2 kullanımı, lifecycle kuralı çalışıyor mu |

---

## Kaynaklar
- [Instagram API with Instagram Login — Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [IG User Media (REELS / STORIES parametreleri)](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media)
- [Content Publishing Limit](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit)
