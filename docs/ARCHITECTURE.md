# Mimari

Bu doküman **kararları** içerir. Alternatifler "elenenler" başlığı altında, neden
elendikleriyle birlikte duruyor — ama karar verilmiş kabul edilir.

---

## 1. Uçtan uca akış

```
┌─────────────────────────────────────────────────────────────────┐
│  SEN                                                            │
│  X / Instagram / TikTok uygulamasında komik bir şey görürsün    │
│  Paylaş → Telegram → kendi botun                                │
└───────────────────────────┬─────────────────────────────────────┘
                            │  (link veya doğrudan video/foto dosyası)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  INTAKE          getUpdates ile mesajları çeker, kuyruğa yazar   │
│                  status: queued                                  │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  RESOLVE         metin + medya + yazar bilgisini çıkarır         │
│                  4 katmanlı fallback (§3)                        │
│                  status: fetched                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  SCREEN          pHash dedupe → AI içerik filtresi → blocklist   │
│                  status: screened | rejected                     │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  RENDER          1. Playwright ile tweet kartı PNG               │
│                  2. ffmpeg/Pillow ile kompozisyon                │
│                     → reel.mp4 (1080x1920)                       │
│                     → feed.jpg (1080x1350)                       │
│                  status: rendered                                │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  CAPTION         Claude Haiku → TR caption + hashtag + kredi     │
└───────────────────────────┬─────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  DELIVER         Telegram'a önizleme + caption + inline butonlar │
│                  [✅ Onayla] [✏️ Caption] [🔁 Yeniden çiz] [❌ Sil]│
│                  status: awaiting_approval                       │
└───────────────────────────┬─────────────────────────────────────┘
                 ┌──────────┴──────────┐
                 ▼                     ▼
      AUTO_PUBLISH=false        AUTO_PUBLISH=true
      sen indirir paylaşırsın   ┌──────────────────────────────────┐
      (P3'te burada biter)      │  SCHEDULE  slota yerleştir       │
                                │  PUBLISH   R2 → IG container →   │
                                │            media_publish         │
                                │  status: published               │
                                └──────────────────────────────────┘
```

**Kritik nokta:** P3 sonunda sistem zaten işe yarıyor. Instagram API entegrasyonu
(P5) olmadan da her sabah telefonunda paylaşıma hazır Reels dosyaları oluyor.
Meta app review, token, R2 — hiçbiri P3 için gerekmiyor. Bu, projenin en erken
değer üreten noktası ve fazlama buna göre yapıldı.

---

## 2. Karar: Tetikleyici neden Telegram?

Sen "beğenince otomatik olsun" istedin. Değerlendirilenler:

| Seçenek | Maliyet | Güvenilirlik | Kurulum | Karar |
|---|---|---|---|---|
| **Telegram'a paylaş** | $0 | Yüksek | 5 dk | ✅ **Varsayılan** |
| X API `liked_tweets` polling | ~$1–5/ay | Orta | OAuth2 + kredi | ⚙️ Opsiyonel (P6) |
| X API `bookmarks` polling | ~$1–5/ay | Orta | OAuth2 + kredi | ⚙️ Opsiyonel (P6) |
| Tarayıcı eklentisi / bookmarklet | $0 | Yüksek (sadece masaüstü) | orta | ⚙️ Opsiyonel (P6) |
| x.com scraping (kendi beğenilerin) | $0 | Düşük | — | ❌ Elendi |

**Neden Telegram varsayılan:**

1. **Tek tetikleyici, üç platform.** X, Instagram ve TikTok'un native paylaş menüsünde
   Telegram var. Beğeni polling'i sadece X'i çözer; sen üçünden de içerik istiyorsun.
2. **Onay kanalı bedava geliyor.** Aynı sohbet hem giriş hem önizleme hem onay hem
   teslim yeri. Ayrı bir panel yazmaya gerek kalmıyor (bkz. `REVIEW.md` C1).
3. **Manuel yükleme aynı kanaldan.** Bot medyayı çözemezse "dosyayı at" der, sen
   videoyu doğrudan sohbete düşürürsün, pipeline devam eder. Beğeni polling'inde
   böyle bir kaçış yolu yok.
4. **Kimlik doğrulama derdi yok.** OAuth2 refresh token bakımı, kredi bakiyesi,
   X'in politika değişikliği — hiçbiri seni etkilemiyor.

**Maliyet farkı "bir tık"dan ibaret:** beğenmek 1 dokunuş, paylaş→Telegram 3 dokunuş.
Buna karşılık kurulum karmaşıklığı ve kırılganlık ciddi biçimde düşüyor.

**X likes polling yine de P6'da var** çünkü Şubat 2026 fiyatlandırmasıyla artık pahalı
değil. `SourceAdapter` arayüzü baştan bunu kaldıracak şekilde tasarlandı — sonradan
eklemek bir dosya yazmak demek, mimariyi değiştirmek değil.

