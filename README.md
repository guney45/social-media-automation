# Social Media Automation

Bu repo, X/Twitter'da seçtiğim komik içerikleri yarı otomatik bir onay akışıyla Instagram'da paylaşmak için tasarlanacak otomasyon projesinin başlangıç dokümanıdır.

## Hedef

İlk sürümün hedefi tamamen kontrolsüz bir bot değil, telif ve platform kurallarına takılmamak için **insan onaylı yarı otomatik** bir sistem kurmaktır:

1. X/Twitter'da beğenilen veya DM/yer imi gibi özel bir kuyruğa eklenen postları algıla.
2. Post içindeki görsel/video medyayı ve metni çıkar.
3. Instagram formatına uygun görsel/video üret:
   - 1080x1350 feed görseli,
   - 1080x1920 Reels/Story videosu,
   - Tweet metnini üst/alt bant veya ekran görüntüsü kartı olarak yerleştirme.
4. İçeriği taslak olarak göster ve manuel onay al.
5. Instagram Graph API ile profesyonel hesaba paylaş.

## Önerilen MVP yaklaşımı

### Neden doğrudan “beğenince paylaş” değil?

X/Twitter beğenileri artık hassas ve API erişimi planlara göre değişebiliyor. Bu yüzden en sağlam MVP tetikleyicisi şunlardan biridir:

- X'te post linkini kendine DM atmak,
- Telegram/Discord botuna link göndermek,
- tarayıcı bookmarklet/extension ile “Instagram kuyruğuna ekle” butonu kullanmak,
- en son seçenek olarak X API'den kullanıcının beğenilerini okumak.

### En düşük maliyetli mimari

```text
Telefon / tarayıcı
  -> Linki Telegram botuna veya küçük web formuna gönder
  -> Backend kuyruğa alır
  -> Medyayı indirir veya kullanıcının manuel yüklemesini ister
  -> FFmpeg/Pillow ile Instagram formatına dönüştürür
  -> Önizleme + onay ekranı
  -> Instagram Graph API ile paylaşır
```

## Platform gerçekleri

### Instagram

Instagram'a API ile paylaşım için genellikle Instagram Business veya Creator hesabı, bağlı Facebook Page, Meta uygulaması ve uygun izinler gerekir. Meta'nın Content Publishing dokümanı tek görsel, video, Reels ve carousel içerik yayınlamayı destekleyen akışı tarif eder.

### X/Twitter

X API'de beğeni uçları vardır; ancak erişim seviyesi, ücretli planlar ve özel beğeniler nedeniyle “beğendim, otomatik çek” fikri pratikte kırılgan olabilir. MVP'de link gönderme veya bookmarklet daha güvenilir olur.

### TikTok

TikTok tarafında Content Posting API vardır; ileride aynı dönüştürme pipeline'ı TikTok için de kullanılabilir. İlk sürümde Instagram'a odaklanmak maliyet ve karmaşıklığı azaltır.

## Telif ve güvenlik notları

Bu proje başkalarının içeriklerini otomatik yeniden paylaşacağı için şu kuralları ürün akışına eklemek gerekir:

- Her içerik için manuel onay adımı.
- Kaynak linkini ve kullanıcı adını caption'a ekleme seçeneği.
- İçerik sahibinden kaldırma talebi gelirse silme/blacklist akışı.
- Özel hesaplardan, filigranlı veya açıkça izin verilmeyen içeriklerden kaçınma.
- API tokenlarını repoya koymama; `.env` ve secret manager kullanma.

## İlk geliştirme adımları

1. `docs/architecture.md` içindeki MVP mimarisini netleştir.
2. Telegram botu veya basit web formu ile link toplama modülünü yaz.
3. SQLite tabanlı içerik kuyruğu ekle.
4. `ffmpeg` ve `pillow` ile görsel/video kompozisyon prototipi yap.
5. Instagram Graph API sandbox/test hesabı ile yayınlama denemesi yap.
6. Onay paneli eklemeden otomatik canlı paylaşım açma.

## Planlanan teknoloji yığını

- Backend: Python + FastAPI veya Node.js + TypeScript
- Kuyruk/veritabanı: SQLite ile başla, gerekirse Postgres'e geç
- Medya işleme: FFmpeg, Pillow/MoviePy
- Deploy: tek VPS, Docker Compose veya GitHub Actions + küçük sunucu
- Tetikleyici: Telegram botu veya bookmarklet
