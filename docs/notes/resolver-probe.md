# Resolver doğrulama notu (P1-0)

## Durum: ⚠️ CANLI DOĞRULAMA YAPILAMADI

`api.fxtwitter.com` ve `api.vxtwitter.com`, kodun yazıldığı ortamdan test
edilemedi — geliştirme ortamının giden bağlantı proxy'si bu hostlara
`CONNECT tunnel failed, response 403` döndürüyor. Aynı proxy `pypi.org`,
`raw.githubusercontent.com` ve `archive.ubuntu.com`'a izin veriyor, yani
engel bu iki servise özel ve servislerin kendi durumu hakkında bir şey
söylemiyor.

**Bu yüzden fxtwitter resolver'ı kayıtlı yanıt örnekleri üzerinden yazıldı ve
test edildi**, canlı trafikle değil.

## Ne test edildi

`tests/fixtures/` altındaki dört kayıtlı yanıt üzerinden
(`tests/test_resolve.py`):

| Fixture | Kapsam |
|---|---|
| `fxtwitter_photo.json` | tek görsel, yazar, avatar, tarih, `t.co` temizliği |
| `fxtwitter_video.json` | video, süre (saniye → ms dönüşümü) |
| `fxtwitter_text.json` | medyasız tweet |
| `fxtwitter_quote.json` | alıntı tweet, iç içe kart |

Ayrıca 401 → `ProtectedContentError`, 500 → `ResolveError` ve
şema uyuşmazlığında ikinci endpoint'e düşme davranışı test ediliyor.

Parser hem fxtwitter'ın `{"tweet": {...}}` sarmalayıcısını hem vxtwitter'ın
düz `media_extended` şeklini kabul edecek biçimde yazıldı.

## Kurulumda yapman gereken doğrulama

İlk gerçek linki gönderdiğinde bu kendiliğinden anlaşılır. Ayrıca elle:

```bash
curl -s "https://api.fxtwitter.com/status/1800000000000000001" | head -c 400
```

- **JSON dönüyorsa:** bir şey yapmana gerek yok, zincirin 1. katmanı çalışıyor.
- **404/503/boş dönüyorsa:** `.env` içinde sırayı değiştir:

  ```bash
  RESOLVER_CHAIN=ytdlp,gallerydl,manual
  ```

  Mimari değişmiyor; yt-dlp birincil olur. X çoğu gönderi için oturum
  istediğinden `COOKIES_FILE` ayarlaman gerekebilir (`docs/KULLANIM.md`
  sorun giderme bölümü).

- **Her iki durumda da** 4. katman (`manual`) hep çalışır: bot medyayı
  çözemezse senden dosyayı ister, pipeline aynen devam eder.

## Neden bu bir tıkanma noktası değil

Zincirin tamamı bu belirsizliği absorbe etmek için tasarlandı. En kötü senaryo
— fxtwitter kapalı, X çerezleri bayat — sistemin durması değil, gönderi başına
bir ek dokunuş (videoyu bota atmak).