### Elenen: x.com scraping
Kendi beğenilerini scrape etmek oturum çerezi gerektirir, çerezler günler içinde
bozulur, X ToS'una aykırı ve hesabın askıya alınma riski var. Bu projede hesabın
kendisi ürün — riske atılmaz.

---

## 3. Karar: Medya çözümleme — 4 katmanlı fallback

Her katman başarısız olursa bir sonrakine düşer. İlk başarılı olan kazanır.

```
1. fxtwitter / vxtwitter JSON API      ← ücretsiz, auth yok, hızlı
   api.fxtwitter.com/status/{id}
   döndürür: metin, yazar, avatar, medya URL'leri, quote tweet
        │ başarısız
        ▼
2. yt-dlp (video) / gallery-dl (görsel) ← ücretsiz, çerez gerekebilir
   --dump-single-json ile metadata + indirme
        │ başarısız
        ▼
3. X API v2 tweet lookup                ← $0.005/post, opsiyonel
   sadece X_API_ENABLED=true ise
        │ başarısız
        ▼
4. MANUEL                               ← her zaman çalışır
   bot: "medyayı indiremedim, dosyayı buraya at"
   kullanıcı Telegram'a dosyayı düşürür
```

**Neden zincir:** X'in erişim politikası öngörülemez şekilde değişiyor. Tek kaynağa
bağlı bir pipeline kaçınılmaz olarak bir gün duruyor. 4. katman sayesinde sistem
**hiçbir zaman tamamen çalışmaz hale gelmiyor** — en kötü ihtimalle bir ek dokunuş
istiyor.

> ⚠️ **Build sırasında doğrula:** fxtwitter/vxtwitter topluluk tarafından işletilen
> servisler; bu ortamdan erişim test edilemedi. P1'in ilk işi bu endpoint'lerin canlı
> olduğunu doğrulamak. Değilse katman 2 birincil olur, mimari değişmez.

Instagram ve TikTok linkleri için yt-dlp birincil (her ikisini de destekliyor;
Instagram çerez isteyebilir).

---

## 4. Karar: Render — Playwright kart + doğrudan ffmpeg

Tweet metnini görüntüye basmanın iki yolu var:

| Yaklaşım | Karar |
|---|---|
| x.com sayfasının gerçek ekran görüntüsü | ❌ Login çerezi gerekir, layout değişir, banner/consent çıkar, tutarsız |
| **Kendi HTML/CSS kartını Playwright ile render et** | ✅ |

Kendi kartımızı çizmek: login yok, %100 deterministik, tema kontrolü bizde
(koyu/açık), Türkçe + emoji garantili, quote tweet iç içe çizilebiliyor, uzun metin
için otomatik font küçültme mümkün.

Kompozisyon **doğrudan ffmpeg** ile, tek `filter_complex` çağrısında. MoviePy elendi
(bkz. `REVIEW.md` C2). Statik görseller Pillow ile (ffmpeg'e göre daha basit).

Somut layout, safe area hesabı ve tam ffmpeg komutları: `SPEC.md` §5.

---

## 5. Karar: Yayınlama — iki mod, tek kod yolu

```python
DELIVERY_MODE = "telegram"   # P3 — Meta kurulumu gerektirmez
DELIVERY_MODE = "instagram"  # P5 — otomatik paylaşım
```

**Instagram tarafı (P5):**

- **Instagram API with Instagram Login** kullanılır — Facebook Page **gerekmiyor**.
  Sadece Instagram Professional (Business veya Creator) hesabı + Meta app.
- App **Development mode**'da kalır, kendi IG hesabın *tester* olarak eklenir.
  Böylece **App Review gerekmez**. (App Review sadece başkalarının hesaplarına
  hizmet veriyorsan gerekiyor — biz vermiyoruz.)
- Yayın 3 adım: `POST /{ig-user-id}/media` (container) → `status_code == FINISHED`
  olana kadar poll → `POST /{ig-user-id}/media_publish`.
- `video_url` **public erişilebilir** olmak zorunda → Cloudflare R2.
- Long-lived token **60 gün** geçerli → haftalık refresh job (§7).
- Limitler: 200 çağrı/saat/hesap, 100 paylaşım/24 saat. Bizim hacmimizde sorun yok.

**Zamanlama:** onaylanan içerik hemen paylaşılmaz. `scheduled_for` alanına günün
sabit slotlarından biri yazılır (varsayılan 12:30 / 18:30 / 21:30 TRT). Ayrı bir
publish job'ı zamanı gelenleri basar. Gerekçe: art arda paylaşım hem erişimi düşürüyor
hem bot sinyali veriyor.

---

## 6. Karar: Teknoloji yığını

| Katman | Seçim | Gerekçe |
|---|---|---|
| Dil | **Python 3.12** | ffmpeg/Pillow/imagehash/yt-dlp/gallery-dl/Playwright hepsi birinci sınıf |
| Paket yönetimi | **uv** | hızlı, lock dosyalı, tek binary |
| Config | **pydantic-settings** | env doğrulaması, tip güvenliği |
| DB | **SQLAlchemy 2.x + Alembic** | SQLite (yerel) ↔ Postgres (bulut) tek kod |
| Bot | **python-telegram-bot v21+** | inline buton UX'i hazır geliyor |
| Kart | **Playwright (Chromium) + Jinja2** | deterministik HTML→PNG |
| Video | **ffmpeg** (subprocess) | hızlı, deterministik |
| Görsel | **Pillow** + **imagehash** | kompozisyon + pHash |
| AI | **anthropic** SDK, `claude-haiku-4-5-20251001` | caption + içerik filtresi, çok ucuz |
| Depolama | **Cloudflare R2** (S3 uyumlu, boto3) | 10 GB ücretsiz, egress ücretsiz |
| Test/lint | pytest, ruff, mypy | — |
| Deploy | **GitHub Actions cron** (varsayılan), Docker Compose (alternatif) | bkz. §7 |

**AI model notu:** caption ve içerik filtresi için Haiku 4.5 fazlasıyla yeterli ve
maliyeti ihmal edilebilir. Caption kalitesi tatmin etmezse `claude-sonnet-5`'e geçmek
tek satırlık config değişikliği — `AI_MODEL` env değişkeni.

---

## 7. Karar: Deploy — deployment-agnostik tasarım

Kod **tek bir CLI** olarak yazılır:

```bash
smauto ingest          # Telegram'dan yeni mesajları çek
smauto process         # kuyruktaki işleri resolve → screen → render → caption → deliver
smauto publish         # zamanı gelen onaylıları Instagram'a bas
smauto refresh-tokens  # IG long-lived token yenile
smauto doctor          # ffmpeg / font / playwright / token / bağlantı sağlık kontrolü
```

Bu sayede iki deploy hedefi aynı koddan çalışır:

**A) GitHub Actions (varsayılan, $0)**
- `worker.yml` — cron, `ingest` + `process`
- `publish.yml` — cron, slot saatlerinde `publish`
- `publish.yml` ayrıca haftalık `refresh-tokens` çalıştırır
- State: Turso veya Supabase (ücretsiz), medya: R2
- ⚠️ Private repoda ayda 2000 dakika ücretsiz ve **her job 1 dakikaya yuvarlanıyor**.
  Karar: **repoyu public yap** → sınırsız dakika. Private kalacaksa worker periyodu
  ≥30 dk olmalı. Hesap: `SETUP.md` §4.

