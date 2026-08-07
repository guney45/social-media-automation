# social-media-automation

X (Twitter), Instagram ve TikTok'ta beğendiğim komik içerikleri toplayıp, Instagram'da
paylaşıma hazır **Reels / feed görseli** olarak üreten otomasyon.

```
Telefonda paylaş → Telegram botu → medyayı indirir → tweet kartını çizer →
1080x1920 Reels'e derler → caption yazar → onaya gönderir → Instagram'a basar
```

İçerik seçimi dışındaki her adım otomatik. Son adım iki modda çalışır:
**otomatik** (Instagram Graph API) veya **manuel** (hazır MP4 + caption Telegram'a
düşer, sen indirip paylaşırsın).

---

## Başla

**→ [`docs/KULLANIM.md`](docs/KULLANIM.md) — adım adım kurulum rehberi**

Kısa versiyon:

```bash
uv sync
uv run playwright install chromium
uv run smauto fetch-fonts

cp .env.example .env        # TELEGRAM_BOT_TOKEN ve TELEGRAM_ALLOWED_USER_IDS doldur
uv run smauto init-db
uv run smauto doctor        # her satır ✓ olmalı

uv run smauto ingest        # Telegram'dan yeni linkleri al
uv run smauto process       # indir → ele → çiz → caption → onaya gönder
```

---

## Ne yapıyor

| Aşama | İş |
|---|---|
| **Intake** | Telegram botuna gelen link veya dosyayı kuyruğa alır |
| **Resolve** | 4 katmanlı fallback: fxtwitter → yt-dlp → gallery-dl → elle yükleme |
| **Screen** | pHash ile kopya kontrolü, AI içerik filtresi, engel listesi |
| **Render** | Playwright ile tweet kartı, ffmpeg ile 1080×1920 Reels, Pillow ile 1080×1350 feed |
| **Caption** | Claude ile Türkçe caption + hashtag, kaynak kredisi zorunlu |
| **Deliver** | Telegram'da önizleme + `✅ ✏️ 🔁 ❌` butonları |
| **Publish** | R2'ye yükler, Instagram container → publish, günde 3 sabit slot |

Ayrıntı: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/SPEC.md`](docs/SPEC.md)

---

## Komutlar

```
smauto doctor           # ffmpeg, chromium, font, DB, token, R2 — hepsini kontrol eder
smauto ingest           # Telegram güncellemelerini çeker
smauto process          # kuyruğu bir aşama ilerletir
smauto publish          # onaylananları zamanlar ve vakti geleni yayınlar
smauto refresh-tokens   # Instagram token'ını yeniler (60 günde bir şart)
smauto show <id>        # bir içeriğin tüm durumu
smauto unpublish <id>   # yayından kaldır + kaynağı engelle
smauto fetch-fonts      # Inter + Noto Color Emoji indirir
```

---

## Neden bu tasarım

Kararların gerekçeleri [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)'de. Özeti:

- **Tetikleyici Telegram, "beğeni" değil.** Beğeni sadece X'i çözer; Telegram'ın
  paylaş menüsü üç platformu da kapsar ve aynı bot onay arayüzü olarak da çalışır.
  X beğeni polling'i mimaride yeri hazır bir eklenti (P6).
- **Tweet kartı x.com screenshot'ı değil.** Kendi HTML/CSS kartımızı Chromium'da
  çiziyoruz: login yok, deterministik, Türkçe + emoji garantili.
- **Doğrudan ffmpeg.** Encode parametreleri sabitlenmiş (`h264 High / yuv420p /
  30fps / AAC`), çünkü Instagram bunların dışındakileri sessizce reddediyor.
- **P3'te sistem zaten kullanılabilir.** Meta hesabı, R2, token — hiçbiri
  Telegram teslimi için gerekmiyor.

---

## Geliştirme

```bash
uv run pytest              # 140 test
uv run pytest -m "not slow"   # gerçek ffmpeg/Chromium gerektirmeyenler
uv run ruff check src tests
uv run mypy src
```

Video testlerinin kabul kriteri `ffprobe` çıktısıdır, gözle kontrol değil —
Instagram'ın reddettiği videoların çoğu gözle sorunsuz görünüyor.

---

## Maliyet

| Kalem | Aylık |
|---|---|
| Telegram Bot API | $0 |
| GitHub Actions (public repo) | $0 |
| Cloudflare R2 (10 GB, egress yok) | $0 |
| Anthropic API (Haiku, ~10 içerik/gün) | ~$0.30–1 |
| **Toplam** | **~$1** |

Detay: [`docs/SETUP.md`](docs/SETUP.md)

---

## Telif ve platform politikası

Bu sistem başkalarının içeriğini yeniden yayınlıyor. Kurallar koda gömülü:

- Her paylaşımda kaynak yazar caption'da etiketlenir (`@handle via X`) — kapatılamaz.
- Korumalı/özel hesap içeriği işlenmeden reddedilir.
- Başka bir meme sayfasının filigranını taşıyan içerik AI filtresinde bloklanır.
- Kaldırma talebinde `smauto unpublish <id>` postu siler ve kaynağı engel listesine alır.
- `AUTO_PUBLISH=false` varsayılan — tam otomatik moda filtreye güvendikten sonra geçilir.
