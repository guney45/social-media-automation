# Teknik Spesifikasyon

Implementasyonun sözleşmesi. `ROADMAP.md` ticket'ları buraya referans verir.

---

## 1. Dizin yapısı

```
src/smauto/
  __init__.py
  cli.py                  # typer: ingest / process / publish / refresh-tokens / doctor
  config.py               # pydantic-settings, tüm env değişkenleri
  logging.py              # structlog, JSON çıktı

  db/
    models.py             # SQLAlchemy modelleri (§2)
    session.py
    migrations/           # alembic

  intake/
    base.py               # SourceAdapter protokolü
    telegram.py           # varsayılan intake
    x_likes.py            # P6, opsiyonel
    bookmarklet.py        # P6, opsiyonel

  resolve/
    base.py               # Resolver protokolü + ResolvedPost (§3)
    chain.py              # fallback zinciri
    fxtwitter.py
    ytdlp.py
    gallerydl.py
    xapi.py               # P6, opsiyonel
    manual.py

  screen/
    dedupe.py             # pHash + tweet_id
    ai_gate.py            # Claude içerik filtresi
    blocklist.py

  render/
    card.py               # Playwright HTML→PNG
    templates/
      tweet_card.html.j2
      card.css
    layout.py             # safe area + yerleşim hesabı (§5.2)
    video.py              # ffmpeg reels kompozisyonu
    image.py              # Pillow feed/story kompozisyonu
    fonts/                # Inter + Noto Color Emoji (repoda gömülü)

  caption/
    writer.py
    prompts.py

  storage/
    base.py               # Storage protokolü
    local.py
    r2.py

  publish/
    telegram_delivery.py  # önizleme + inline butonlar
    instagram.py          # container → poll → publish
    tokens.py             # long-lived token yenileme

  schedule/
    planner.py            # slot ataması

tests/
  fixtures/               # örnek tweet JSON'ları, küçük mp4/jpg örnekleri
  ...
```

---

## 2. Veri modeli

Alembic ile migrate edilir. SQLite ve Postgres'te aynı şema.

### `items` — ana kuyruk

| Alan | Tip | Not |
|---|---|---|
| `id` | int PK | |
| `source_platform` | str | `x` \| `instagram` \| `tiktok` \| `manual` |
| `source_url` | str | orijinal link |
| `source_id` | str | platform içi id, **UNIQUE(source_platform, source_id)** |
| `author_handle` | str? | `@` olmadan |
| `author_name` | str? | |
| `author_avatar_url` | str? | |
| `text` | text? | tweet metni, t.co linkleri temizlenmiş |
| `lang` | str? | `tr` \| `en` \| ... |
| `source_created_at` | datetime? | |
| `status` | str | §2.1 |
| `reject_reason` | str? | |
| `phash` | str? | 64-bit hex, dedupe için |
| `dupe_of_item_id` | int? | FK → items.id |
| `ai_caption` | text? | |
| `ai_hashtags` | text? | JSON dizi |
| `ai_score` | int? | 0–100 komiklik/uyum skoru |
| `ai_flags` | text? | JSON dizi, ör. `["watermark"]` |
| `telegram_chat_id` | int? | |
| `telegram_message_id` | int? | önizleme mesajı, edit için |
| `scheduled_for` | datetime? | UTC |
| `published_at` | datetime? | |
| `ig_media_id` | str? | |
| `ig_permalink` | str? | |
| `attempts` | int | varsayılan 0 |
| `last_error` | text? | |
| `created_at` / `updated_at` | datetime | |

### `media_assets`

| Alan | Tip | Not |
|---|---|---|
| `id` | int PK | |
| `item_id` | int FK | |
| `kind` | str | `source` \| `render` |
| `variant` | str | `original` \| `card` \| `reel` \| `feed` \| `story` |
| `local_path` | str? | |
| `remote_url` | str? | R2 public URL |
| `width` / `height` | int? | |
| `duration_ms` | int? | |
| `bytes` | int? | |
| `mime` | str? | |

### `events` — gözlemlenebilirlik

`id`, `item_id?`, `stage`, `level` (`debug`/`info`/`warn`/`error`), `message`,
`payload_json`, `created_at`.

