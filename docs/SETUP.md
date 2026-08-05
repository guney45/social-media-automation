# Kurulum, Maliyet ve Operasyon

---

## 1. Ne zaman neye ihtiyacın var

Her şeyi baştan kurmana gerek yok. Faza göre gereken hesaplar:

| Ne için | Gereken | Süre |
|---|---|---|
| **Çalıştırmak** | **Telegram botu** | **5 dk** |
| AI caption + içerik filtresi | Anthropic API anahtarı | 5 dk |
| Instagram'a otomatik paylaşım | Instagram Professional hesap + Meta app + Cloudflare R2 | ~45 dk |
| X beğeni polling'i (opsiyonel) | X Developer hesabı + kredi | ~20 dk |

> Adım adım kurulum için: **[`KULLANIM.md`](KULLANIM.md)**. Bu doküman referans
> ve operasyon runbook'u.

**Sadece Telegram botuyla sistem çalışır durumda.** Instagram entegrasyonunu hiç
yapmasan da her gün telefonuna paylaşıma hazır Reels dosyaları düşer.

---

## 2. Telegram botu (5 dakika)

1. Telegram'da **@BotFather** → `/newbot` → isim ve kullanıcı adı ver
   → `TELEGRAM_BOT_TOKEN` çıkar
2. **@userinfobot**'a mesaj at → kendi numeric user id'ni al
   → `TELEGRAM_ALLOWED_USER_IDS` ve `TELEGRAM_TARGET_CHAT_ID`
3. Botunla bir sohbet başlat (`/start`) — aksi halde bot sana mesaj atamaz

> `TELEGRAM_ALLOWED_USER_IDS` boş bırakılmaz. Bot token'ı sızarsa başkası
> senin adına içerik üretemesin diye kod bunu zorunlu tutuyor.

**Kullanımı:** X/Instagram/TikTok uygulamasında Paylaş → Telegram → botunu seç.

---

## 3. Instagram (otomatik paylaşım için)

Facebook Page **gerekmiyor**. Instagram API with Instagram Login kullanılıyor.

