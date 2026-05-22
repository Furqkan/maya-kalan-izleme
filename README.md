# Maya Tekstil — Kalan İzleme Web Dashboard

PyQt6 masaüstü uygulamasının Flask tabanlı web versiyonu.
Aynı `mayaapi.mayatextile.com` API'sini kullanır.

## Lokal Çalıştırma

```bash
# 1. Bağımlılıkları kur
pip install -r requirements.txt

# 2. Çalıştır
python app.py

# 3. Tarayıcıda aç
http://localhost:5000
```

## Kullanıcılar (varsayılan)

| Kullanıcı | Şifre      | Rol    |
|-----------|------------|--------|
| admin     | maya2024   | admin  |
| izleme    | izleme123  | viewer |

> **Üretimde mutlaka** `app.py` içindeki `Config.USERS` şifrelerini değiştir!

## İnternete Açma — Render.com (Ücretsiz)

1. Kodu GitHub'a push et
2. https://render.com → New → Web Service → Repoyu seç
3. Environment: Python, Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
5. Environment variable: `SECRET_KEY` = rastgele uzun bir string
6. Deploy!

URL: `https://maya-kalan-izleme.onrender.com` (ücretsiz)

## API Endpointleri

| Endpoint | Açıklama |
|---|---|
| `GET /api/ozet` | KPI özet verisi |
| `GET /api/bolum_dagilim?tip=ham` | Bölüm bazlı kalan |
| `GET /api/gun_dagilim` | Geçen gün dağılımı |
| `GET /api/tablo?tip=ham&page=1&q=...` | Sayfalı tablo |
| `GET /api/bolumler?tip=ham` | Unique bölüm listesi |
| `POST /api/refresh` | Cache temizle + yenile |

## Proje Yapısı

```
maya_web/
├── app.py                # Flask backend
├── requirements.txt
├── render.yaml           # Render.com deploy config
└── templates/
    ├── login.html        # Giriş ekranı
    └── dashboard.html    # Ana dashboard (KPI + charts + tablolar)
```