Her aşama geçişi buraya yazılır. Hata ayıklama ve "neden bu item takıldı" sorusu
buradan cevaplanır.

### `blocklist`

`id`, `kind` (`author` \| `domain` \| `phash`), `value`, `reason`, `created_at`.

### `tokens`

`id`, `provider` (`instagram` \| `x`), `access_token`, `refresh_token?`,
`expires_at`, `updated_at`.

> DB'ye token yazılacaksa dosya izinleri 600 olmalı; bulut DB kullanılıyorsa
> `TOKEN_ENCRYPTION_KEY` ile Fernet şifreleme uygulanır.

### 2.1 Durum makinesi

```
queued
  → fetching → fetched          (resolve başarılı)
             → needs_manual     (4. katmana düştü, kullanıcıdan dosya bekleniyor)
             → failed           (tüm katmanlar başarısız, attempts tükendi)
  fetched → screening → screened
                      → rejected          (dedupe / AI filtresi / blocklist)
  screened → rendering → rendered
                       → failed
  rendered → captioning → awaiting_approval
  awaiting_approval → approved            (kullanıcı ✅)
                    → rejected            (kullanıcı ❌)
                    → screened            (kullanıcı 🔁 yeniden çiz)
  approved → scheduled → publishing → published
                                    → failed
```

Kurallar:
- `failed` **terminal değil** — `attempts < MAX_ATTEMPTS` ise exponential backoff ile
  bir önceki aşamaya geri döner (`1m, 5m, 25m`). Tükenince Telegram'a bildirilir.
- `rejected` terminal. Gerekçe her zaman `reject_reason`'da.
- Her geçiş `events`'e yazılır.
- `process` komutu idempotent olmalı — aynı item iki kez işlenirse yeni yan etki
  üretmemeli.

---

## 3. Modül arayüzleri

### 3.1 `resolve/base.py`

```python
@dataclass
class ResolvedMedia:
    url: str | None            # indirilecek uzak URL
    local_path: Path | None    # zaten indirilmişse
    kind: Literal["image", "video", "gif"]
    width: int | None
    height: int | None
    duration_ms: int | None

@dataclass
class ResolvedPost:
    platform: Literal["x", "instagram", "tiktok", "manual"]
    source_id: str
    source_url: str
    author_handle: str | None
    author_name: str | None
    author_avatar_url: str | None
    text: str | None
    lang: str | None
    created_at: datetime | None
    media: list[ResolvedMedia]
    is_protected: bool = False      # True ise pipeline anında reject eder
    quoted: "ResolvedPost | None" = None
    raw: dict | None = None         # debug için ham yanıt

class Resolver(Protocol):
    name: str
    def supports(self, url: str) -> bool: ...
    def resolve(self, url: str) -> ResolvedPost: ...   # başarısızlıkta ResolveError
```

`chain.py` sırayla dener, her denemeyi `events`'e yazar, ilk başarılıyı döner.
Hepsi başarısızsa `needs_manual` durumuna geçirir.

