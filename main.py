"""
UB Scanner Invictus — Backend
Construido para Jhon Román · Programa Ultra Black
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import yfinance as yf
import pandas as pd
import math
import time
import random
from datetime import datetime
import os

try:
    from curl_cffi import requests as curl_requests
    _HAS_CURL_CFFI = True
except Exception:
    _HAS_CURL_CFFI = False

app = FastAPI(title="UB Scanner Invictus API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_IMPERSONATES = ["chrome", "chrome120", "chrome116", "chrome110", "chrome107",
                 "safari15_5", "safari17_0", "edge99", "edge101", "firefox133"]
_CACHE_TTL_SEC = 900
_MAX_RETRIES = 5
_stock_cache: dict[str, tuple[float, dict]] = {}
_news_cache: dict[str, tuple[float, dict]] = {}

def _make_session(impersonate: str | None = None):
    if not _HAS_CURL_CFFI:
        return None
    imp = impersonate or random.choice(_IMPERSONATES)
    try:
        return curl_requests.Session(impersonate=imp)
    except Exception:
        try:
            return curl_requests.Session(impersonate="chrome")
        except Exception:
            return None

def _ticker(symbol: str, impersonate: str | None = None):
    sess = _make_session(impersonate)
    if sess is not None:
        try:
            return yf.Ticker(symbol, session=sess)
        except TypeError:
            pass
    return yf.Ticker(symbol)

def _cache_get(cache: dict, key: str):
    entry = cache.get(key)
    if entry and (time.time() - entry[0] < _CACHE_TTL_SEC):
        return entry[1]
    return None

def _cache_put(cache: dict, key: str, value: dict):
    cache[key] = (time.time(), value)

def safe_get(d, key, default=None):
    v = d.get(key, default)
    if v is None or v == "":
        return default
    if isinstance(v, float) and math.isnan(v):
        return default
    return v

def clean_num(v):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v

def fmt_money(n):
    if n is None:
        return None
    n = float(n)
    if abs(n) >= 1e9:
        return f"${n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"${n/1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"${n/1e3:.1f}K"
    return f"${n:.2f}"

@app.get("/")
def root():
    return {"service": "UB Scanner Invictus", "status": "online", "usage": "GET /stock/{ticker}"}

@app.get("/health")
def health():
    return {"status": "ok", "ts": datetime.now().isoformat()}

@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    ticker = ticker.upper().strip()
    cached = _cache_get(_stock_cache, ticker)
    if cached is not None:
        return cached
    try:
        info = None
        t = None
        last_err = None
        impersonates_to_try = random.sample(_IMPERSONATES, min(_MAX_RETRIES, len(_IMPERSONATES)))
        for attempt, imp in enumerate(impersonates_to_try):
            try:
                t = _ticker(ticker, impersonate=imp)
                info = t.info
                if info and (info.get("regularMarketPrice") is not None or info.get("currentPrice") is not None):
                    break
                last_err = "info vacío"
            except Exception as e:
                last_err = str(e)
            is_rate_limit = last_err and ("rate" in last_err.lower() or "429" in last_err or "too many" in last_err.lower())
            if attempt < len(impersonates_to_try) - 1:
                backoff = (2.0 * (attempt + 1)) + random.uniform(0, 1.5)
                time.sleep(backoff if is_rate_limit else 0.5)
        if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
            raise HTTPException(503, f"Yahoo rate-limited este ticker ({ticker}). Probá de nuevo en 30s. Detalle: {last_err}")

        price = safe_get(info, "currentPrice") or safe_get(info, "regularMarketPrice") or safe_get(info, "previousClose", 0)
        high52 = safe_get(info, "fiftyTwoWeekHigh", 0)
        low52 = safe_get(info, "fiftyTwoWeekLow", 0)
        market_cap = safe_get(info, "marketCap", 0)

        roe = safe_get(info, "returnOnEquity")
        roe_pct = roe * 100 if roe else None
        roa = safe_get(info, "returnOnAssets")
        roic_pct = roa * 100 if roa else None
        profit_margin = safe_get(info, "profitMargins")
        pm_pct = profit_margin * 100 if profit_margin else None
        debt_to_equity = safe_get(info, "debtToEquity")
        de_ratio = debt_to_equity / 100 if debt_to_equity else 0
        payout = safe_get(info, "payoutRatio")
        payout_pct = payout * 100 if payout else 0
        eps_5y = safe_get(info, "earningsGrowth")
        eps_5y_pct = eps_5y * 100 if eps_5y else None
        pe = safe_get(info, "trailingPE") or safe_get(info, "forwardPE")
        eps = safe_get(info, "trailingEps")

        fcf_data = []
        try:
            cf = t.cashflow
            if cf is not None and not cf.empty:
                if "Free Cash Flow" in cf.index:
                    fcf_series = cf.loc["Free Cash Flow"]
                    for date, value in fcf_series.items():
                        if pd.notna(value):
                            fv = float(value)
                            fcf_data.append({"year": str(date.year), "fcf_usd": fv, "fcf_m": fv / 1e6})
        except Exception:
            pass

        fcf_base_m = fcf_data[0]["fcf_m"] if fcf_data else 0
        total_cash = safe_get(info, "totalCash", 0) or 0
        total_debt = safe_get(info, "totalDebt", 0) or 0
        net_cash_m = (total_cash - total_debt) / 1e6
        shares = safe_get(info, "sharesOutstanding", 0) or 0
        shares_m = shares / 1e6 if shares else 0
        div_yield = safe_get(info, "dividendYield")
        if div_yield is not None:
            div_yield_pct = div_yield * 100 if div_yield < 1 else div_yield
        else:
            div_yield_pct = 0
        div_rate = safe_get(info, "dividendRate", 0)

        if market_cap >= 200e9: cap_class = "MEGA-CAP"
        elif market_cap >= 10e9: cap_class = "LARGE-CAP"
        elif market_cap >= 2e9: cap_class = "MID-CAP"
        elif market_cap >= 300e6: cap_class = "SMALL-CAP"
        else: cap_class = "MICRO-CAP"

        result = {
            "ticker": ticker, "timestamp": datetime.now().isoformat(),
            "name": safe_get(info, "longName") or safe_get(info, "shortName") or ticker,
            "sector": safe_get(info, "sector", "N/D"),
            "industry": safe_get(info, "industry", "N/D"),
            "country": safe_get(info, "country", "N/D"),
            "exchange": safe_get(info, "fullExchangeName") or safe_get(info, "exchange", "N/D"),
            "cap_class": cap_class, "website": safe_get(info, "website"),
            "price": price, "high_52w": high52, "low_52w": low52,
            "market_cap": market_cap, "market_cap_fmt": fmt_money(market_cap),
            "roe_pct": roe_pct, "roic_pct": roic_pct, "profit_margin_pct": pm_pct,
            "debt_equity": de_ratio, "payout_pct": payout_pct, "eps_growth_pct": eps_5y_pct,
            "pe": pe, "eps": eps, "fcf_base_m": fcf_base_m,
            "fcf_history": fcf_data[:5], "shares_outstanding_m": shares_m,
            "net_cash_m": net_cash_m, "total_cash": total_cash, "total_debt": total_debt,
            "dividend_yield_pct": div_yield_pct, "dividend_rate": div_rate,
            "beta": safe_get(info, "beta"), "pb_ratio": safe_get(info, "priceToBook"),
            "recommendation": safe_get(info, "recommendationKey", "none"),
            "target_mean": safe_get(info, "targetMeanPrice"),
            "analyst_count": safe_get(info, "numberOfAnalystOpinions", 0),
            "business_summary": (safe_get(info, "longBusinessSummary", "") or "")[:500]
        }
        _cache_put(_stock_cache, ticker, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Error procesando {ticker}: {str(e)}")

@app.get("/news/{ticker}")
def get_news(ticker: str, limit: int = 5):
    ticker = ticker.upper().strip()
    cache_key = f"{ticker}:{limit}"
    cached = _cache_get(_news_cache, cache_key)
    if cached is not None:
        return cached
    try:
        t = _ticker(ticker)
        news = t.news or []
        result_list = []
        for n in news[:limit]:
            result_list.append({"title": n.get("title", ""), "publisher": n.get("publisher", ""),
                          "link": n.get("link", ""), "published": n.get("providerPublishTime")})
        payload = {"ticker": ticker, "news": result_list}
        _cache_put(_news_cache, cache_key, payload)
        return payload
    except Exception as e:
        return {"ticker": ticker, "news": [], "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
