"""
UB Scanner Invictus — Backend v3 FINAL
Construido para Jhon Román · Programa Ultra Black

MOTOR DCF: idéntico al Excel INVICTUS DCF MÉTODO WARREN BUFFETT
  - SGR = ROIC × Reinvestment Rate (crecimiento inicial)
  - Fade rate automático por sector (Damodaran NYU Stern)
  - 10 sectores completos con rangos
  - Veredicto 4 niveles (🔴 / 🟡 / 🟢 / 💎)

FUENTES DE DATOS (combinadas para máxima cobertura):
  1. FMP (Financial Modeling Prep) → mejor para S&P 500 + large caps
  2. yfinance → respaldo para mid/small caps que FMP no cubre
  3. Si ninguna tiene el ROIC, se puede pasar manual por query param
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import os
import httpx
from typing import Optional

# ============ IMPORTS OPCIONALES (fallback seguro) ============
try:
    import yfinance as yf
    _YFINANCE_OK = True
except Exception:
    _YFINANCE_OK = False

try:
    from deep_translator import GoogleTranslator
    _TRANSLATOR_OK = True
except Exception:
    _TRANSLATOR_OK = False


FMP_API_KEY = os.environ.get("FMP_API_KEY", "")  # Opcional: si está, usa FMP
# API Stable (nueva) de FMP — reemplaza los endpoints legacy que FMP cerró en Aug 2025
FMP_BASE = "https://financialmodelingprep.com/stable"


app = FastAPI(title="UB Scanner Invictus API v3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# TABLA SECTORIAL INVICTUS (idéntica al Excel de Jhon)
# ============================================================
SECTORES_INVICTUS = {
    "Tecnología":                  {"roe":(0.15, 0.30), "roic":(0.15, 0.25), "pm":(0.15, 0.30), "de_max":0.5, "eps":(0.15, 0.25), "pe":(15, 25),  "decay":0.05},
    "Consumo Defensivo":           {"roe":(0.15, 0.25), "roic":(0.12, 0.20), "pm":(0.10, 0.20), "de_max":1.0, "eps":(0.05, 0.10), "pe":(15, 20),  "decay":0.03},
    "Consumo Cíclico":             {"roe":(0.10, 0.20), "roic":(0.08, 0.15), "pm":(0.05, 0.12), "de_max":1.2, "eps":(0.06, 0.12), "pe":(8, 10),   "decay":0.08},
    "Salud":                       {"roe":(0.12, 0.20), "roic":(0.10, 0.18), "pm":(0.15, 0.25), "de_max":0.8, "eps":(0.05, 0.10), "pe":(15, 20),  "decay":0.04},
    "Financiero":                  {"roe":(0.10, 0.20), "roic":(0.06, 0.12), "pm":(0.20, 0.35), "de_max":3.0, "eps":(0.05, 0.10), "pe":(10, 15),  "decay":0.06},
    "Energía / Servicios Públicos":{"roe":(0.08, 0.15), "roic":(0.05, 0.10), "pm":(0.05, 0.12), "de_max":2.0, "eps":(0.02, 0.05), "pe":(8, 15),   "decay":0.10},
    "Industriales":                {"roe":(0.10, 0.18), "roic":(0.08, 0.15), "pm":(0.05, 0.10), "de_max":1.5, "eps":(0.03, 0.08), "pe":(10, 18),  "decay":0.07},
    "Materiales Básicos":          {"roe":(0.08, 0.15), "roic":(0.06, 0.10), "pm":(0.05, 0.10), "de_max":1.5, "eps":(0.03, 0.08), "pe":(8, 15),   "decay":0.09},
    "Comunicaciones":              {"roe":(0.10, 0.12), "roic":(0.08, 0.15), "pm":(0.08, 0.15), "de_max":1.2, "eps":(0.06, 0.10), "pe":(10, 18),  "decay":0.06},
    "Bienes Raíces":               {"roe":(0.08, 0.15), "roic":(0.06, 0.10), "pm":(0.05, 0.10), "de_max":2.0, "eps":(0.03, 0.06), "pe":(8, 15),   "decay":0.06},
}

MAPA_SECTORES = {
    "Technology": "Tecnología",
    "Communication Services": "Comunicaciones",
    "Consumer Defensive": "Consumo Defensivo",
    "Consumer Cyclical": "Consumo Cíclico",
    "Consumer Staples": "Consumo Defensivo",
    "Consumer Discretionary": "Consumo Cíclico",
    "Healthcare": "Salud",
    "Health Care": "Salud",
    "Financial Services": "Financiero",
    "Financial": "Financiero",
    "Financials": "Financiero",
    "Energy": "Energía / Servicios Públicos",
    "Utilities": "Energía / Servicios Públicos",
    "Industrials": "Industriales",
    "Basic Materials": "Materiales Básicos",
    "Materials": "Materiales Básicos",
    "Real Estate": "Bienes Raíces",
}


def mapear_sector(sector_raw: str) -> str:
    if not sector_raw:
        return "Industriales"
    return MAPA_SECTORES.get(sector_raw.strip(), "Industriales")


def traducir_es(texto: str) -> str:
    if not texto or not _TRANSLATOR_OK:
        return texto
    try:
        return GoogleTranslator(source="auto", target="es").translate(texto[:4500])
    except Exception:
        return texto


def fmt_money(n):
    if n is None:
        return None
    n = float(n)
    if abs(n) >= 1e9: return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6: return f"${n/1e6:.1f}M"
    if abs(n) >= 1e3: return f"${n/1e3:.1f}K"
    return f"${n:.2f}"


# ============================================================
# FUENTE 1 — FMP (prioritaria)
# ============================================================
def fmp_fetch(ticker: str) -> dict:
    """Obtiene datos de FMP. Devuelve dict con lo que haya o {} si falla."""
    if not FMP_API_KEY:
        return {}
    out = {"source": "FMP"}
    try:
        with httpx.Client(timeout=10.0) as client:
            # NOTA: FMP cerró los endpoints legacy /api/v3/ en Aug 2025.
            # Ahora usamos /stable/ con ?symbol=TICKER en vez de /api/v3/.../TICKER

            # Profile (nombre, sector, industria, precio)
            r = client.get(f"{FMP_BASE}/profile", params={"symbol": ticker, "apikey": FMP_API_KEY})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    p = data[0]
                    out["name"] = p.get("companyName")
                    out["sector_raw"] = p.get("sector")
                    out["industry"] = p.get("industry")
                    out["country"] = p.get("country")
                    out["exchange"] = p.get("exchangeShortName") or p.get("exchange")
                    out["price"] = p.get("price")
                    out["market_cap"] = p.get("mktCap") or p.get("marketCap")
                    out["website"] = p.get("website")
                    out["business_summary_en"] = p.get("description")
                    out["beta"] = p.get("beta")
                    # 52W high/low pueden venir en campos separados o en range
                    out["high_52w"] = p.get("priceHigh52") or p.get("yearHigh")
                    out["low_52w"] = p.get("priceLow52") or p.get("yearLow")
                    if not out.get("high_52w") and p.get("range"):
                        try:
                            parts = p.get("range", "").split("-")
                            out["low_52w"] = float(parts[0].strip())
                            out["high_52w"] = float(parts[-1].strip())
                        except: pass
            # Key Metrics TTM (ROIC, ROE, payout, debt/eq)
            r = client.get(f"{FMP_BASE}/key-metrics-ttm", params={"symbol": ticker, "apikey": FMP_API_KEY})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    km = data[0]
                    out["roic"] = km.get("roicTTM") or km.get("returnOnInvestedCapitalTTM")
                    out["roe"] = km.get("roeTTM") or km.get("returnOnEquityTTM")
                    out["payout_ratio"] = km.get("payoutRatioTTM")
                    out["debt_equity"] = km.get("debtToEquityTTM") or km.get("debtEquityRatioTTM")
                    out["pe"] = km.get("peRatioTTM") or km.get("priceToEarningsRatioTTM")
                    out["eps"] = km.get("netIncomePerShareTTM") or km.get("epsTTM")
                    out["fcf_per_share"] = km.get("freeCashFlowPerShareTTM")
                    out["dividend_yield"] = km.get("dividendYieldTTM")
            # Ratios TTM (profit margin, EPS growth)
            r = client.get(f"{FMP_BASE}/ratios-ttm", params={"symbol": ticker, "apikey": FMP_API_KEY})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    rt = data[0]
                    out["profit_margin"] = rt.get("netProfitMarginTTM") or rt.get("netIncomeMarginTTM")
                    if "payout_ratio" not in out or out["payout_ratio"] is None:
                        out["payout_ratio"] = rt.get("payoutRatioTTM")
                    if "dividend_yield" not in out or out["dividend_yield"] is None:
                        out["dividend_yield"] = rt.get("dividendYieldTTM")
            # Cash Flow histórico (FCF últimos años)
            r = client.get(f"{FMP_BASE}/cash-flow-statement", params={"symbol": ticker, "apikey": FMP_API_KEY, "limit": 5})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list):
                    fcf_hist = []
                    for row in data:
                        fcf = row.get("freeCashFlow")
                        year = (row.get("date") or "")[:4]
                        if fcf is not None and year:
                            fcf_hist.append({"year": year, "fcf_usd": float(fcf), "fcf_m": float(fcf)/1e6})
                    if fcf_hist:
                        out["fcf_history"] = fcf_hist
                        out["fcf_base_m"] = fcf_hist[0]["fcf_m"]
            # Income Statement (shares outstanding + eps_growth)
            r = client.get(f"{FMP_BASE}/income-statement", params={"symbol": ticker, "apikey": FMP_API_KEY, "limit": 2})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    row = data[0]
                    shares = row.get("weightedAverageShsOutDil") or row.get("weightedAverageShsOut")
                    if shares:
                        out["shares_outstanding"] = shares
                    # Calcular eps growth comparando con año anterior
                    eps_now = row.get("eps") or row.get("epsdiluted")
                    if len(data) >= 2 and eps_now:
                        eps_prev = data[1].get("eps") or data[1].get("epsdiluted")
                        if eps_prev and eps_prev > 0:
                            out["eps_growth"] = (eps_now - eps_prev) / eps_prev
            # Dividendos (para dividend_rate)
            r = client.get(f"{FMP_BASE}/dividends", params={"symbol": ticker, "apikey": FMP_API_KEY, "limit": 4})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) >= 1:
                    # Sumar últimos 4 dividendos trimestrales ≈ dividendo anual
                    total = sum((row.get("dividend") or 0) for row in data[:4])
                    if total > 0:
                        out["dividend_rate"] = total
            # Analyst estimates (EPS growth futuro, opcional)
            r = client.get(f"{FMP_BASE}/analyst-estimates", params={"symbol": ticker, "apikey": FMP_API_KEY, "limit": 1})
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, list) and len(data) > 0:
                    est = data[0].get("estimatedEpsAvg") or data[0].get("epsAvg")
                    cur_eps = out.get("eps")
                    if est and cur_eps and cur_eps > 0 and not out.get("eps_growth"):
                        out["eps_growth"] = (est - cur_eps) / cur_eps
    except Exception as e:
        out["fmp_error"] = str(e)[:200]
        print(f"[FMP ERROR {ticker}] {e}")
    return out


# ============================================================
# FUENTE 2 — yfinance (respaldo)
# ============================================================
def yf_fetch(ticker: str) -> dict:
    """Obtiene datos de yfinance como respaldo."""
    if not _YFINANCE_OK:
        return {}
    out = {"source": "yfinance"}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
            return {}
        out["name"] = info.get("longName") or info.get("shortName")
        out["sector_raw"] = info.get("sector")
        out["industry"] = info.get("industry")
        out["country"] = info.get("country")
        out["exchange"] = info.get("fullExchangeName") or info.get("exchange")
        out["website"] = info.get("website")
        out["business_summary_en"] = info.get("longBusinessSummary")
        out["price"] = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        out["high_52w"] = info.get("fiftyTwoWeekHigh")
        out["low_52w"] = info.get("fiftyTwoWeekLow")
        out["market_cap"] = info.get("marketCap")
        out["roe"] = info.get("returnOnEquity")
        # Yahoo no da ROIC directo — usamos ROA como proxy (más conservador)
        out["roic"] = info.get("returnOnAssets")
        out["profit_margin"] = info.get("profitMargins")
        de = info.get("debtToEquity")
        out["debt_equity"] = de/100 if de else None
        out["payout_ratio"] = info.get("payoutRatio")
        out["eps_growth"] = info.get("earningsGrowth")
        out["pe"] = info.get("trailingPE") or info.get("forwardPE")
        out["eps"] = info.get("trailingEps")
        shares = info.get("sharesOutstanding")
        out["shares_outstanding"] = shares
        # FIX dividend_yield: yfinance cambia el formato entre versiones.
        # Versión vieja (<= 0.2.x): fracción (0.0031 = 0.31%)
        # Versión nueva (1.3+): ya en % (0.31 = 0.31%)
        # Validamos cruzando con dividendRate: yield real = divRate / price
        # Si el yield guardado es coherente con eso, lo dejamos. Si no, lo escalamos.
        dy = info.get("dividendYield")
        div_rate = info.get("dividendRate")
        price_now = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        if dy is not None:
            yield_fraccion = dy  # asumimos fracción (formato viejo)
            # Si tenemos price + divRate, calculamos el real
            if div_rate and price_now and price_now > 0:
                yield_real = div_rate / price_now  # Esto es la fracción correcta (ej: 0.0031)
                # Si el yield guardado es mucho más grande que el real, está en %, no en fracción
                if dy > yield_real * 50:  # Si es 50x más grande, está en %
                    yield_fraccion = dy / 100
            else:
                # Sin datos para validar: heurística simple
                # Más de 20 implica que viene como 31 (muy raro) → dividir por 100
                # Más de 0.5 implica que viene como % (0.31%) → dividir por 100
                if dy > 0.5:
                    yield_fraccion = dy / 100
            out["dividend_yield"] = yield_fraccion
        out["dividend_rate"] = div_rate
        out["total_cash"] = info.get("totalCash") or 0
        out["total_debt"] = info.get("totalDebt") or 0
        out["beta"] = info.get("beta")
        out["target_mean"] = info.get("targetMeanPrice")
        out["analyst_count"] = info.get("numberOfAnalystOpinions", 0)
        # FCF histórico
        try:
            cf = t.cashflow
            if cf is not None and not cf.empty and "Free Cash Flow" in cf.index:
                hist = []
                for date, val in cf.loc["Free Cash Flow"].items():
                    if val is not None:
                        hist.append({"year": str(date.year), "fcf_usd": float(val), "fcf_m": float(val)/1e6})
                if hist:
                    out["fcf_history"] = hist
                    out["fcf_base_m"] = hist[0]["fcf_m"]
        except Exception:
            pass
    except Exception as e:
        out["yf_error"] = str(e)[:100]
    return out


# ============================================================
# FUSIÓN DE FUENTES (FMP primero, yfinance como fallback)
# ============================================================
def fetch_combined(ticker: str) -> dict:
    """Combina FMP + yfinance: FMP primero, yfinance completa huecos."""
    out = {}
    fmp = fmp_fetch(ticker) if FMP_API_KEY else {}
    yfd = yf_fetch(ticker)
    # Prioridad: FMP sobre yfinance
    for d in [yfd, fmp]:
        for k, v in d.items():
            if v is not None and v != "" and (out.get(k) is None):
                out[k] = v
    # Fuente final
    sources = []
    if fmp and not fmp.get("fmp_error"): sources.append("FMP")
    if yfd and not yfd.get("yf_error"): sources.append("yfinance")
    out["data_sources"] = sources
    return out


# ============================================================
# ENDPOINTS
# ============================================================
@app.get("/")
def root():
    return {
        "service": "UB Scanner Invictus v3",
        "status": "online",
        "method": "DCF Buffett con fade rate sectorial (Damodaran)",
        "sources": "FMP + yfinance combinados",
        "usage": "GET /stock/{ticker} — opcional: ?roic=0.40&reinv=0.68"
    }


@app.get("/health")
def health():
    return {"status": "ok", "ts": datetime.now().isoformat()}


@app.get("/sectors")
def list_sectors():
    return {"sectors": [{"name": name, **data} for name, data in SECTORES_INVICTUS.items()]}


@app.get("/stock/{ticker}")
def get_stock(
    ticker: str,
    roic: Optional[float] = Query(None, description="ROIC 5Y manual (opcional, en fracción: 0.40 = 40%)"),
    reinv: Optional[float] = Query(None, description="Tasa reinversión manual (opcional, en fracción)"),
):
    """
    Devuelve TODOS los datos para el DCF Invictus:
    - Datos combinados FMP + yfinance (máxima cobertura)
    - 7 indicadores Invictus
    - FCF histórico
    - **INVICTUS DCF**: ROIC 5Y, Reinvestment Rate, Sector Invictus, Decay Rate, Rangos
    - Si pasas ?roic=0.40&reinv=0.68 los usa en lugar de los automáticos
    """
    ticker = ticker.upper().strip()
    data = fetch_combined(ticker)

    if not data or data.get("price") is None:
        raise HTTPException(404, f"Ticker {ticker} no encontrado o sin datos disponibles")

    # ===== EXTRAER VALORES CON FALLBACK =====
    price = data.get("price") or 0
    high52 = data.get("high_52w") or 0
    low52 = data.get("low_52w") or 0
    market_cap = data.get("market_cap") or 0

    roe = data.get("roe")
    roe_pct = roe * 100 if roe else None
    roic_auto = data.get("roic")
    roic_pct = roic_auto * 100 if roic_auto else None
    pm = data.get("profit_margin")
    pm_pct = pm * 100 if pm else None
    de = data.get("debt_equity") or 0
    payout = data.get("payout_ratio") or 0
    payout_pct = payout * 100
    eps_g = data.get("eps_growth")
    eps_g_pct = eps_g * 100 if eps_g else None
    pe = data.get("pe")
    eps = data.get("eps")

    fcf_history = data.get("fcf_history") or []
    fcf_base_m = data.get("fcf_base_m") or 0

    shares = data.get("shares_outstanding") or 0
    shares_m = shares / 1e6 if shares else 0

    total_cash = data.get("total_cash") or 0
    total_debt = data.get("total_debt") or 0
    net_cash_m = (total_cash - total_debt) / 1e6

    div_yield = data.get("dividend_yield") or 0
    div_yield_pct = div_yield * 100
    div_rate = data.get("dividend_rate") or 0

    # ===== CAP CLASS =====
    if market_cap >= 200e9: cap_class = "MEGA-CAP"
    elif market_cap >= 10e9: cap_class = "LARGE-CAP"
    elif market_cap >= 2e9: cap_class = "MID-CAP"
    elif market_cap >= 300e6: cap_class = "SMALL-CAP"
    else: cap_class = "MICRO-CAP"

    # ===== SECTOR INVICTUS + DECAY =====
    sector_raw = data.get("sector_raw") or ""
    sector_invictus = mapear_sector(sector_raw)
    sector_data = SECTORES_INVICTUS[sector_invictus]

    # ===== INVICTUS DCF PARAMS =====
    # ROIC 5Y: usa el manual si viene, sino el automático, sino 10% default
    roic_5y = roic if roic is not None else (roic_auto if roic_auto else 0.10)
    # Reinvestment Rate: manual si viene, sino 1-payout, sino 70% default
    if reinv is not None:
        reinv_rate = reinv
    elif payout is not None:
        reinv_rate = max(0, 1 - payout)
    else:
        reinv_rate = 0.70
    sgr_inicial = roic_5y * reinv_rate
    decay_rate = sector_data["decay"]
    wacc_sugerido = 0.12  # Punto medio recomendado (10%-17% rango Invictus)
    tasa_perpetua = 0.03

    # ===== MOAT =====
    moat_score = 0
    moat_signals = []
    if roe_pct is not None:
        if roe_pct >= 20: moat_score += 2; moat_signals.append("Rentabilidad sobre patrimonio excepcional (ROE alto)")
        elif roe_pct >= 12: moat_score += 1; moat_signals.append("Buena rentabilidad sobre patrimonio")
    if pm_pct is not None:
        if pm_pct >= 20: moat_score += 2; moat_signals.append("Márgenes de ganancia muy altos: poder de fijación de precios")
        elif pm_pct >= 10: moat_score += 1; moat_signals.append("Márgenes de ganancia saludables")
    if roic_pct is not None:
        if roic_pct >= 12: moat_score += 2; moat_signals.append("Retorno sobre capital invertido sobresaliente")
        elif roic_pct >= 7: moat_score += 1; moat_signals.append("Retorno sobre capital invertido sólido")
    if de is not None and de <= 0.5:
        moat_score += 1; moat_signals.append("Deuda muy baja: solidez financiera")
    if market_cap >= 10e9:
        moat_score += 1; moat_signals.append("Empresa grande y consolidada en su mercado")

    if moat_score >= 7:
        moat_level, moat_desc = "MOAT AMPLIO", "Ventaja competitiva fuerte y durable. Es el tipo de negocio que Buffett llama 'castillo con foso ancho': difícil de atacar por la competencia."
    elif moat_score >= 4:
        moat_level, moat_desc = "MOAT MODERADO", "Tiene ventajas competitivas reales, pero no impenetrables. Buen negocio que requiere seguir vigilando su posición."
    elif moat_score >= 2:
        moat_level, moat_desc = "MOAT ESTRECHO", "Ventaja competitiva limitada. El negocio compite duro y sus retornos pueden erosionarse con el tiempo."
    else:
        moat_level, moat_desc = "SIN MOAT CLARO", "No se detectan señales financieras de ventaja competitiva durable. Mayor riesgo para inversión de largo plazo."

    # ===== RESUMEN TRADUCIDO =====
    biz_en = data.get("business_summary_en") or ""
    biz_es = traducir_es(biz_en[:900]) if biz_en else ""

    return {
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "method": "Invictus DCF v3",
        "data_sources": data.get("data_sources", []),

        "name": data.get("name") or ticker,
        "sector": sector_raw or "N/D",
        "sector_invictus": sector_invictus,
        "industry": data.get("industry") or "N/D",
        "country": data.get("country") or "N/D",
        "exchange": data.get("exchange") or "N/D",
        "cap_class": cap_class,
        "website": data.get("website"),

        "moat_level": moat_level,
        "moat_desc": moat_desc,
        "moat_score": moat_score,
        "moat_signals": moat_signals,

        "price": price,
        "high_52w": high52,
        "low_52w": low52,
        "market_cap": market_cap,
        "market_cap_fmt": fmt_money(market_cap),

        "roe": roe, "roe_pct": roe_pct,
        "roic_pct": roic_pct,
        "profit_margin": pm, "profit_margin_pct": pm_pct,
        "debt_equity": de,
        "payout_ratio": payout, "payout_pct": payout_pct,
        "eps_growth": eps_g, "eps_growth_pct": eps_g_pct,
        "pe": pe, "eps": eps,

        # ============ DATOS CLAVE INVICTUS DCF ============
        "invictus_dcf": {
            "roic_5y": roic_5y,                      # Final usado (manual o auto)
            "roic_5y_auto": roic_auto,               # El que detectó auto (para mostrar al usuario)
            "roic_5y_source": "manual" if roic is not None else ("auto" if roic_auto else "default"),
            "reinvestment_rate": reinv_rate,
            "reinvestment_rate_source": "manual" if reinv is not None else ("auto" if payout else "default"),
            "sgr_inicial": sgr_inicial,
            "decay_rate": decay_rate,
            "wacc_sugerido": wacc_sugerido,
            "tasa_perpetua": tasa_perpetua,
            "sector_ranges": sector_data,
        },

        "fcf_base_m": fcf_base_m,
        "fcf_history": fcf_history[:5],
        "shares_outstanding_m": shares_m,
        "net_cash_m": net_cash_m,
        "total_cash": total_cash,
        "total_debt": total_debt,

        "dividend_yield_pct": div_yield_pct,
        "dividend_rate": div_rate,

        "beta": data.get("beta"),
        "target_mean": data.get("target_mean"),
        "analyst_count": data.get("analyst_count") or 0,

        "business_summary": biz_es,
        "business_summary_en": biz_en[:900],
    }


@app.get("/news/{ticker}")
def get_news(ticker: str, limit: int = 5):
    ticker = ticker.upper().strip()
    if not _YFINANCE_OK:
        return {"ticker": ticker, "news": []}
    try:
        t = yf.Ticker(ticker)
        news = t.news or []
        result = []
        for n in news[:limit]:
            result.append({
                "title": n.get("title", ""),
                "publisher": n.get("publisher", ""),
                "link": n.get("link", ""),
                "published": n.get("providerPublishTime"),
            })
        return {"ticker": ticker, "news": result}
    except Exception as e:
        return {"ticker": ticker, "news": [], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
