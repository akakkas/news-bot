# 📰 Haber Botu — Telegram

Türkiye ve dünyadan haberleri RSS üzerinden çeker, Türkçe'ye çevirir, tekrarları eler ve Telegram'a gönderir. GitHub Actions üzerinde **ücretsiz** çalışır.

## Kurulum (10 dakika)

### 1. Telegram bot oluştur
1. Telegram'da [@BotFather](https://t.me/BotFather) ile sohbet aç.
2. `/newbot` yaz → bota isim ve kullanıcı adı ver.
3. Sana bir **token** verir (örn. `123456789:ABC-DEF...`). Kaydet.

### 2. Chat ID'ni öğren
1. Kendi botunla sohbet aç ve **/start** yaz (bu zorunlu — yoksa bot sana mesaj gönderemez).
2. Tarayıcıda aç: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Gelen JSON içinde `"chat":{"id": 123456789` gibi bir sayı ara — bu senin **chat_id**'n.

### 3. Repo'yu hazırla
1. Bu dosyaları yeni bir GitHub reposuna yükle (private olabilir).
2. **Settings → Secrets and variables → Actions → New repository secret**:
   - `TELEGRAM_BOT_TOKEN` → BotFather'dan aldığın token
   - `TELEGRAM_CHAT_ID` → 2. adımdaki sayı
3. **Settings → Actions → General → Workflow permissions**: "Read and write permissions" seçili olsun (state dosyasını commit'leyebilmesi için).

### 4. Çalıştır
- **Actions** sekmesine git → "News Bot" → "Run workflow" → manuel ilk çalıştırma.
- **İlk çalıştırmada hiç mesaj gelmez** (mevcut tüm haberleri "görüldü" olarak işaretler — yoksa 100+ mesajla boğulurdun).
- Sonraki çalıştırmalar her 15 dakikada bir otomatik. Yalnızca **yeni** haberler gelir.

## Düzenleme

- **Kaynak ekle/çıkar:** `news_bot.py` içindeki `FEEDS` listesini düzenle. Format: `(emoji, kategori, kaynak_adı, rss_url, dil)`. Dil `"tr"` ise çeviri atlanır.
- **Sıklık:** `.github/workflows/news.yml` içindeki cron'u değiştir (`*/15` = 15 dk, `*/30` = 30 dk). Not: GitHub cron bazen yoğunlukta gecikir, garanti dakika hassasiyeti yok.
- **Sessiz mod:** Bir kategoriyi tamamen kapatmak için ilgili satırları FEEDS'ten sil veya başına `#` koy.

## Notlar

- **RSS URL'leri:** Listedeki tüm URL'ler standart formatlardır ama bazı yayıncılar zamanla feed yolunu değiştirir. İlk çalıştırmadan sonra Actions log'una bak — "empty/broken feed" yazan kaynak varsa o URL'yi güncelle.
- **Çeviri:** `deep-translator` Google'ın ücretsiz uç noktasını kullanır. Çok yoğun saatte ara sıra başarısız olabilir; o haber orijinal İngilizce başlıkla gider.
- **Tekrar engelleme:** Hem URL hem normalize edilmiş başlık hash'i tutulur. Aynı haberi farklı kaynaktan az çok aynı başlıkla yayınlarlarsa yine yakalar. Tam semantik dedup (aynı olay farklı kelimelerle) yok — istersen sonra ekleriz.
- **Maliyet:** GitHub Actions public repo'da sınırsız, private repo'da ayda 2000 dk ücretsiz. Bu bot ayda ~120 dk kullanır.

## Dosya yapısı

```
.
├── news_bot.py              # ana script
├── requirements.txt         # bağımlılıklar
├── seen.json                # görülen haberler (otomatik güncellenir)
├── README.md                # bu dosya
└── .github/workflows/news.yml  # her 15 dk'da çalışan job
```
