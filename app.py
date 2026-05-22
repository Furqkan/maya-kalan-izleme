"""
Maya Tekstil — Kalan İzleme Web Dashboard
Flask backend: aynı API'yi çeker, tarayıcıya sunar.
"""

import os
import time
import logging
import datetime as dt
from functools import wraps

import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request, session, redirect, url_for

# ──────────────────────────────────────────────
#  CONFIG
# ──────────────────────────────────────────────
class Config:
    SECRET_KEY      = os.environ.get("SECRET_KEY", "maya-gizli-anahtar-degistir")
    API_H           = "https://mayaapi.mayatextile.com/FurkanHammaddeKalan"
    API_U           = "https://mayaapi.mayatextile.com/FurkanUretimKalan"
    CACHE_TTL       = 300          # saniye (5 dk)
    KRITIK_GUN      = 5
    EXCLUDED_BOLUM  = {"GENEL", "TEST", "DEPO"}

    # Basit kullanıcı listesi — ileride DB'ye taşıyabilirsin
    USERS = {
        "admin":  {"password": "maya2024", "role": "admin"},
        "izleme": {"password": "izleme123", "role": "viewer"},
    }

# ──────────────────────────────────────────────
#  UYGULAMA
# ──────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ──────────────────────────────────────────────
#  BELLEK-İÇİ CACHE  (basit dict, production'da Redis kullan)
# ──────────────────────────────────────────────
_cache: dict = {}

def _cache_get(key: str):
    entry = _cache.get(key)
    if entry and time.time() < entry["exp"]:
        return entry["val"]
    return None

def _cache_set(key: str, val, ttl: int = Config.CACHE_TTL):
    _cache[key] = {"val": val, "exp": time.time() + ttl}

# ──────────────────────────────────────────────
#  VERİ ÇEKME  (PyQt6 kodundaki _api() + _std() mantığı)
# ──────────────────────────────────────────────
EXCLUDED_BOLUMLER = {"GENEL", "TEST", "DEPO"}

def _std(df: pd.DataFrame, tip: str) -> pd.DataFrame:
    """Kolon adlarını normalize et, eksikleri doldur."""
    if df.empty:
        return df
    mapping = {
        "isemrino":          "IsEmriNo",
        "mmodelkodu":        "MModelKodu",
        "mmodeladi":         "MModelAdi",
        "hmodelkodu":        "HModelKodu",
        "hmodeladi":         "HModelAdi",
        "hbirim":            "HBirim",
        "uretimsonukalan":   "UretimSonuKalan",
        "uretimbolumadi":    "UretimBolumAdi",
        "transfertarihi":    "TransferTarihi",
        "kalan":             "Kalan",
        "acikkapali":        "AcikKapali",
        "sonurungiristarihi":"SonUrunGirisTarihi",
    }
    df = df.rename(columns={c: mapping.get(c.lower(), c) for c in df.columns})

    required_h = ["IsEmriNo","MModelKodu","MModelAdi","HModelKodu","HModelAdi",
                  "HBirim","UretimSonuKalan","UretimBolumAdi","TransferTarihi"]
    required_u = ["IsEmriNo","MModelKodu","MModelAdi","Kalan",
                  "UretimBolumAdi","AcikKapali","SonUrunGirisTarihi"]
    required   = required_h if tip == "h" else required_u

    for col in required:
        if col not in df.columns:
            if col in ("UretimSonuKalan", "Kalan"):
                df[col] = 0
            elif "Tarihi" in col:
                df[col] = pd.NaT
            else:
                df[col] = ""

    for col in ("UretimSonuKalan", "Kalan"):
        if col in df.columns:
            df[col] = (
                pd.to_numeric(
                    df[col].astype(str).str.replace(",", ".", regex=False),
                    errors="coerce"
                ).fillna(0)
            )
    return df


def _is_gunu(tarih_serisi: pd.Series) -> pd.Series:
    """Hafta sonlarını saymadan iş günü farkı."""
    bugun = dt.date.today()
    dates = pd.to_datetime(tarih_serisi, errors="coerce", dayfirst=True).dt.date
    valid = dates.notna() & (dates <= bugun)
    result = np.zeros(len(tarih_serisi), dtype=int)
    if valid.any():
        arr = np.array(dates[valid].tolist(), dtype="datetime64[D]")
        result[valid.values] = np.maximum(
            np.busday_count(arr, np.datetime64(bugun, "D")), 0
        )
    return pd.Series(result, index=tarih_serisi.index)


