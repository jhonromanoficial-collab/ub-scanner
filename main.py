"""
UB Scanner Invictus — Backend
Construido para Jhon Román · Programa Ultra Black
Fuente de datos: Financial Modeling Prep (API stable)
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import time
import math
from datetime import datetime
import os

FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
FMP_BASE = "https://financialmodelingprep.com/stable"
HTTP_TIMEOUT = 15

app = FastAPI(title="UB Scanner Invictus API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_CACHE_TTL_SEC = 900
_stock_cache: dict[str, tuple[float, dict]] = {}
_news_cache: dict[str, tuple[float, dict]] = {}


def _cache_get(cache: dict, key: str):
    entry = cache.get(key)
    if entry and (time.time() - entry[0] < _CACHE_TTL_SEC):
        return entry[1]
    return None


def _cache_put(cache: dict, key: str, value: dict):
    cache[key] = (time.time(), value)


def _fmp(path: str, _strict: bool = True, **params):
    if not FMP_API_KEY:
        raise HTTPException(500, "FMP_API_KEY no configurada en el servidor")
    params["apikey"] = FMP_API_KEY
    url = f"{FMP_BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    except requests.exceptions.RequestException as e:
        if _strict:
            raise HTTPException(502, f"Error conectando a FMP: {e}")
        return None
    if r.status_code == 200:
        try:
            return r.json()
        except Exception:
            if _strict:
                raise HTTPException(502, "FMP devolvió respuesta no-JSON")
            return None
    if r.status_code == 402:
        return None
    if not _strict:
        return None
    if r.status_code == 401:
        raise HTTPException(500, "FMP API key inválida o expirada")
    if r.status_code == 403:
        raise HTTPException(500, f"FMP 403: endpoint '{path}' requiere plan superior")
    if r.status_code == 429:
        raise HTTPException(503, "FMP rate-limited (250 calls/día agotadas)")
    if r.status_code >= 500:
        raise HTTPException(502, f"FMP error {r.status_code}")
    raise HTTPException(502, f"FMP HTTP {r.status_code}: {r.text[:200]}")


def _fmp_soft(path: str, **params):
    return _fmp(path, _strict=False, **params)


def _fmp_obj(path: str, _soft: bool = False, **params):
    data = _fmp_soft(path, **params) if _soft else _fmp(path, **params)
    if isinstance(data, list):
        return data[0] if data else {}
    if isinstance(data, dict):
        return data
    return {}


def _fmp_list(path: str, _soft: bool = False, **params):
    data = _fmp_soft(path, **params) if _soft else _fmp(path, **params)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def clean_num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def fmt_money(n):
    n = clean_num(n)
    if n is None:
        return None
    if abs(n) >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"${n/1e3:.1f}K"
    return f"${n:.2f}"


@app.get("/")
def root():
    return {"service": "UB Scanner Invictus", "status": "online",
            "source": "Financial Modeling Prep (stable)", "usage": "GET /stock/{ticker}"}


@app.get("/health")
def health():
    return {"status": "ok", "ts": datetime.now().isoformat(),
            "fmp_key_configured": bool(FMP_API_KEY)}


@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()
    if not ticker or len(ticker) > 12:
        raise HTTPException(400, "Ticker inválido")

    cached = _cache_get(_stock_cache, ticker)
    if cached is not None:
        return cached

    profile = _fmp_obj("profile", symbol=ticker)
    if not profile or not profile.get("symbol"):
        raise HTTPException(404, f"Ticker {ticker} no encontrado en FMP")

    quote = _fmp_obj("quote", _soft=True, symbol=ticker)
    ratios = _fmp_obj("ratios-ttm", _soft=True, symbol=ticker)
    keymetrics = _fmp_obj("key-metrics-ttm", _soft=True, symbol=ticker)
    cashflow_list = _fmp_list("cash-flow-statement", _soft=True, symbol=ticker, limit=5)
    balance = _fmp_obj("balance-sheet-statement", _soft=True, symbol=ticker, limit=1)
    coverage_full = bool(ratios and keymetrics and cashflow_list)

    price = clean_num(profile.get("price")) or clean_num(quote.get("price")) or 0
    market_cap = clean_num(profile.get("marketCap")) or clean_num(quote.get("marketCap")) or 0
    high52 = clean_num(quote.get("yearHigh")) or 0
    low52 = clean_num(quote.get("yearLow")) or 0
    if (not high52 or not low52) and profile.get("range"):
        parts = str(profile.get("range")).split("-")
        if len(parts) == 2:
            low52 = low52 or clean_num(parts[0].strip()) or 0
            high52 = high52 or clean_num(parts[1].strip()) or 0

    pe = clean_num(ratios.get("priceToEarningsRatioTTM"))
    pb = clean_num(ratios.get("priceToBookRatioTTM"))

    eps = None
    earnings_yield = clean_num(keymetrics.get("earningsYieldTTM"))
    if earnings_yield and price:
        eps = round(earnings_yield * price, 4)
    elif pe and price:
        eps = round(price / pe, 4)

    shares = 0
    if market_cap and price:
        shares = market_cap / price
    shares_m = shares / 1e6 if shares else 0

    beta = clean_num(profile.get("beta"))

    roe = clean_num(keymetrics.get("returnOnEquityTTM"))
    roe_pct = roe * 100 if roe is not None else None
    roic = clean_num(keymetrics.get("returnOnInvestedCapitalTTM"))
    if roic is None:
        roic = clean_num(keymetrics.get("returnOnAssetsTTM"))
    roic_pct = roic * 100 if roic is not None else None

    pm = clean_num(ratios.get("netProfitMarginTTM"))
    pm_pct = pm * 100 if pm is not None else None

    de = clean_num(ratios.get("debtToEquityRatioTTM")) or 0
    payout = clean_num(ratios.get("dividendPayoutRatioTTM")) or clean_num(ratios.get("payoutRatioTTM"))
    payout_pct = payout * 100 if payout is not None else 0
    div_yield = clean_num(ratios.get("dividendYieldTTM")) or clean_num(ratios.get("dividendYielTTM"))
    if div_yield is not None:
        div_yield_pct = div_yield * 100 if div_yield < 1 else div_yield
    else:
        div_yield_pct = 0
    div_rate = clean_num(profile.get("lastDividend")) or 0

    fcf_data = []
    for row in cashflow_list:
        fcf_val = clean_num(row.get("freeCashFlow"))
        if fcf_val is None:
            ocf = clean_num(row.get("netCashProvidedByOperatingActivities")) or clean_num(row.get("operatingCashFlow"))
            capex = clean_num(row.get("investmentsInPropertyPlantAndEquipment")) or clean_num(row.get("capitalExpenditure"))
            if ocf is not None and capex is not None:
                fcf_val = ocf + capex
        if fcf_val is None:
            continue
        date_str = row.get("date") or ""
        year = date_str.split("-")[0] if date_str else str(row.get("fiscalYear") or row.get("calendarYear") or "")
        fcf_data.append({"year": year, "fcf_usd": fcf_val, "fcf_m": fcf_val / 1e6})
    fcf_base_m = fcf_data[0]["fcf_m"] if fcf_data else 0

    total_cash = (clean_num(balance.get("cashAndShortTermInvestments"))
                  or clean_num(balance.get("cashAndCashEquivalents")) or 0)
    total_debt = (clean_num(balance.get("totalDebt"))
                  or clean_num(balance.get("netDebt")) or 0)
    net_cash_m = (total_cash - total_debt) / 1e6

    if market_cap >= 200e9:
        cap_class = "MEGA-CAP"
    elif market_cap >= 10e9:
        cap_class = "LARGE-CAP"
    elif market_cap >= 2e9:
        cap_class = "MID-CAP"
    elif market_cap >= 300e6:
        cap_class = "SMALL-CAP"
    else:
        cap_class = "MICRO-CAP"

    summary = profile.get("description") or ""

    result = {
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "name": profile.get("companyName") or quote.get("name") or ticker,
        "sector": profile.get("sector") or "N/D",
        "industry": profile.get("industry") or "N/D",
        "country": profile.get("country") or "N/D",
        "exchange": profile.get("exchangeFullName") or profile.get("exchange") or quote.get("exchange") or "N/D",
        "cap_class": cap_class,
        "website": profile.get("website"),
        "price": price,
        "high_52w": high52,
        "low_52w": low52,
        "market_cap": market_cap,
        "market_cap_fmt": fmt_money(market_cap),
        "roe_pct": roe_pct,
        "roic_pct": roic_pct,
        "profit_margin_pct": pm_pct,
        "debt_equity": de,
        "payout_pct": payout_pct,
        "eps_growth_pct": None,
        "pe": pe,
        "eps": eps,
        "fcf_base_m": fcf_base_m,
        "fcf_history": fcf_data[:5],
        "shares_outstanding_m": shares_m,
        "net_cash_m": net_cash_m,
        "total_cash": total_cash,
        "total_debt": total_debt,
        "dividend_yield_pct": div_yield_pct,
        "dividend_rate": div_rate,
        "beta": beta,
        "pb_ratio": pb,
        "recommendation": "none",
        "target_mean": None,
        "analyst_count": 0,
        "business_summary": summary[:500],
        "coverage_full": coverage_full,
        "data_source": "FMP" if coverage_full else "FMP-partial (ticker fuera de plan free)",
    }

    _cache_put(_stock_cache, ticker, result)
    return result


@app.get("/news/{ticker}")
def get_news(ticker: str, limit: int = 5):
    ticker = ticker.upper().strip()
    cache_key = f"{ticker}:{limit}"
    cached = _cache_get(_news_cache, cache_key)
    if cached is not None:
        return cached
    try:
        items = _fmp_list("news/stock", symbols=ticker, limit=limit)
        result_list = []
        for n in items[:limit]:
            result_list.append({
                "title": n.get("title", ""),
                "publisher": n.get("publisher") or n.get("site", ""),
                "link": n.get("url", ""),
                "published": n.get("publishedDate"),
            })
        payload = {"ticker": ticker, "news": result_list}
        _cache_put(_news_cache, cache_key, payload)
        return payload
    except HTTPException as e:
        return {"ticker": ticker, "news": [], "error": e.detail}
    except Exception as e:
        return {"ticker": ticker, "news": [], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
