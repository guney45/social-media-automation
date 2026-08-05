# social-media-automation

X (Twitter), Instagram ve TikTok'ta beğendiğim komik içerikleri toplayıp, Instagram'da
paylaşıma hazır **Reels / feed görseli** olarak üreten otomasyon.

Tek cümlelik akış:

```
Telefonda paylaş → bot yakalar → medyayı indirir → tweet kartını çizer →
1080x1920 Reels'e derler → caption yazar → Telegram'a onaya gönderir → Instagram'a basar
```

İçerik seçimi dışındaki her adım otomatik. Instagram'a basma adımı iki modda çalışır:
**otomatik** (Instagram Graph API) veya **manuel** (hazır MP4 + caption Telegram'a düşer,
sen indirip paylaşırsın).

---

## Dokümanlar

Bunları sırayla oku. Kod yazmadan önce en az `ARCHITECTURE` + `SPEC` gerekli.

| Doküman | İçerik |
|---|---|
| [`docs/REVIEW.md`](docs/REVIEW.md) | İlk taslak planın (v0) eksikleri ve neden değiştirildiği |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Nihai mimari, alınan kararlar, elenen alternatifler |
| [`docs/SPEC.md`](docs/SPEC.md) | Veri modeli, modül arayüzleri, medya spec'leri, ffmpeg/Playwright reçeteleri, AI prompt'ları |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Faz faz, numaralı ticket'lar + kabul kriterleri (implementasyon buradan yürür) |
| [`docs/SETUP.md`](docs/SETUP.md) | Hesaplar, secret'lar, deploy, maliyet tablosu, operasyon runbook'u |

---

## Durum

Şu an repoda **kod yok**, sadece plan var. Implementasyon `docs/ROADMAP.md` içindeki
ticket sırasına göre ilerler.

- [ ] P0 — İskelet (config, DB, CLI, CI, `doctor`)
- [ ] P1 — Intake + medya çözümleme
- [ ] P2 — Render (tweet kartı, görsel, video)
- [ ] P3 — Telegram teslimi ← **buradan itibaren sistem uçtan uca kullanılabilir**
- [ ] P4 — AI katmanı (caption, içerik filtresi, dedupe)
- [ ] P5 — Instagram otomatik paylaşım
- [ ] P6 — Opsiyoneller (X likes polling, carousel, story, analitik)

---

## Hızlı bakış: maliyet

Varsayılan kurulumda (P0–P4) aylık maliyet **~$0**. Detay: [`docs/SETUP.md`](docs/SETUP.md).

| Kalem | Maliyet |
|---|---|
| Telegram Bot API | $0 |
| GitHub Actions (public repo) | $0 |
| Turso / Supabase (DB) | $0 |
| Cloudflare R2 (medya barındırma) | $0 (10 GB, egress ücretsiz) |
| Anthropic API (caption + filtre, Haiku) | ~$0.30–1 / ay |
| X API (opsiyonel, sadece likes polling için) | ~$1–5 / ay |

---

## Telif ve platform politikası

Bu sistem başkalarının içeriğini yeniden yayınlıyor. Kurallar koda gömülü:

- Her paylaşımda kaynak yazar caption'da etiketlenir (`@handle via X`).
- Özel/korumalı hesap içeriği hiç işlenmez.
- Başka bir meme sayfasının watermark'ını taşıyan içerik AI filtresinde bloklanır.
- Kaldırma talebi gelirse `blocklist` tablosuna yazar eklenir, geçmiş paylaşım silinir.
- İlk fazlarda her paylaşım insan onayından geçer (`AUTO_PUBLISH=false` varsayılan).