def fetch_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """API'den veri çeker, 5 dk cache'ler."""
    cached = _cache_get("data")
    if cached:
        return cached

    headers = {"Accept": "application/json", "Connection": "keep-alive"}
    try:
        import concurrent.futures

        def get(url):
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            d = r.json()
            return pd.DataFrame(d if isinstance(d, list) else d.get("data", d))

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_h = ex.submit(get, Config.API_H)
            f_u = ex.submit(get, Config.API_U)
            df_h = f_h.result()
            df_u = f_u.result()

    except Exception as e:
        logging.warning(f"API hatası, örnek veri kullanılıyor: {e}")
        df_h, df_u = _sample_data()

    df_h = _std(df_h, "h")
    df_u = _std(df_u, "u")

    # Tarih & iş günü hesapla
    if "TransferTarihi" in df_h.columns:
        df_h["TransferTarihi"] = pd.to_datetime(
            df_h["TransferTarihi"], errors="coerce", dayfirst=True)
        df_h["GeçenGün"] = _is_gunu(df_h["TransferTarihi"])

    if "SonUrunGirisTarihi" in df_u.columns:
        df_u["SonUrunGirisTarihi"] = pd.to_datetime(
            df_u["SonUrunGirisTarihi"], errors="coerce", dayfirst=True)
        df_u["GeçenGün"] = _is_gunu(df_u["SonUrunGirisTarihi"])

    # Bölüm filtresi
    if "UretimBolumAdi" in df_h.columns:
        df_h = df_h[~df_h["UretimBolumAdi"].str.upper().isin(EXCLUDED_BOLUMLER)]

    result = (df_h.reset_index(drop=True), df_u.reset_index(drop=True))
    _cache_set("data", result)
    return result


def _sample_data():
    """API erişimi yoksa demo veri döndür."""
    np.random.seed(42)
    bolumler = ["KESİM", "DİKİM", "ÜTÜ", "KALİTE KONTROL", "NAKIŞ", "APLIKE"]
    n = 80

    df_h = pd.DataFrame({
        "IsEmriNo":        [f"IE{10000+i}" for i in range(n)],
        "MModelKodu":      [f"MM{100+i%20}" for i in range(n)],
        "MModelAdi":       [f"Model {i%20+1}" for i in range(n)],
        "HModelKodu":      [f"HM{200+i%15}" for i in range(n)],
        "HModelAdi":       [f"Ham Madde {i%15+1}" for i in range(n)],
        "HBirim":          np.random.choice(["MT","KG","AD"], n),
        "UretimSonuKalan": np.random.randint(10, 500, n).astype(float),
        "UretimBolumAdi":  np.random.choice(bolumler, n),
        "TransferTarihi":  pd.date_range(end=dt.date.today(), periods=n, freq="6h"),
    })

    df_u = pd.DataFrame({
        "IsEmriNo":           [f"IE{10000+i}" for i in range(n)],
        "MModelKodu":         [f"MM{100+i%20}" for i in range(n)],
        "MModelAdi":          [f"Model {i%20+1}" for i in range(n)],
        "UretimBolumAdi":     np.random.choice(bolumler, n),
        "AcikKapali":         np.random.choice(["Açık","Kapalı"], n),
        "SonUrunGirisTarihi": pd.date_range(end=dt.date.today(), periods=n, freq="8h"),
        "Kalan":              np.random.randint(5, 300, n).astype(float),
    })
    return df_h, df_u

# ──────────────────────────────────────────────
#  LOGIN ZORUNLULUĞU
# ──────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated

# ──────────────────────────────────────────────
#  ROTALAR
# ──────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = Config.USERS.get(username)
        if user and user["password"] == password:
            session["user"] = username
            session["role"] = user["role"]
            return redirect(request.args.get("next") or url_for("index"))
        error = "Kullanıcı adı veya şifre hatalı"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("dashboard.html",
                           user=session.get("user"),
                           role=session.get("role"))


# ── API Endpoint'leri ─────────────────────────

@app.route("/api/ozet")
@login_required
def api_ozet():
    """KPI özet verileri."""
    try:
        df_h, df_u = fetch_data()
        kritik = Config.KRITIK_GUN

        t_ham  = int(df_h["UretimSonuKalan"].sum()) if "UretimSonuKalan" in df_h.columns else 0
        t_urt  = int(df_u["Kalan"].sum())            if "Kalan"           in df_u.columns else 0

        crit_h = len(df_h[df_h["GeçenGün"] >= kritik]) if "GeçenGün" in df_h.columns else 0
        crit_u = len(df_u[df_u["GeçenGün"] >= kritik]) if "GeçenGün" in df_u.columns else 0

        avg_h  = round(df_h["GeçenGün"].mean(), 1) if "GeçenGün" in df_h.columns and not df_h.empty else 0
        avg_u  = round(df_u["GeçenGün"].mean(), 1) if "GeçenGün" in df_u.columns and not df_u.empty else 0

        return jsonify({
            "ok": True,
            "ts": dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
            "ham": {
                "toplam_kalan": t_ham,
                "siparis_say":  len(df_h),
                "kritik_say":   crit_h,
                "ort_gun":      avg_h,
            },
            "urun": {
                "toplam_kalan": t_urt,
                "siparis_say":  len(df_u),
                "kritik_say":   crit_u,
                "ort_gun":      avg_u,
            },
        })
    except Exception as e:
        logging.exception("api_ozet hatası")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bolum_dagilim")