**Metin temizleme (tüm resolver'lar için ortak):** sondaki `https://t.co/...` linkleri
kaldırılır; HTML entity'leri (`&amp;` → `&`) çözülür; `\n\n\n+` → `\n\n`.

### 3.2 `render/card.py`

```python
def render_tweet_card(
    post: ResolvedPost,
    *,
    theme: Literal["dark", "light"] = "dark",
    width_px: int = 984,
    scale: int = 2,          # retina
) -> Path:                   # şeffaf arkaplanlı PNG
```

### 3.3 `storage/base.py`

```python
class Storage(Protocol):
    def put(self, path: Path, key: str, *, content_type: str) -> str:  # public URL
    def delete(self, key: str) -> None
    def public_url(self, key: str) -> str
```

`local.py` P1–P4 için (URL yerine `file://`), `r2.py` P5'te devreye girer.

### 3.4 `publish/instagram.py`

```python
def publish_reel(video_url: str, caption: str) -> IGPublishResult
def publish_image(image_url: str, caption: str) -> IGPublishResult
def publish_carousel(image_urls: list[str], caption: str) -> IGPublishResult
```

Container poll'u: `GET /{container-id}?fields=status_code,status`, 5 sn aralık,
maksimum 5 dakika. `status_code`: `IN_PROGRESS` → bekle, `FINISHED` → yayınla,
`ERROR` / `EXPIRED` → `failed` + hata mesajını `last_error`'a yaz.

---

## 4. Medya spesifikasyonları

### 4.1 Çıktı formatları

| Varyant | Boyut | Oran | Kullanım |
|---|---|---|---|
| `reel` | 1080 × 1920 | 9:16 | Reels — **video içerikte varsayılan** |
| `feed` | 1080 × 1350 | 4:5 | Feed görseli — **statik içerikte varsayılan** |
| `story` | 1080 × 1920 | 9:16 | Story çapraz paylaşımı (opsiyonel) |
| `card` | 984 × otomatik | — | ara ürün, şeffaf PNG |

4:5 seçildi çünkü feed'de 1:1'den fazla dikey alan kaplıyor.

### 4.2 Video kısıtları (Instagram)

| Kural | Değer | Sonuç |
|---|---|---|
| Reels sekmesine girmek için oran | 9:16 | dışındaysa normal video postu olur, erişim düşer |
| Reels sekmesine girmek için süre | **5–90 sn** | dışındaysa normal video postu olur |
| Codec | H.264 (High profile, ≤ Level 4.1) | |
| Pixel format | **yuv420p** | verilmezse IG reddedebilir |
| Frame rate | 30 fps | |
| Ses | AAC 128 kbps, 44.1 kHz, stereo | **sessiz videoya bile track eklenmeli** |
| Loudness | -14 LUFS, TP -1.5 dBTP | |
| moov atom | `+faststart` | |
| Dosya boyutu | < 100 MB hedef | |

**Süre normalizasyonu (zorunlu):**
- `< 5 sn` → `-stream_loop` ile ≥ 6 sn'ye tamamla
- `> 90 sn` → 90 sn'ye kes, Telegram bildirimine "kesildi" notu ekle

### 4.3 Instagram API limitleri

- 200 API çağrısı / saat / hesap
- 100 API paylaşımı / 24 saat (kayan pencere)
- `video_url` ve `image_url` **public erişilebilir** olmalı (R2)

---

## 5. Kompozisyon

### 5.1 Reels güvenli alan

Instagram Reels UI'ı frame'in bir kısmını kapatır. İçerik bu bandın dışında kalmalı:

```
y=0    ┌──────────────────────────┐
       │  ⛔ üst UI (140px)        │
y=140  ├──────────────────────────┤
       │                          │
       │   ✅ İÇERİK ALANI         │   1380px
       │   (kart + medya)         │
       │                          │
y=1520 ├──────────────────────────┤
       │  ⛔ alt UI (400px)        │   caption, kullanıcı adı,
y=1920 └──────────────────────────┘   sağdaki aksiyon butonları
```

Kullanılabilir yükseklik: **1380 px**. Yan boşluk: her iki yandan 48 px
(içerik genişliği 984 px).

### 5.2 `layout.py` hesabı

```
Hc = kart PNG yüksekliği (984px genişlikte)
Hv = medya yüksekliği, 984px genişliğe ölçeklendiğinde
GAP = 32

toplam = Hc + GAP + Hv
if toplam > 1380:
    # medyayı küçült, kart sabit kalsın (metin okunabilirliği öncelikli)
    Hv = 1380 - Hc - GAP
    ölçek = Hv / orijinal_yükseklik
    # medya genişliği de aynı oranda düşer, yatayda ortalanır

blok_üst = 140 + (1380 - toplam) / 2      # dikeyde ortala
kart_y   = blok_üst
medya_y  = blok_üst + Hc + GAP
```

Kart 1380px'in %70'ini aşarsa (`Hc > 966`), `card.py` font boyutunu bir kademe
düşürüp yeniden çizer (44 → 38 → 32 → 28 px). 28'de hâlâ sığmıyorsa metin
kırpılır (`…`) ve tam metin caption'a eklenir.

### 5.3 ffmpeg — Reels kompozisyonu

Referans komut. `layout.py` çıktısı `CARD_Y`, `MEDIA_Y`, `MEDIA_W` yerine geçer.

```bash
ffmpeg -y \
  -i source.mp4 \
  -i card.png \
  -f lavfi -t 0.1 -i anullsrc=channel_layout=stereo:sample_rate=44100 \
  -filter_complex "\
    [0:v]scale=1080:1920:force_original_aspect_ratio=increase,\
         crop=1080:1920,gblur=sigma=30,eq=brightness=-0.15,setsar=1[bg]; \
    [0:v]scale=MEDIA_W:-2,setsar=1[fg]; \
    [bg][fg]overlay=(W-w)/2:MEDIA_Y[v1]; \
    [v1][1:v]overlay=(W-w)/2:CARD_Y[vout]; \
    [0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100[aout]" \
  -map "[vout]" -map "[aout]" \
  -c:v libx264 -preset medium -crf 20 \
  -profile:v high -level 4.1 -pix_fmt yuv420p \
  -r 30 -g 60 \
  -c:a aac -b:a 128k -ar 44100 -ac 2 \
  -movflags +faststart \
  -t 90 \
  out_reel.mp4
```

**Ses yoksa** (`ffprobe` ile stream kontrolü): `[0:a]` yerine 3. girdiden
(`anullsrc`) `-shortest` ile beslenir:

```bash
  -map "[vout]" -map 2:a -shortest
```

**Blur arkaplan** kaynak videonun kendisinden üretiliyor — böylece her post kendi
renk paletiyle uyumlu görünüyor ve sabit bir arkaplan görseline gerek kalmıyor.

### 5.4 Statik görsel (Pillow)

1. 1080×1350 tuval
2. Arkaplan: kaynak görsel `cover` şekilde ölçeklenip kırpılır, `GaussianBlur(30)`,
   `%15` karartma
3. Kaynak görsel 984 px genişliğe ölçeklenir, köşeler 24 px yuvarlatılır
4. Kart PNG'si üste yapıştırılır, `layout.py` ile aynı hesap (safe area feed'de daha
   gevşek: üst 60, alt 120)