**B) VPS (Hetzner CX22 ~€3.79/ay)**
- Docker Compose, aynı CLI, cron yerine systemd timer veya bot webhook modu
- Gecikme 10 dakikadan saniyelere iner
- Actions'ın gecikme/kota derdi yok

`doctor` komutu her iki ortamda da kurulumu doğrular — bu tür projelerde en çok
zaman kaybettiren şey "neden çalışmıyor" olduğu için baştan var.

---

## 8. Karar: Güvenlik ve telif kuralları koda gömülü

Bunlar dokümanda öğüt değil, `screen` aşamasında kod:

- **Kredi zorunlu.** Caption'a her zaman `@handle via X` satırı eklenir. Kapatılamaz.
- **Özel hesaplar işlenmez.** Resolver protected/private sinyali görürse `rejected`.
- **Watermark filtresi.** AI filtresi başka bir meme sayfasının kullanıcı adı/logosu
  görünüyorsa bloklar — hem telif hem "çalıntı sayfa" algısı riski.
- **İçerik filtresi.** Nefret söylemi, şiddet/gore, çıplaklık, siyasi içerik, reklam
  → `rejected`, gerekçesiyle birlikte Telegram'a bildirilir.
- **Blocklist.** Yazar, alan adı veya pHash bazlı kalıcı engelleme. Kaldırma talebi
  geldiğinde yazar buraya eklenir ve geçmiş paylaşımlar `unpublish` ile silinir.
- **Secret'lar repoda değil.** `.env` gitignore'da, GitHub Secrets veya VPS'te
  `.env` dosyası. `doctor` sızıntı kontrolü yapar.
- **İlk fazlarda `AUTO_PUBLISH=false`.** Tam otomatik moda ancak filtreye güvendikten
  sonra geçilir.

---

## 9. Elenen alternatifler (özet)

| Alternatif | Neden elendi |
|---|---|
| Ayrı web review paneli | Telegram butonları aynı işi 1/20 eforla yapıyor |
| MoviePy | ffmpeg'i sarmalıyor, yavaş, bellek yiyor |
| Node.js + TypeScript | medya/scraping ekosistemi Python'da daha güçlü |
| FastAPI webhook (ilk faz) | polling yeterli, public URL + TLS derdi yok |
| SQLite dosyasını repoya commit'lemek | yarış durumu, kirli git geçmişi |
| n8n / Make / Zapier | medya kompozisyonu yapamıyor, ücretli seviyeye zorluyor |
| x.com scraping | ToS ihlali, hesap askıya alma riski, çerez kırılganlığı |
| Facebook Page + Business Manager | Instagram Login API ile artık gereksiz |