@login_required
def api_bolum_dagilim():
    """Bölüm bazlı kalan dağılımı (bar chart için)."""
    try:
        df_h, df_u = fetch_data()
        tip = request.args.get("tip", "ham")

        if tip == "ham" and "UretimBolumAdi" in df_h.columns:
            grp = (df_h.groupby("UretimBolumAdi")["UretimSonuKalan"]
                       .sum()
                       .sort_values(ascending=False)
                       .head(10))
        else:
            grp = (df_u.groupby("UretimBolumAdi")["Kalan"]
                       .sum()
                       .sort_values(ascending=False)
                       .head(10)) if "UretimBolumAdi" in df_u.columns else pd.Series()

        return jsonify({
            "ok":     True,
            "labels": grp.index.tolist(),
            "values": [int(v) for v in grp.values],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/gun_dagilim")
@login_required
def api_gun_dagilim():
    """Geçen gün dağılımı (0-5, 5-10, 10+ gün grupları)."""
    try:
        df_h, df_u = fetch_data()
        bins   = [0, 5, 10, 999]
        labels = ["0-5 gün", "5-10 gün", "10+ gün"]

        def dist(df, col):
            if "GeçenGün" not in df.columns:
                return [0, 0, 0]
            cats = pd.cut(df["GeçenGün"], bins=bins, labels=labels, right=False)
            return [int(cats.value_counts().get(l, 0)) for l in labels]

        return jsonify({
            "ok":     True,
            "labels": labels,
            "ham":    dist(df_h, "GeçenGün"),
            "urun":   dist(df_u, "GeçenGün"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tablo")
@login_required
def api_tablo():
    """Tablo verisi — sayfalama + arama + filtreleme."""
    try:
        df_h, df_u = fetch_data()
        tip    = request.args.get("tip", "ham")
        search = request.args.get("q", "").strip().lower()
        bolum  = request.args.get("bolum", "").strip()
        page   = max(1, int(request.args.get("page", 1)))
        limit  = min(100, max(10, int(request.args.get("limit", 50))))
        kritik_only = request.args.get("kritik", "false").lower() == "true"

        if tip == "ham":
            df = df_h.copy()
            kalan_col = "UretimSonuKalan"
        else:
            df = df_u.copy()
            kalan_col = "Kalan"

        # Arama
        if search:
            mask = pd.Series(False, index=df.index)
            for col in ["IsEmriNo", "MModelAdi", "UretimBolumAdi"]:
                if col in df.columns:
                    mask |= df[col].astype(str).str.lower().str.contains(search, na=False)
            df = df[mask]

        # Bölüm filtresi
        if bolum and "UretimBolumAdi" in df.columns:
            df = df[df["UretimBolumAdi"] == bolum]

        # Kritik filtresi
        if kritik_only and "GeçenGün" in df.columns:
            df = df[df["GeçenGün"] >= Config.KRITIK_GUN]

        total = len(df)
        start = (page - 1) * limit
        df_page = df.iloc[start: start + limit]

        def safe_val(v):
            if pd.isna(v):      return None
            if isinstance(v, (np.integer,)):  return int(v)
            if isinstance(v, (np.floating,)): return round(float(v), 2)
            if isinstance(v, pd.Timestamp):   return v.strftime("%d.%m.%Y")
            return str(v)

        rows = [
            {col: safe_val(row[col]) for col in df_page.columns}
            for _, row in df_page.iterrows()
        ]

        return jsonify({
            "ok":    True,
            "total": total,
            "page":  page,
            "limit": limit,
            "rows":  rows,
        })
    except Exception as e:
        logging.exception("api_tablo hatası")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bolumler")
@login_required
def api_bolumler():
    """Unique bölüm listesi (filter dropdown için)."""
    try:
        df_h, df_u = fetch_data()
        tip = request.args.get("tip", "ham")
        df  = df_h if tip == "ham" else df_u
        if "UretimBolumAdi" not in df.columns:
            return jsonify({"ok": True, "bolumler": []})
        bolumler = sorted(df["UretimBolumAdi"].dropna().unique().tolist())
        return jsonify({"ok": True, "bolumler": bolumler})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh", methods=["POST"])
@login_required
def api_refresh():
    """Cache'i temizle, veriyi yenile."""
    _cache.clear()
    try:
        df_h, df_u = fetch_data()
        return jsonify({"ok": True, "msg": "Veri yenilendi",
                        "ham_say": len(df_h), "urun_say": len(df_u)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ──────────────────────────────────────────────
#  ÇALIŞTIRICISI
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