5. `JPEG quality=92, subsampling=0`, sRGB

**Sadece metin içeren tweet:** medya yok → kart, hafif gradyan arkaplan üzerine
dikeyde ortalanır, 1080×1350.

**Çoklu görsel (2–4):** her biri ayrı `feed` varyantı olarak render edilir ve
IG carousel olarak yayınlanır (P6). Ara çözüm: sadece ilk görsel kullanılır.

### 5.5 Tweet kartı tasarımı

`tweet_card.html.j2` içeriği:

- Avatar (dairesel, 96 px) · Görünen ad (bold) · doğrulama rozeti · `@handle` (gri)
- Metin: 44 px, `line-height: 1.35`, mention/hashtag/link vurgulu renkte
- Alt satır: tarih · `𝕏` logosu
- Kart: `border-radius: 32px`, koyu tema `#15202B`, açık tema `#FFFFFF`,
  hafif gölge, `padding: 48px`
- Alıntı tweet varsa iç içe daha küçük kart

**Fontlar repoda gömülü** (`render/fonts/`), sisteme bağlı değil:
- `Inter` (veya `Noto Sans`) — Türkçe karakterler için tam kapsama
- **`Noto Color Emoji`** — bu olmadan emojiler kutu çıkar

CSS'te:
```css
font-family: "Inter", "Noto Sans", "Noto Color Emoji", sans-serif;
```

Playwright: `device_scale_factor=2`, `omit_background=True`, elementin kendisine
`screenshot()` (tam sayfa değil).

---

## 6. Dedupe

1. **Kesin eşleşme:** `UNIQUE(source_platform, source_id)` — aynı tweet iki kez girmez.
2. **Algısal eşleşme:** `imagehash.phash()` — video için 1. saniyedeki kare, görsel
   için görselin kendisi. Yeni item'ın hash'i mevcut item'lardan herhangi biriyle
   **Hamming mesafesi ≤ 6** ise `rejected`, `dupe_of_item_id` doldurulur.
3. Telegram'a "bunu zaten paylaşmıştın (link)" bildirimi gider.

pHash sorgusu için son 2000 item yeterli — tam tablo taraması gerekmez.

---

## 7. AI katmanı

Model: `claude-haiku-4-5-20251001` (env: `AI_MODEL`). İki çağrı, ikisi de
structured output (JSON) döner.

### 7.1 İçerik filtresi (`screen/ai_gate.py`)

Girdi: tweet metni + görsel/anahtar kare (vision).

