# MVP Architecture

## Amaç

Kullanıcının X/Twitter'da gördüğü komik bir postu Instagram hesabında paylaşabilmesi için düşük maliyetli, onaylı ve genişletilebilir bir otomasyon kurmak.

## V1 kapsamı

V1'de otomasyon şu işleri yapar:

- Kullanıcıdan X/Twitter post linki alır.
- Linki kuyruğa ekler.
- Post metnini ve mümkünse medya URL'lerini çıkarır.
- Instagram uyumlu çıktı üretir.
- Kullanıcıya önizleme gösterir.
- Kullanıcı onayından sonra Instagram'a gönderir.

V1'de şunlar bilerek kapsam dışıdır:

- Tam otomatik, onaysız paylaşım.
- Her beğenilen X postunu gerçek zamanlı izleme.
- TikTok ve YouTube Shorts'a aynı anda paylaşım.
- Büyük ölçekli hesap büyütme bot aktiviteleri.

## Modüller

### 1. Intake

Önerilen ilk tetikleyici Telegram botudur. Kullanıcı X/Twitter linkini bota gönderir. Bot şu kayıtları oluşturur:

- kaynak platform,
- kaynak URL,
- gönderen kullanıcı,
- durum: `queued`,
- oluşturulma zamanı.

Alternatif tetikleyiciler:

- bookmarklet,
- Chrome extension,
- küçük web formu,
- X API liked posts polling.

### 2. Fetcher

Fetcher, kaynak URL'den paylaşım metnini ve medyayı alır. Resmi API erişimi varsa API tercih edilir. API yoksa veya maliyetliyse, kullanıcıdan medya dosyasını manuel yüklemesi istenebilir.

Fetcher çıktısı:

- metin,
- medya dosyaları,
- kaynak yazar,
- kaynak post URL'si,
- lisans/telif uyarı bayrakları.

### 3. Composer

Composer, medyayı Instagram formatına çevirir:

- görsel için 1080x1350 veya 1080x1080,
- video/Reels için 1080x1920,
- metin kartı veya overlay,
- kaynak etiketi,
- opsiyonel arka plan blur.

### 4. Review Panel

Review panel minimum şu aksiyonları sunar:

- önizle,
- caption düzenle,
- kaynak bilgisi ekle/kaldır,
- paylaş,
- reddet,
- blacklist'e al.

### 5. Publisher

Publisher ilk etapta Instagram Graph API kullanır. Yayınlamadan önce dosyanın erişilebilir bir URL'de olması gerekir; bu nedenle küçük bir public object storage veya geçici signed URL çözümü gerekir.

## Veri modeli taslağı

```sql
CREATE TABLE posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_platform TEXT NOT NULL,
  source_url TEXT NOT NULL UNIQUE,
  source_author TEXT,
  source_text TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  caption TEXT,
  media_path TEXT,
  rendered_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## Tavsiye edilen yol haritası

1. Önce link alıp kuyruğa yazan Telegram botunu geliştir.
2. Ardından tek görsel post için composer prototipi yaz.
3. Sonra Instagram test paylaşımını bağla.
4. Video/Reels desteğini ekle.
5. En son X beğenilerini otomatik polling seçeneğini değerlendir.

## Riskler

- Platform API fiyatları ve izinleri değişebilir.
- Başkasına ait içeriklerin yeniden paylaşımı telif veya platform politikası sorunu yaratabilir.
- Tam otomatik paylaşım marka güvenliği açısından risklidir.
- Sosyal medya büyümesi sadece otomasyona bağlı değildir; içerik seçimi, niş, caption ve paylaşım zamanı da önemlidir.
