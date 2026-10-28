# v0 Plan İncelemesi

İlk taslak (commit `f187705`, `README.md` + `docs/architecture.md`) yön olarak doğruydu:
insan onaylı akış, FFmpeg/Pillow ile dönüştürme, SQLite kuyruk, "beğeniyi tetikleyici
yapma" uyarısı. Ama **hiç kod yoktu** ve implementasyona geçilebilecek bir seviyede
değildi. Aşağıda somut eksikler.

---

## A. Yanlış / eskimiş platform bilgileri

### A1. "Instagram için bağlı Facebook Page gerekir" — artık doğru değil

v0 şunu diyordu:

> Instagram'a API ile paylaşım için genellikle Instagram Business veya Creator hesabı,
> **bağlı Facebook Page**, Meta uygulaması ve uygun izinler gerekir.

Temmuz 2024'te çıkan **Instagram API with Instagram Login** ile Facebook Page bağlama
zorunluluğu content publishing için kalktı. Instagram Professional (Business veya
Creator) hesabı + Meta app yeterli. Bu, kurulumdan bir Facebook Page + Business Manager
adımını tamamen siliyor.

### A2. X API tier'ları değişti — "Basic $200/ay" modeli artık yok

v0, "ücretli planlar" ve "erişim seviyesi" dilini kullanıyor; bu 2023–2025 dönemindeki
Free / Basic($200) / Pro($5000) modelini varsayıyor. **6 Şubat 2026'da** X pay-per-use
kredi modeline geçti:

- Genel free tier **yok**; Basic ve Pro yeni kayıtlara kapalı (mevcut aboneler devam ediyor).
- Fiyat kaynak başına: **$0.005 / post read**, **$0.010 / user read**, **$0.015 / post create**.
- **Minimum harcama yok**, kredi satın alınıyor, kullanılmazsa $0.
- Aylık okuma tavanı 2M post read.

Bu, sonucu tersine çeviriyor: v0 "beğenileri API'den okumak kırılgan ve pahalı, en son
seçenek" diyordu. Yeni modelde günde ~10 beğeni için maliyet ayda birkaç dolar — yani
**artık ekonomik olarak makul**. Yine de varsayılan tetikleyici olmamalı (gerekçe:
`ARCHITECTURE.md` §2), ama "en son seçenek" değil, "opsiyonel eklenti".

### A3. Instagram rate limit'i hiç yazılmamış

Gerçek limitler: hesap başına **saatte 200 API çağrısı** (2025'te 5000'den düşürüldü) ve
**24 saatlik kayan pencerede 100 API paylaşımı**. Bizim hacmimiz için sorun değil ama
polling döngüsünü buna göre kurmak gerekiyor.

---

## B. Sistemin sessizce çökeceği eksikler

Bunlar planda hiç geçmiyor ve hepsi bu tür sistemleri gerçekten bozan şeyler:

### B1. Instagram token rotasyonu yok
Long-lived access token **60 gün** geçerli. Yenilenmezse sistem iki ay sonra sessizce
durur. Haftalık `refresh_access_token` job'ı + başarısızlıkta Telegram uyarısı şart.

### B2. Dedupe yok
Aynı meme farklı hesaplardan tekrar tekrar karşına çıkar. `tweet_id` unique index
yetmez; **perceptual hash (pHash)** ile görsel benzerlik kontrolü gerekir. Bu olmadan
sayfa kısa sürede kendini tekrar eder.

### B3. Font ve emoji yok
Tweet kartında Türkçe karakter + emoji render edilecek. Docker imajında **Noto Sans**
ve **Noto Color Emoji** bulunmazsa emojiler kutu (tofu) çıkar. Bu tür projelerde en sık
görülen görsel bug.

### B4. Reels güvenli alan (safe area) hesabı yok
Instagram Reels UI'ı alt ~400px ve üst ~140px'i kaplar (caption, kullanıcı adı, sağdaki
aksiyon butonları). Bunu hesaba katmayan kompozisyonlarda tweet metni Instagram
arayüzünün altında kalır.

### B5. Reels uygunluk penceresi yok
API teknik olarak 15 dakikaya kadar video kabul ediyor, ama **Reels sekmesinde
görünmek için 9:16 ve 5–90 saniye** gerekiyor. Dışına çıkan video normal video postu
olarak yayınlanır — yani erişim ölür. Süre normalizasyonu (kısa videoyu loop'la,
uzunu kes) pipeline'ın parçası olmalı.

### B6. Ses normalizasyonu yok
Kaynak videoların ses seviyeleri çok değişken. `loudnorm` ile -14 LUFS'a çekmek gerekir.
Ayrıca **sessiz videolar** için AAC sessiz track üretmek gerekir, aksi halde bazı
durumlarda IG container'ı reddediyor.

### B7. Encode parametreleri belirsiz
"FFmpeg ile dönüştür" yeterli değil. Instagram'ın kabul ettiği çıktı için
`libx264 / High profile / yuv420p / 30fps / AAC 128k / +faststart` sabitlenmeli.
`yuv420p` verilmezse bazı kaynaklar `yuv444p` çıkar ve IG reddeder.

