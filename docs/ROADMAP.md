# Yol Haritası — Implementasyon Ticket'ları

> **Durum: P0–P5 tamamlandı.** Kod `src/smauto/` altında, 140 test geçiyor.
> Bu doküman ne yapıldığının kaydı ve P6 için sıradaki iş listesi olarak duruyor.
> Uygulama sırasında plandan bilinçli sapmalar için `SPEC.md` §0.

Kabul kriterleri **doğrulanabilir** yazıldı — "çalışıyor" değil, "şu komut şunu üretir".

Detaylar için `SPEC.md`'nin ilgili bölümüne bak.

---

## ✅ P0 — İskelet

> Hedef: `smauto doctor` yeşil dönüyor, CI geçiyor. Henüz iş yapmıyor.

### P0-1 · Proje iskeleti
- `uv` ile `pyproject.toml`, Python 3.12, `src/smauto/` layout
- `ruff` + `mypy` (strict) + `pytest` konfigürasyonu
- `.gitignore` (`.env`, `data/`, `*.mp4`, `*.png`, `.venv`)
- `.env.example` — `SPEC.md` §10'daki tüm değişkenler, açıklamalı

**Kabul:** `uv sync && uv run ruff check && uv run mypy src && uv run pytest` hatasız.

### P0-2 · Config + logging
- `config.py`: pydantic-settings, `SPEC.md` §10'daki tüm alanlar
- `DELIVERY_MODE=instagram` iken `IG_*`/`R2_*` boşsa **başlangıçta** `ValidationError`
- `logging.py`: structlog, JSON çıktı, her log satırında `item_id`

**Kabul:** eksik zorunlu env ile import → anlaşılır hata; test bunu doğruluyor.

### P0-3 · Veri modeli + migration
- `SPEC.md` §2'deki 5 tablo, SQLAlchemy 2.x tipli modeller
- Alembic ilk migration
- Durum makinesi `transition(item, new_status)` helper'ı — geçersiz geçiş `ValueError`
- Her geçişte `events` kaydı

**Kabul:** SQLite ve Postgres'te migration çalışıyor; geçersiz geçiş testi kırmızıdan yeşile.

### P0-4 · CLI + `doctor`
- typer: `ingest`, `process`, `publish`, `refresh-tokens`, `doctor`
- `doctor` → `SPEC.md` §12'deki kontrol listesi, renkli tablo, hata varsa exit 1

**Kabul:** `smauto doctor` tabloyu basıyor; ffmpeg PATH'ten kaldırılınca exit 1.