1. Instagram hesabını **Professional** yap (Ayarlar → Hesap türü → Business veya Creator)
2. [developers.facebook.com](https://developers.facebook.com) → **Create App**
3. Ürün ekle: **Instagram** → *API setup with Instagram login*
4. **Instagram App ID** ve **App Secret**'ı not al → `IG_APP_ID`, `IG_APP_SECRET`
5. **Roles → Instagram Testers** altına kendi hesabını ekle
6. Instagram uygulamasında daveti kabul et:
   Ayarlar → Web sitesi izinleri → Tester davetleri
7. OAuth akışını çalıştır, izinler:
   `instagram_business_basic`, `instagram_business_content_publish`
8. Short-lived token'ı **long-lived** ile değiştir (60 gün) → `IG_ACCESS_TOKEN`
9. `IG_USER_ID`'yi `GET /me?fields=id,username` ile al

> **App Review gerekmiyor.** App *Development mode*'da kalıyor ve sen kendi
> hesabına tester olarak ekli olduğun için yayın yapabiliyorsun. App Review
> sadece başkalarının hesaplarına hizmet veren uygulamalar için gerekli.

> ⏰ **Token 60 günde bir yenilenmeli.** `publish.yml` bunu haftalık yapıyor.
> `smauto doctor` kalan gün sayısını gösterir; 14 günün altına düşünce Telegram'a
> uyarı gelir. Bu adımı atlamak sistemi iki ay sonra sessizce durdurur.

### Bilmen gereken limitler
- 200 API çağrısı / saat / hesap
- 100 API paylaşımı / 24 saat (kayan pencere)
- Reels sekmesinde görünmek için: 9:16 oran, **5–90 saniye**

---

## 4. Cloudflare R2 (medya barındırma)

Instagram, `video_url`'in **public erişilebilir** olmasını istiyor. R2 bunun için
en ucuz yol: 10 GB depolama ücretsiz ve **egress ücreti yok** (S3'ün aksine).

1. Cloudflare hesabı → **R2** → bucket oluştur
2. Bucket → Settings → **Public access** aç (r2.dev alt alanı yeterli)
   → `R2_PUBLIC_BASE_URL`
3. **Manage R2 API Tokens** → S3 uyumlu anahtar üret
   → `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ACCOUNT_ID`
4. **Lifecycle rule ekle: 30 gün sonra sil.** Instagram medyayı kendi sunucusuna
   kopyaladığı için yayından sonra dosyayı tutmaya gerek yok. Bu kural olmadan
   ücretsiz kotayı birkaç ayda doldurursun.

---

## 5. Deploy

### Seçenek A — GitHub Actions (varsayılan, $0)

Üç workflow:

| Dosya | Cron | İş |
|---|---|---|
| `worker.yml` | her 10 dk | `ingest` + `process` |
| `publish.yml` | slot saatleri | `publish` |
| `publish.yml` | pazartesi 04:00 UTC | `refresh-tokens` |
| `ci.yml` | her push | ruff + mypy + pytest |

Secret'lar: repo → Settings → Secrets and variables → Actions.

#### ⚠️ Dakika hesabı — repoyu public yap

GitHub Actions **her job'ı en az 1 dakikaya yuvarlar**. Free planda private repo
ayda 2000 dakika:

```
10 dk'da bir worker  = 4320 çalışma/ay  → 4320+ dk   ❌ kotayı aşar
30 dk'da bir worker  = 1440 çalışma/ay  → 1440 dk
  + render yapan çalışmalar (~10/gün × ~3 dk)  →  +600 dk
  + publish (3/gün) + CI                       →  +150 dk
                                          toplam ≈ 2190 dk  ❌ yine aşar
```

**Karar: repoyu public yap → Actions dakikaları sınırsız.** Kodda secret yok,
hepsi GitHub Secrets'ta. Repo private kalacaksa alternatifler: GitHub Pro
($4/ay, 3000 dk) veya Seçenek B.

Workflow dosyaları hazır: `.github/workflows/worker.yml` (cron `*/10`),
`publish.yml` (yayın slotları + haftalık token yenileme), `ci.yml` (lint/type/test).

> **Durum kalıcılığı.** Actions runner'ı her koşuda sıfırdan başlar. `worker.yml`
> veritabanını `actions/cache` ile taşıyor; cache düşerse kuyruk, dedupe geçmişi
> ve Telegram offset'i sıfırlanır (Telegram son 24 saatin güncellemelerini tekrar
> gönderir). Kalıcı çözüm: ücretsiz bir Postgres (Supabase/Neon) alıp
> `DATABASE_URL` secret'ını ayarlamak. Render edilmiş medya `DELIVERY_MODE=instagram`
> iken render anında R2'ye yükleniyor, o yüzden yayın aşamasında yerel dosyaya
> ihtiyaç yok.

#### Kurulum süresini sıfırla
Her çalışmada Playwright Chromium indirmek ~60 sn yiyor. Bunun yerine CI'da bir
Docker imajı build edip **GHCR**'a push et, workflow'lar `container:` ile onu
kullansın. Kurulum süresi saniyelere iner. (`ubuntu-latest`'te ffmpeg zaten kurulu.)

### Seçenek B — VPS (~€3.79/ay)

Hetzner CX22 veya benzeri. Docker Compose + systemd timer, aynı CLI.
Gecikme 10 dakikadan saniyelere iner, kota derdi yok. Aylık ~4 € ödemeye
razıysan operasyonel olarak en rahat yol bu.

---

## 6. Maliyet tablosu

### Varsayılan kurulum (Telegram teslimi)

| Kalem | Aylık |
|---|---|
| Telegram Bot API | $0 |
| GitHub Actions (public repo) | $0 |
| Depolama (yerel / Actions artifact) | $0 |
| Anthropic API — Haiku, ~10 item/gün | $0.30 – $1 |
| **Toplam** | **~$1** |

### Tam kurulum (Instagram otomatik)

| Kalem | Aylık |
|---|---|
| Yukarıdakiler | ~$1 |
| Cloudflare R2 (10 GB ücretsiz + lifecycle) | $0 |
| Instagram Graph API | $0 |
| **Toplam** | **~$1** |

### Opsiyonel eklentiler

| Kalem | Aylık |
|---|---|
| X API likes polling (opsiyonel) | ~$1 – $5 |
| VPS yerine Actions (Seçenek B) | ~€3.79 |
| Caption için Haiku yerine Sonnet | ~$3 – $8 |

**X API fiyatlandırması (6 Şubat 2026'dan itibaren):** pay-per-use kredi modeli.
$0.005 / post read, $0.010 / user read, $0.015 / post create. Minimum harcama yok,
free tier yok. Günde ~10 beğeni + makul polling ile aylık birkaç dolar.

---

## 7. Operasyon runbook'u

### `smauto doctor` her şeyden önce
Bir şey çalışmıyorsa ilk komut bu. ffmpeg, font, Playwright, DB, token süresi,
R2 erişimi — hepsini tek tabloda gösterir.

### Sık karşılaşılan sorunlar

| Belirti | Sebep | Çözüm |
|---|---|---|
| Emojiler kutu (□) çıkıyor | `Noto Color Emoji` yok | `smauto fetch-fonts`, sonra `smauto doctor` |
| Instagram video'yu reddediyor | `yuv420p` değil veya ses stream'i yok | `ffprobe out_reel.mp4` — `SPEC.md` §4.2'deki tabloyla karşılaştır |
| Reels sekmesinde görünmüyor | Süre 5–90 sn dışında veya oran 9:16 değil | süre normalizasyonu çalışıyor mu bak |
| Paylaşım aniden durdu | IG token 60 günü doldurdu | `smauto refresh-tokens`; `publish.yml`'ın haftalık koşusu neden çalışmamış bak |
| Metin Instagram UI'ının altında kalıyor | safe area hesabı bozuk | `layout.py` testleri, `SPEC.md` §5.1 |
| yt-dlp "login required" | X çerezleri bayatladı (günler içinde olur) | tarayıcıda x.com'a çıkış/giriş yap, çerezleri yeniden dışa aktar |
| Aynı meme ikinci kez düştü | pHash eşiği dar | `SPEC.md` §6, Hamming eşiğini 6→8 çıkarmayı dene |
| Actions job'ları çakışıyor | concurrency group yok | `worker.yml`'a `concurrency` ekle |
| Container `ERROR` dönüyor | medya URL'i public değil | R2 public access + `R2_PUBLIC_BASE_URL` |

### Düzenli bakım

| Sıklık | İş |
|---|---|
| Haftalık (otomatik) | IG token yenileme |
| Aylık | R2 kullanımı kontrol, lifecycle çalışıyor mu |
| Gerektiğinde | X çerezlerini yenile (yt-dlp katmanı kullanılıyorsa) |
| Çeyreklik | `AI_MIN_SCORE` eşiğini gerçek performansa göre ayarla |

---

## 8. Güvenlik kontrol listesi

- [ ] `.env` `.gitignore`'da
- [ ] `TELEGRAM_ALLOWED_USER_IDS` dolu
- [ ] Secret'lar GitHub Secrets'ta, kodda değil
- [ ] Bulut DB kullanılıyorsa `TOKEN_ENCRYPTION_KEY` set
- [ ] R2 bucket'ında sadece render çıktıları var, kaynak/kimlik verisi yok
- [ ] `AUTO_PUBLISH=false` — filtreye güvenene kadar
- [ ] Repo public yapıldıysa git geçmişinde secret olmadığı doğrulandı

---

## Kaynaklar

- [Instagram Platform — Meta for Developers](https://developers.facebook.com/documentation/instagram-platform)
- [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)
- [X API pay-per-usage pricing and credits](https://docs.x.com/x-api/getting-started/pricing)
- [X API Bookmarks endpoints](https://docs.x.com/x-api/posts/bookmarks/introduction)