```
Sen bir Türkçe mizah/meme Instagram sayfasının editör asistanısın.
Verilen içeriği değerlendir ve SADECE JSON döndür:

{
  "funny_score": 0-100,          // hedef kitle için komiklik
  "in_niche": true|false,        // genel mizah/absürt/günlük hayat mı
  "language": "tr"|"en"|"other"|"none",
  "flags": [],                    // aşağıdakilerden geçerli olanlar
  "watermark_handle": null|"@x",  // görselde başka bir sayfanın etiketi varsa
  "ocr_text": "",                 // görselde okunan metin
  "reason": ""                    // reddedilecekse tek cümle gerekçe
}

Geçerli flag değerleri:
hate, violence, gore, nudity, sexual, politics, ad, watermark,
minor_safety, self_harm, low_quality

Kurallar:
- Görselde başka bir meme sayfasının kullanıcı adı/logosu varsa "watermark" ekle.
- Siyasi figür veya parti içeren mizah için "politics" ekle.
- Metin okunamıyorsa veya görüntü çok bozuksa "low_quality" ekle.
```

**Reddetme kuralı (kodda, modelde değil):**
```python
BLOCKING_FLAGS = {"hate","violence","gore","nudity","sexual",
                  "minor_safety","self_harm","watermark","ad"}
reject = bool(flags & BLOCKING_FLAGS) or funny_score < AI_MIN_SCORE  # varsayılan 45
```
`politics` ve `low_quality` bloklamaz ama Telegram bildiriminde uyarı olarak gösterilir.

### 7.2 Caption yazarı (`caption/writer.py`)

```
Türkçe bir mizah Instagram sayfası için caption yaz. SADECE JSON döndür:

{
  "caption": "",       // 1-2 satır, samimi, emoji ölçülü, tırnak/başlık yok
  "hashtags": []       // 8-15 adet, karışık TR/EN, '#' dahil
}

İçerik: {text}
Görselde okunan metin: {ocr_text}

Kurallar:
- Tweet metnini aynen tekrarlama; üzerine bir yorum/tepki yaz.
- Clickbait yok, "beğen ve kaydet" gibi kalıplar yok.
- Hashtag'ler içerikle gerçekten ilgili olsun; jenerik #keşfet spam'i yapma.
```

Kredi satırı **kod tarafından** eklenir, modele bırakılmaz:

```python
final_caption = f"{ai_caption}\n\n📍 @{author_handle} via {platform_label}\n\n{' '.join(hashtags)}"
```

### 7.3 Maliyet

Item başına ~2–4k input token (görsel dahil) + ~300 output token.
Günde 10 item → ayda **$0.30–1** civarı.

---

## 8. Telegram UX

### Girdi
- Metin mesajı içinde link → `queued`
- Doğrudan foto/video → `manual` platform, `fetched` durumunda başlar
- `/status` → kuyruk özeti
- `/stats` → son 7 gün: alınan / reddedilen / paylaşılan
- `/block @handle` → blocklist'e ekle

### Önizleme mesajı

```
🎬 Reels hazır  ·  @kullanici  ·  komiklik 78/100

<caption metni burada>

⚠️ 90 sn'ye kesildi
```
+ video/foto eki + inline klavye:

```
[ ✅ Onayla ]  [ ✏️ Caption ]
[ 🔁 Yeniden çiz ]  [ ❌ Sil ]
```

- **✅ Onayla** → `approved`; `AUTO_PUBLISH=true` ise slota yerleşir, değilse
  "hazır, indirip paylaşabilirsin" olarak kalır
- **✏️ Caption** → bot cevap bekler, gelen metin caption olur
- **🔁 Yeniden çiz** → temayı değiştirip (dark↔light) yeniden render
- **❌ Sil** → `rejected`, dosyalar silinir

Hata durumunda da mesaj gider: hangi aşama, hangi hata, tekrar denenecek mi.

---

## 9. Zamanlama

```python
PUBLISH_SLOTS = ["12:30", "18:30", "21:30"]   # Europe/Istanbul
MAX_PER_DAY = 3
MIN_GAP_MINUTES = 180
```