### P0-5 · CI
- GitHub Actions: ruff + mypy + pytest, Python 3.12
- ffmpeg ve Playwright Chromium kurulumu (cache'li)

**Kabul:** PR'da CI yeşil.

---

## ✅ P1 — Intake + medya çözümleme

> Hedef: Telegram'a link atınca DB'ye medya + metin düşüyor.

### P1-0 · fxtwitter doğrulaması ⚠️ ÖNCE BU
`api.fxtwitter.com/status/{id}` ve `api.vxtwitter.com/i/status/{id}` canlı mı,
hangi alanları döndürüyor, rate limit var mı — elle test et, bulguları
`docs/notes/resolver-probe.md`'ye yaz.

**Sonuca göre:** canlıysa zincirin 1. katmanı olarak kalır. Değilse `ytdlp`
birincil olur, `RESOLVER_CHAIN` varsayılanı güncellenir. **Mimari değişmez.**

**Kabul:** not dosyası, gerçek yanıt örnekleriyle birlikte commit'lendi.

### P1-1 · Telegram intake
- `python-telegram-bot`, `getUpdates` (long polling, offset DB'de saklanır)
- Sadece `TELEGRAM_ALLOWED_USER_IDS` içindeki kullanıcılar
- Mesajdan URL çıkarımı; x.com/twitter.com/instagram.com/tiktok.com tanınır
- Doğrudan foto/video → `platform=manual`, `status=fetched`
- `/status`, `/stats`, `/block @handle` komutları

**Kabul:** `smauto ingest` çalıştırılınca bota atılan link `items` tablosunda
`queued` olarak beliriyor; yetkisiz kullanıcının mesajı yok sayılıyor.

### P1-2 · Resolver zinciri
- `ResolvedPost` / `ResolvedMedia` dataclass'ları (`SPEC.md` §3.1)
- `chain.py`: `RESOLVER_CHAIN` sırasıyla dener, her denemeyi `events`'e yazar
- Ortak metin temizleme (t.co kaldırma, entity decode)
- `is_protected=True` → anında `rejected`

**Kabul:** fixture'lardan 3 farklı tweet tipi (tek görsel, video, sadece metin)
doğru `ResolvedPost` üretiyor; 1. resolver patlatıldığında 2.'ye düşüyor.

### P1-3 · fxtwitter resolver
**Kabul:** kayıtlı JSON fixture'dan metin, yazar, avatar, medya URL'leri çıkıyor;
alıntı tweet varsa `quoted` doluyor.

### P1-4 · yt-dlp + gallery-dl resolver
- `--dump-single-json` ile metadata, ayrı adımda indirme
- `COOKIES_FILE` desteği (opsiyonel, `SETUP.md`'de anlatılıyor)
- Çerez hatası ayırt edilip `events`'e "çerez yenilenmeli" olarak yazılır

**Kabul:** yerel örnek dosyayla indirme yolu test ediliyor; çerez hatası mesajı
Telegram'a düşüyor.

### P1-5 · Manuel fallback
- Zincir tükenince `needs_manual`, bota "dosyayı at" mesajı
- Kullanıcının yolladığı dosya bekleyen item'a bağlanır (reply veya son bekleyen)

**Kabul:** çözümlenemeyen link için bot dosya istiyor; dosya atılınca item
`fetched`'e geçiyor.

### P1-6 · Retry + backoff
- `MAX_ATTEMPTS`, gecikme `1m, 5m, 25m`
- Tükenince `failed` + Telegram bildirimi

**Kabul:** kalıcı hata veren resolver 3 denemeden sonra duruyor, bildirim gidiyor.

---

## ✅ P2 — Render

> Hedef: `smauto process` sonunda diskte 1080×1920 mp4 ve/veya 1080×1350 jpg var.
> **Projenin en yüksek değerli fazı — kalite burada belirleniyor.**

### P2-1 · Fontlar
- `Inter` (veya Noto Sans) + **`Noto Color Emoji`** `render/fonts/` içine, lisanslarıyla
- `doctor`'a emoji render testi eklenir (üret → tofu kontrolü)

**Kabul:** `🤣🇹🇷ğşıçöü` içeren kart PNG'sinde tofu yok — test bunu piksel bazında doğruluyor.

### P2-2 · Tweet kartı
- `tweet_card.html.j2` + `card.css` (`SPEC.md` §5.5)
- Playwright, `device_scale_factor=2`, `omit_background=True`, element screenshot
- Koyu/açık tema, doğrulama rozeti, alıntı tweet
- Otomatik font küçültme 44→38→32→28, sonra kırpma

**Kabul:** aynı girdi iki kez çalıştırıldığında byte-identical PNG (deterministik);
1500 karakterlik tweet 966 px'i aşmıyor.

### P2-3 · Layout hesabı
- `layout.py`, saf fonksiyon, `SPEC.md` §5.2

**Kabul:** sınır durum testleri geçiyor — kart tek başına alanı doldurduğunda medya
küçülüyor, hiçbir durumda `y < 140` veya `y+h > 1520` çıkmıyor.

### P2-4 · Video kompozisyonu
- `SPEC.md` §5.3'teki ffmpeg komutu, tek `filter_complex`
- Sessiz video tespiti (`ffprobe`) → `anullsrc` track
- Süre normalizasyonu: `<5s` loop, `>90s` kes + bayrak

**Kabul:** `ffprobe` çıktısı doğruluyor — 1080×1920, `yuv420p`, `high` profile,
30 fps, AAC stereo 44.1 kHz, süre 5–90 sn, `moov` başta. Sessiz kaynak videoda
bile ses stream'i var.

### P2-5 · Görsel kompozisyonu
- Pillow, 1080×1350, blur arkaplan, yuvarlatılmış köşe, kart
- Sadece metin içeren tweet → gradyan arkaplan

**Kabul:** üç senaryo (tek görsel / sadece metin / çoklu görselden ilki) çıktı üretiyor,
boyut ve mod (`RGB`, sRGB) doğru.

---

## ✅ P3 — Telegram teslimi

> 🎯 **Bu fazın sonunda sistem uçtan uca kullanılabilir.**
> Meta hesabı, R2, token — hiçbiri gerekmiyor.

### P3-1 · Önizleme + inline butonlar
- `SPEC.md` §8'deki mesaj formatı ve klavye
- ✅ Onayla / ✏️ Caption / 🔁 Yeniden çiz / ❌ Sil callback'leri
- Mesaj düzenleme için `telegram_message_id` saklanır

**Kabul:** render biten item Telegram'a video + caption + 4 butonla düşüyor;
her buton doğru durum geçişini yapıyor; ✏️ ile gönderilen metin caption'ı
güncelleyip mesajı yeniden düzenliyor.

### P3-2 · Hata bildirimleri
Her `failed` ve `rejected` için: hangi aşama, gerekçe, tekrar denenecek mi.

**Kabul:** resolver çökmesi ve AI reddi için ayrı ayrı okunabilir mesaj gidiyor.

### P3-3 · Actions worker
- `.github/workflows/worker.yml` — cron, `ingest` + `process`
- Concurrency group (üst üste çalışmayı engelle)
- Secret'lar GitHub Secrets'tan
- Periyot kararı: `SETUP.md` §4 (public repo → 10 dk, private → 30 dk)

**Kabul:** manuel `workflow_dispatch` ile tetiklenince bota atılmış link
işlenip Telegram'a düşüyor.

---

## ✅ P4 — AI katmanı + dedupe

### P4-1 · pHash dedupe
- `imagehash.phash`, video için 1. saniye karesi
- Hamming ≤ 6 → `rejected` + `dupe_of_item_id`
- Son 2000 item ile karşılaştırma

**Kabul:** aynı görselin %70 boyuta küçültülmüş + JPEG q60 hali duplicate sayılıyor;
alakasız görsel sayılmıyor.

### P4-2 · AI içerik filtresi
- `SPEC.md` §7.1 prompt'u, vision girdisi, structured JSON
- Bloklama kararı **kodda** (`BLOCKING_FLAGS`), modelde değil
- `AI_ENABLED=false` ile tamamen atlanabilir

**Kabul:** mock yanıtlarla: `hate` flag'i → reject; `politics` → geçer ama uyarı;
`funny_score=30` → reject.

### P4-3 · Caption yazarı
- `SPEC.md` §7.2, kredi satırı kod tarafından ekleniyor

**Kabul:** kredi satırı ve hashtag'ler her caption'da var; model kredi satırını
üretmese bile ekleniyor.

### P4-4 · Blocklist
- `/block @handle`, `blocklist` tablosu, `screen` aşamasında kontrol

**Kabul:** bloklu yazarın yeni içeriği `rejected` oluyor.

---

## ✅ P5 — Instagram otomatik paylaşım

> Bundan öncesi olmadan buraya girilmez. `AUTO_PUBLISH` varsayılan `false` kalır.

### P5-1 · R2 storage
- `Storage` protokolü, `r2.py` (boto3, S3 uyumlu)
- 30 günlük lifecycle silme kuralı (dokümante et)
- Anahtar şeması: `{item_id}/{variant}.{ext}`

**Kabul:** `doctor` R2'ye yazıp public URL'den okuyabiliyor.

### P5-2 · IG publish
- `SPEC.md` §3.4: container → poll (`status_code`) → `media_publish`
- `ERROR`/`EXPIRED` yolları, 5 dk timeout
- `ig_media_id` + `ig_permalink` kaydedilir

**Kabul:** mock HTTP ile üç yol da test ediliyor (FINISHED / ERROR / timeout);
gerçek bir test paylaşımı elle doğrulanıp `events`'e yazılıyor.

### P5-3 · Token yenileme
- `refresh-tokens` komutu, `ig_refresh_token` akışı
- haftalık cron (`publish.yml` içinde)
- Token < 14 gün kaldıysa Telegram uyarısı

**Kabul:** `doctor` kalan gün sayısını gösteriyor; yenileme sonrası `expires_at` ileriye kayıyor.

### P5-4 · Zamanlayıcı
- `schedule/planner.py`, `PUBLISH_SLOTS`, `MAX_PER_DAY`
- `publish.yml` slot saatlerinde çalışır

**Kabul:** 5 onaylı item 3 slotlu güne dağıtılınca 3'ü bugüne, 2'si yarına yazılıyor.

### P5-5 · Kaldırma akışı
- `/unpublish <item_id>` → IG'den sil, `blocklist`'e yazar ekle

**Kabul:** komut çalışıyor, item durumu güncelleniyor.

---

## P6 — Opsiyoneller

Öncelik sırası yok, ihtiyaca göre.

| # | İş | Not |
|---|---|---|
| P6-1 | **X likes/bookmarks polling** | `SourceAdapter`, OAuth2 refresh, `X_POLL_SOURCE`. Kredi tüketimi `events`'e loglanır |
| P6-2 | IG carousel (çoklu görsel) | `is_carousel_item` + `CAROUSEL` container |
| P6-3 | Story çapraz paylaşım | `media_type=STORIES` |
| P6-4 | Bookmarklet / tarayıcı eklentisi | masaüstü intake |
| P6-5 | Analitik | IG Insights ile erişim/etkileşim çekip `funny_score` ile ilişkilendir |
| P6-6 | TikTok'a paylaşım | Content Posting API, aynı render pipeline'ı |
| P6-7 | VPS deploy | Docker Compose + systemd timer, webhook modu |

---

## Katkı notları

- **Her değişiklik tek PR.** Kabul kriterini karşılayan test olmadan açma.
- **`SPEC.md` sözleşmedir.** Sapman gerekirse önce `SPEC.md`'yi güncelle (§0'a
  satır ekle), sonra kodu yaz.
- **Ağ çağrısı olan hiçbir şeyi test içinde gerçekten çağırma** — `tests/fixtures/`
  veya `respx`/`MockTransport` kullan.
- **Video değişikliklerinin kabulü `ffprobe` çıktısı üzerinden.** Gözle "iyi
  görünüyor" yeterli değil; Instagram'ın reddettiği çoğu video gözle sorunsuz.
- **Türkçe karakter ve emoji regresyonu** `test_render_real.py` içinde korunuyor;
  kart şablonuna dokunursan o testi çalıştır.
- Kod içi tanımlayıcılar ve commit mesajları İngilizce; dokümanlar Türkçe.
