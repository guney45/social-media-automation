# Kullanım Rehberi — Adım Adım

Sıfırdan çalışır hale getirmek için yapman gerekenler. Sırayla git.

**Bölüm 1–4 (~20 dakika)** bittiğinde sistem çalışır: bir tweet paylaşırsın,
telefonuna paylaşıma hazır Reels videosu düşer. Instagram'a otomatik basma
(Bölüm 6) isteğe bağlı ve sonradan eklenebilir.

---

## Bölüm 1 — Telegram botunu aç (5 dk)

Bu botun üç işi var: içerik gönderdiğin yer, önizlemeyi gördüğün yer, onay
verdiğin yer.

1. Telegram'da **[@BotFather](https://t.me/BotFather)**'a yaz → `/newbot`
2. Bota bir isim ver (ör. `Meme Kasa`) ve bir kullanıcı adı (ör. `memekasa_bot`)
3. BotFather sana bir **token** verir. Şuna benzer:
   `7123456789:AAH8x...`. Bunu kaydet.
4. **[@userinfobot](https://t.me/userinfobot)**'a herhangi bir mesaj at.
   Sana **numeric user id**'ni söyler (ör. `512345678`). Bunu da kaydet.
5. Kendi botuna git ve `/start` yaz.
   ⚠️ Bu adımı atlarsan bot sana mesaj gönderemez.

---

## Bölüm 2 — Mac'e kur (~15 dk)

Sıfırdan bir Mac varsayıyorum. Komutları sırayla, tek tek yapıştır — her adımda
bir doğrulama satırı var, çalışmazsa oradan devam etme.

> **Neden kendi bilgisayarıma kuruyorum?** İlk kurulumda bir şeyin eksik olması
> çok olası (bot token'ı yanlış, bota `/start` yazılmamış, font inmemiş).
> `smauto doctor` bunları anında söylüyor. Sistemin çalıştığını gördükten sonra
> Bölüm 5'te Docker veya GitHub Actions'a taşıyorsun; oradan sonra bilgisayarın
> kapalıyken de çalışıyor.

> **venv gerekiyor mu? Hayır.** `uv sync` proje klasöründe otomatik `.venv/`
> oluşturup paketleri oraya kuruyor, sistem Python'una dokunmuyor. `uv run ...`
> da otomatik onu kullanıyor — `activate` yazmana gerek yok. Python 3.12'yi de
> uv kendi indiriyor.

---

### Adım 1 — Homebrew

macOS'ta ffmpeg'i kuracak paket yöneticisi. Kurulum sırasında Mac şifreni
soracak; Xcode Command Line Tools yoksa onu da indirir (~1–2 GB, birkaç dakika).
`git` de bununla birlikte geliyor.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Apple Silicon'da (M1/M2/M3/M4) Homebrew PATH'e kendiliğinden girmiyor:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

```bash
brew --version        # ✅ sürüm yazmalı
```

### Adım 2 — ffmpeg

Videoyu kesen, birleştiren, Instagram formatına çeviren araç. Bağımlılıklarıyla
birlikte büyük (~1 GB), birkaç dakika sürer — normal.

```bash
brew install ffmpeg
```

```bash
ffmpeg -version | head -1     # ✅ sürüm yazmalı
ffprobe -version | head -1    # ✅ sürüm yazmalı
```

### Adım 3 — uv

Python paket yöneticisi. Python'u da kendisi indiriyor.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Kurulum bittiğinde PATH'e almak için (bu adım şart, atlarsan
`command not found` alırsın):

```bash
source $HOME/.local/bin/env
```

```bash
uv --version          # ✅ sürüm yazmalı
```

### Adım 4 — Projeyi indir

```bash
cd ~
git clone https://github.com/guney45/social-media-automation.git
cd social-media-automation
```

Bundan sonraki tüm komutlar bu klasörün içinde çalışıyor. Terminali kapatıp
açarsan `cd ~/social-media-automation` ile geri dön.

### Adım 5 — Bağımlılıklar

```bash
uv sync
```

Tarayıcı (tweet kartını çizmek için) ve fontlar (Türkçe karakter + emoji):

```bash
uv run playwright install chromium
uv run smauto fetch-fonts
```

```bash
uv run smauto version      # ✅ 0.1.0 yazmalı
```

### Adım 6 — Ayarlar

```bash
cp .env.example .env
open -e .env               # TextEdit'te açar
```

Şu üç satırı Bölüm 1'de aldığın değerlerle doldur:

```bash
TELEGRAM_BOT_TOKEN=7123456789:AAH8x...     # BotFather'dan
TELEGRAM_ALLOWED_USER_IDS=512345678        # @userinfobot'tan
TELEGRAM_TARGET_CHAT_ID=512345678          # aynı numara
```

AI caption ve içerik filtresi istiyorsan (aylık ~$0.50) bir de bunu:

```bash
ANTHROPIC_API_KEY=sk-ant-...               # console.anthropic.com
```

İstemiyorsan `.env` içinde `AI_ENABLED=false` yap — sistem AI'sız çalışır,
caption'ı elle yazarsın.

Kaydet ve kapat.

### Adım 7 — Doğrula

```bash
uv run smauto init-db
uv run smauto doctor
```

Çıktı böyle bir tablo:

```
┏━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃    ┃ Kontrol          ┃ Sonuç                                 ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ✓  │ ffmpeg           │ 8.0                                   │
│ ✓  │ chromium         │ ~/Library/Caches/ms-playwright/...    │
│ ✓  │ fontlar          │ InterVariable.ttf + emoji             │
│ ✓  │ emoji testi      │ 161 renk                              │
│ ✓  │ veritabanı       │ 6 tablo                               │
│ ✓  │ telegram         │ @senin_botun                          │
│ ✓  │ anthropic        │ claude-haiku-4-5-20251001             │
│ ✓  │ instagram        │ DELIVERY_MODE=telegram — gerekmiyor   │
│ ✓  │ secret sızıntısı │ .env takip edilmiyor                  │
└────┴──────────────────┴───────────────────────────────────────┘

Her şey yolunda.
```

Bir satır ✗ ise ne yapman gerektiğini yazıyor. En sık çıkanlar:

| Satır | Anlamı |
|---|---|
| `telegram: TELEGRAM_BOT_TOKEN boş` | `.env`'i kaydetmemişsin veya yanlış klasördesin |
| `telegram: TELEGRAM_ALLOWED_USER_IDS boş` | @userinfobot'tan aldığın numarayı gir |
| `telegram: Unauthorized` | Token yanlış kopyalanmış |
| `fontlar` / `emoji testi` | `uv run smauto fetch-fonts` |
| `chromium bulunamadı` | `uv run playwright install chromium` |

Hepsi ✓ ise Bölüm 3'e geç.

---

## Bölüm 3 — İlk içeriğini geçir (2 dk)

1. X/Instagram/TikTok'ta komik bir gönderi bul
2. **Paylaş → Telegram → botunu seç**
   (masaüstünden linki kopyalayıp bota yapıştırman da olur)
3. Bot "Aldım 👍 (#1)" der
4. Terminalde:

```bash
uv run smauto ingest     # Telegram'dan mesajları alır
uv run smauto process    # indirir, çizer, video üretir, sana yollar
```

5. Telegram'a paylaşıma hazır video + caption + 4 buton düşer:

```
[ ✅ Onayla ]  [ ✏️ Caption ]
[ 🔁 Yeniden çiz ]  [ ❌ Sil ]
```

| Buton | Ne yapar |
|---|---|
| ✅ Onayla | İçeriği onaylar. Otomatik paylaşım kapalıysa "indir ve paylaş" der |
| ✏️ Caption | Bot senden yeni caption bekler, yazdığını kullanır |
| 🔁 Yeniden çiz | Kart temasını değiştirip (koyu ↔ açık) baştan üretir |
| ❌ Sil | İçeriği reddeder |

Butona bastıktan sonra `uv run smauto ingest` çalıştırmayı unutma — buton
basışları da bir "update", onları da almak gerekiyor. (Bölüm 5'te bu
otomatikleşiyor.)

6. Videoyu Telegram'dan indir, Instagram'da paylaş. **Bitti.**

---

## Bölüm 4 — Bot komutları

Telegram'da bota yazabileceklerin:

| Komut | Ne yapar |
|---|---|
| `/status` | Kuyrukta ne var, hangi aşamada |
| `/stats` | Son 7 gün: alınan / reddedilen / yayınlanan |
| `/block @kullanici` | O hesabın içeriğini bir daha alma |
| `/help` | Kısa yardım |

Terminalden:

| Komut | Ne yapar |
|---|---|
| `smauto show 5` | 5 numaralı içeriğin her şeyi (durum, hata, dosyalar) |
| `smauto doctor` | Sağlık kontrolü — bir şey bozulduğunda ilk çalıştıracağın |
| `smauto unpublish 5` | Yayından kaldır + kaynağı engelle (kaldırma talebi geldiğinde) |

---

## Bölüm 5 — Otomatikleştir (elle komut çalıştırmayı bırak)

Şu ana kadar `ingest` ve `process`'i elle çalıştırdın. Sistemin çalıştığını
gördüğüne göre artık otomatiğe bağlayabilirsin. Üç seçenek, kolaydan zora:

### Seçenek A — Mac'te döngü (30 saniye, hemen dene)

Terminali açık bıraktığın sürece çalışır. Kapatınca durur.

```bash
cd ~/social-media-automation
while true; do
  uv run smauto ingest
  uv run smauto process
  sleep 60
done
```

Durdurmak için `Ctrl+C`.

### Seçenek B — Docker (Mac açıkken arka planda)

Terminal kapansa da çalışır, Mac uykuya girince durur.
Önce [Docker Desktop](https://www.docker.com/products/docker-desktop/) kur, sonra:

```bash
cd ~/social-media-automation
docker compose build                       # ilk seferde ~5 dk
docker compose run --rm smauto doctor      # ✅ hepsi ✓ olmalı
docker compose run --rm smauto init-db
docker compose up -d worker                # arka planda başlat
```

```bash
docker compose logs -f worker              # ne yaptığını izle
docker compose down                        # durdur
```

Docker `.env` dosyanı olduğu gibi kullanıyor, ayrıca bir ayar gerekmiyor.
Bu moddayken her `uv run smauto X` komutunun karşılığı
`docker compose run --rm smauto X`.

### Seçenek C — GitHub Actions ($0, bilgisayarın kapalıyken de çalışır)

1. **Repoyu public yap.**
   Neden: GitHub her job'ı en az 1 dakikaya yuvarlıyor. Private repoda ayda
   2000 dakika ücretsiz; 10 dakikada bir çalışan worker bunu aşar. Public
   repoda dakika sınırsız. Kodda secret yok, hepsi GitHub Secrets'ta duruyor.

2. **Secret'ları gir:** repo → Settings → Secrets and variables → Actions →
   *New repository secret*:

   | İsim | Değer |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | BotFather token'ın |
   | `TELEGRAM_ALLOWED_USER_IDS` | numeric user id'n |
   | `TELEGRAM_TARGET_CHAT_ID` | aynı numara |
   | `ANTHROPIC_API_KEY` | (AI kullanıyorsan) |

3. Actions sekmesinden `worker` workflow'unu bir kez **Run workflow** ile
   elle çalıştır, çalıştığını gör. Sonrası 10 dakikada bir otomatik.

> ⚠️ **Durum kalıcılığı.** Actions'ta veritabanı `actions/cache` ile koşular
> arasında taşınıyor — bu "best effort"tur, cache düşerse kuyruk ve tekrar
> kontrolü geçmişi sıfırlanır. Ciddiye alacaksan ücretsiz bir Postgres al
> (Supabase veya Neon) ve `DATABASE_URL` secret'ını onun bağlantı adresine
> ayarla. Tek değişiklik bu.

---

## Bölüm 6 — Instagram'a otomatik paylaşım (isteğe bağlı, ~45 dk)

Buraya kadar her şey çalışıyorsa ve elle paylaşmaktan sıkıldıysan.

### 6.1 Instagram hesabını hazırla
Instagram → Ayarlar → Hesap türü ve araçlar → **Profesyonel hesaba geç**
(Business veya Creator — ikisi de olur).

> Facebook sayfası **gerekmiyor**. Instagram Login API'si kullanılıyor.

### 6.2 Meta uygulaması oluştur
1. [developers.facebook.com](https://developers.facebook.com) → **Create App**
2. Ürün ekle: **Instagram** → *API setup with Instagram login*
3. **Instagram App ID** ve **App Secret**'ı not al
4. **Roles → Instagram Testers** altına kendi hesabını ekle
5. Instagram uygulamasında daveti kabul et:
   Ayarlar → Web sitesi izinleri → Tester davetleri

> App Review **gerekmiyor**. Uygulama Development mode'da kalıyor ve sen kendi
> hesabına tester olarak ekli olduğun için yayın yapabiliyorsun.

### 6.3 Token al
1. OAuth akışını çalıştır, izinler:
   `instagram_business_basic`, `instagram_business_content_publish`
2. Dönen kısa ömürlü token'ı 60 günlüğe çevir:

```bash
uv run smauto exchange-token KISA_OMURLU_TOKEN
```

3. `IG_USER_ID`'yi öğren: `GET https://graph.instagram.com/me?fields=id,username&access_token=...`

### 6.4 Cloudflare R2 (medya barındırma, ücretsiz)
Instagram videoyu internetten çekebilmek istiyor, o yüzden bir yerde public
olması gerekiyor.

1. Cloudflare hesabı → **R2** → bucket oluştur
2. Bucket → Settings → **Public access** aç → çıkan adresi not al
3. **Manage R2 API Tokens** → S3 uyumlu anahtar üret
4. ⚠️ **Lifecycle rule ekle: 30 gün sonra sil.** Instagram medyayı kendi
   sunucusuna kopyaladığı için sonrasında tutmaya gerek yok. Bu kural olmazsa
   ücretsiz 10 GB birkaç ayda dolar.

### 6.5 `.env`'i güncelle

```bash
DELIVERY_MODE=instagram
AUTO_PUBLISH=true
STORAGE_BACKEND=r2

IG_USER_ID=...
IG_ACCESS_TOKEN=...
IG_APP_ID=...
IG_APP_SECRET=...

R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=...
R2_PUBLIC_BASE_URL=https://pub-xxxx.r2.dev
```

```bash
uv run smauto doctor    # "ig token" ve "depolama" satırları ✓ olmalı
```

### 6.6 Token yenilemeyi kur ⚠️ ATLAMA
Instagram token'ı **60 gün** geçerli. Yenilenmezse sistem iki ay sonra
sessizce durur — hata vermez, sadece paylaşmaz.

- GitHub Actions kullanıyorsan: `publish` workflow'u bunu haftalık yapıyor,
  ek bir şey gerekmiyor.
- Kendi sunucundaysan: `docker compose up -d token-refresh` ya da haftalık
  bir cron: `uv run smauto refresh-tokens`

`smauto doctor` sana kaç gün kaldığını gösterir; 14 günün altına düşünce
Telegram'a uyarı gelir.

### 6.7 Paylaşım saatleri
Onaylanan içerik hemen paylaşılmaz, günün sabit slotlarına dağıtılır:

```bash
PUBLISH_SLOTS=12:30,18:30,21:30
MAX_PER_DAY=3
TIMEZONE=Europe/Istanbul
```

Neden: 8 içeriği arka arkaya basmak hem erişimi düşürüyor hem bot sinyali
veriyor. Actions kullanıyorsan `publish.yml` içindeki cron'u da (UTC!) buna
göre güncelle.

---

## Sorun giderme

**Her şeyden önce:** `uv run smauto doctor`

| Belirti | Sebep | Çözüm |
|---|---|---|
| Bot mesajlarıma cevap vermiyor | `TELEGRAM_ALLOWED_USER_IDS` yanlış | @userinfobot'tan id'ni tekrar al |
| Bot bana mesaj atamıyor | Botla sohbet başlatmamışsın | Bota `/start` yaz |
| Emojiler kutu (□) çıkıyor | Emoji fontu yok | `uv run smauto fetch-fonts` |
| Kart yazı tipi garip görünüyor | Inter yüklenmemiş | `uv run smauto fetch-fonts` |
| "medyasını indiremedim" | Kaynak login istiyor | Videoyu doğrudan bota at — pipeline devam eder |
| yt-dlp "login required" | X çerezleri bayat | Tarayıcıda x.com'a çıkış/giriş yap, çerezleri yeniden dışa aktar, `COOKIES_FILE` ayarla |
| Instagram videoyu reddediyor | Encode parametresi | `uv run smauto show <id>` → hata mesajı; render zaten `yuv420p`/h264 doğruluyor |
| Reels sekmesinde görünmüyor | Süre 5–90 sn dışında | Sistem otomatik ayarlıyor; `show` çıktısındaki nota bak |
| Paylaşım aniden durdu | IG token 60 günü doldurdu | `uv run smauto refresh-tokens` |
| Aynı meme ikinci kez geldi | pHash eşiği dar | `src/smauto/screen/dedupe.py` → `THRESHOLD` 6'dan 8'e |
| İçerik hep eleniyor | AI skoru sert | `.env` → `AI_MIN_SCORE=30` |
| Video Telegram'a düşmüyor | 50 MB bot sınırı | Bot fotoğraf + dosya yolunu gönderir; dosyayı diskten al |

### İçerik neden elendi?

```bash
uv run smauto show 12
```

`reject_reason` alanı sebebi yazar: engel listesi, kopya içerik, AI filtresi
(hangi flag), düşük skor.

---

## Günlük akış (her şey kurulduktan sonra)

1. Telefonunda gezerken komik bir şey görürsün
2. **Paylaş → Telegram → botun**
3. 10 dakika içinde Telegram'a önizleme düşer
4. ✅ ya da ❌
5. Onayladıkların ya otomatik paylaşılır ya da senin indirip paylaşman için hazır bekler

Elle yapman gereken tek şey: neyin komik olduğuna karar vermek.