`schedule/planner.py`: `approved` item'ı bir sonraki boş slota yazar. Slotlar
doluysa ertesi güne taşar. `publish` job'ı slot saatlerinde çalışır ve
`scheduled_for <= now` olan ilk item'ı yayınlar.

---

## 10. Konfigürasyon (env)

```bash
# --- Temel ---
ENV=dev                                # dev | prod
DATABASE_URL=sqlite:///./data/smauto.db
DATA_DIR=./data
LOG_LEVEL=INFO

# --- Telegram (zorunlu) ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=123456789    # virgülle ayrılmış; başkası kullanamaz
TELEGRAM_TARGET_CHAT_ID=123456789

# --- AI ---
ANTHROPIC_API_KEY=
AI_MODEL=claude-haiku-4-5-20251001
AI_ENABLED=true
AI_MIN_SCORE=45

# --- Render ---
CARD_THEME=dark                        # dark | light
RENDER_REEL=true
RENDER_FEED=true
RENDER_STORY=false

# --- Teslim ---
DELIVERY_MODE=telegram                 # telegram | instagram
AUTO_PUBLISH=false

# --- Instagram (P5) ---
IG_USER_ID=
IG_ACCESS_TOKEN=
IG_APP_ID=
IG_APP_SECRET=

# --- Depolama (P5) ---
STORAGE_BACKEND=local                  # local | r2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
R2_PUBLIC_BASE_URL=

# --- Zamanlama ---
TIMEZONE=Europe/Istanbul
PUBLISH_SLOTS=12:30,18:30,21:30
MAX_PER_DAY=3

# --- Dayanıklılık ---
MAX_ATTEMPTS=3
RESOLVER_CHAIN=fxtwitter,ytdlp,gallerydl,manual
HTTP_TIMEOUT_SECONDS=30

# --- X API (P6, opsiyonel) ---
X_API_ENABLED=false
X_BEARER_TOKEN=
X_OAUTH_CLIENT_ID=
X_OAUTH_CLIENT_SECRET=
X_REFRESH_TOKEN=
X_POLL_SOURCE=likes                    # likes | bookmarks
```

`config.py` bunları pydantic ile doğrular; `DELIVERY_MODE=instagram` iken
`IG_*` ve `R2_*` boşsa **başlangıçta** hata verir (çalışma anında değil).

---

## 11. Test stratejisi

| Katman | Yaklaşım |
|---|---|
| Resolver'lar | `tests/fixtures/` içindeki kayıtlı JSON yanıtları, ağ yok |
| Layout | saf fonksiyon, sınır durumları (çok uzun kart, çok geniş medya) |
| Kart render | Playwright ile üret, boyut + non-boş piksel kontrolü; emoji tofu regresyon testi |
| Video | 2 sn'lik örnek mp4, `ffprobe` ile çıktı doğrulama: 1080×1920, yuv420p, 30 fps, ses stream'i var, süre 5–90 |
| Dedupe | aynı görselin yeniden boyutlandırılmış/JPEG'lenmiş hali → duplicate saymalı |
| AI | yanıtlar mock'lanır; kod sadece flag mantığını test eder |
| IG publish | `responses`/`respx` ile HTTP mock; container poll döngüsü ve ERROR yolu |
| Durum makinesi | geçersiz geçişlerin reddedildiği testler |

`ffprobe` doğrulaması en kritik test — Instagram'ın sessizce reddettiği çıktıları
yakalayan tek şey.

---

## 12. `doctor` komutu

`smauto doctor` şunları kontrol eder ve tablo basar:

- [ ] `ffmpeg` / `ffprobe` PATH'te, sürüm ≥ 6
- [ ] Playwright Chromium kurulu, açılıyor
- [ ] `render/fonts/` içinde Inter + Noto Color Emoji var
- [ ] Emoji render testi (küçük PNG üret, tofu kontrolü)
- [ ] DB erişilebilir, migration'lar güncel
- [ ] `TELEGRAM_BOT_TOKEN` geçerli (`getMe`)
- [ ] `ANTHROPIC_API_KEY` geçerli
- [ ] `DELIVERY_MODE=instagram` ise: IG token geçerli mi, **kaç gün kaldı**
- [ ] `STORAGE_BACKEND=r2` ise: bucket'a yazma + public URL okuma testi
- [ ] `.env` git'e eklenmiş mi (sızıntı kontrolü)