### B8. Public URL sorunu çözülmemiş
v0 "küçük bir public object storage veya geçici signed URL çözümü gerekir" deyip
bırakmış. Somut karar gerekiyor: **Cloudflare R2** (10 GB ücretsiz, egress ücretsiz)
+ 30 günlük lifecycle silme kuralı.

### B9. Hata yönetimi, retry, gözlemlenebilirlik yok
Retry politikası, dead-letter durumu, attempt sayacı, hata bildirimi — hiçbiri yok.
Bir indirme başarısız olduğunda kullanıcı bunu asla öğrenmiyor.

### B10. GitHub Actions dakika matematiği yapılmamış
v0 "GitHub Actions + küçük sunucu" diyor ama private repoda ayda **2000 dakika**
ücretsiz ve **her job en az 1 dakikaya yuvarlanıyor**. 10 dakikada bir worker =
4320 çalışma = 4320 dakika → ücretsiz kotayı aşar. Karar gerekiyor (repoyu public yap
veya periyodu uzat). Detay: `SETUP.md`.

---

## C. Fazla iş / yanlış araç seçimleri

### C1. Ayrı web "Review Panel" gereksiz
v0 önizleme + caption düzenleme + paylaş/reddet/blacklist için ayrı bir panel
öngörüyor. Bu haftalarca iş (frontend, auth, hosting) ve hiçbir ek değer vermiyor.
**Telegram inline butonları** aynı işi bir günde ve mobil-native yapıyor. Aynı bot
zaten intake kanalı; giriş ve onay tek yerde toplanıyor.

### C2. MoviePy önerilmiş
MoviePy yavaş, bellek yiyor ve ffmpeg'i zaten sarmalıyor. Tek `filter_complex` ile
doğrudan ffmpeg çağırmak hem hızlı hem deterministik. MoviePy bağımlılıktan çıkarıldı.

### C3. "Python + FastAPI **veya** Node + TypeScript" — karar verilmemiş
Plan dokümanının işi karar vermek. Seçim yapıldı: **Python 3.12**. Gerekçe: ffmpeg
sarmalayıcıları, Pillow, imagehash, yt-dlp/gallery-dl ve Playwright'ın hepsi Python'da
birinci sınıf. FastAPI ilk fazlarda gereksiz (webhook yerine polling).

### C4. Manuel yükleme yolu ikinci sınıf bırakılmış
"API yoksa kullanıcıdan manuel yüklemesi istenebilir" diye geçiştirilmiş. Oysa bu
**garantili çalışan tek yol** ve birinci sınıf akış olmalı: bot medyayı çözemezse
"dosyayı bana at" der, kullanıcı Telegram'a videoyu düşürür, pipeline aynen devam eder.

---

## D. Implementasyona geçilemez seviyede belirsizlik

Bunlar "eksik özellik" değil, **plan kalitesi** sorunu. Sonnet'e verildiğinde iş
çıkmasını engelleyen şeyler:

- **Yol haritası ticket değil.** "Telegram botunu geliştir" bir görev değil; kabul
  kriteri, girdi/çıktı sözleşmesi, hata davranışı yok.
- **Modül arayüzleri tanımsız.** Fetcher'ın döndürdüğü şeyin şekli yazılmamış; her
  modül birbirine körlemesine bağlanacak.
- **Veri modeli çok ince.** `posts` tablosunda dedupe hash'i, deneme sayacı, hata
  alanı, render varyantları (reel/feed/story ayrı dosyalar), zamanlama slotu, IG media
  id'si — hiçbiri yok. Ayrıca event/log tablosu ve blocklist tablosu yok.
- **Durum makinesi yok.** `status` alanı var ama geçerli değerler ve geçişler
  tanımlanmamış.
- **Config/secret listesi yok.** Hangi env değişkeni gerekiyor, belli değil.
- **Test stratejisi yok.**
- **Maliyet modeli yok.** "Minimal olsun" isteğine rağmen tek bir rakam geçmiyor.

---

## E. Ürün tarafında eksik olan tek şey

**Paylaşım temposu.** v0 render eder etmez paylaşmayı varsayıyor. 8 meme'i arka arkaya
basmak erişimi düşürür ve bot gibi görünür. Onaylananlar bir kuyrukta beklemeli ve
günde 2–3 sabit slotta çıkmalı. Bu, `scheduled_for` alanı + ayrı publish job'ı demek.

---

## Özet

| | v0 | Bu plan |
|---|---|---|
| Kod | yok | ticket'lara bölünmüş, kabul kriterli |
| Onay arayüzü | ayrı web paneli | Telegram inline butonları |
| Tetikleyici | belirsiz (5 alternatif) | Telegram share, X likes opsiyonel eklenti |
| Medya çözümleme | "API veya manuel" | 4 katmanlı fallback zinciri |
| Video işleme | MoviePy | doğrudan ffmpeg, parametreleri sabitlenmiş |
| Dedupe | — | tweet_id + pHash |
| Token rotasyonu | — | haftalık job + uyarı |
| Depolama | "bir çözüm gerekir" | Cloudflare R2 + lifecycle |
| Deploy | "VPS veya Actions" | Actions varsayılan, VPS tek komut alternatif |
| Maliyet | — | ~$0/ay, kalem kalem |
