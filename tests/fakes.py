"""Offline fakes shared by the tests (and the crash-test subprocess)."""
import time
import war_room_bot as wr


def mk_rows(n, tf, now_ms, start=100.0, step=0.0, vol=100.0):
    tfm = wr.TF_MS[tf]
    cur = now_ms // tfm * tfm
    rows = []
    for i in range(n):
        c = start + step * i
        rows.append([float(cur - (n - 1 - i) * tfm), c, c * 1.001, c * 0.999, c, vol])
    return rows


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {"ok": status == 200}

    def json(self):
        return self._body


class TGRecorder:
    """Stand-in for war_room_bot._http_post. `script` is a list of responses/exceptions used in order."""

    def __init__(self, script=None, fail=False):
        self.script = list(script or [])
        self.fail = fail
        self.payloads = []
        self.times = []

    def __call__(self, url, payload, timeout):
        self.times.append(time.time())
        if self.fail:
            raise ConnectionError(f"HTTPSConnectionPool(host='api.telegram.org'): Max retries exceeded with url: {url}")
        if self.script:
            r = self.script.pop(0)
            if isinstance(r, Exception):
                raise r
            self.payloads.append(payload)
            return r
        self.payloads.append(payload)
        return FakeResp(200, {"ok": True, "result": {}})

    def texts(self, needle=None):
        return [p["text"] for p in self.payloads if needle is None or needle in p["text"]]


class FakeSpot:
    # Default clock is pinned 30 s into the current 5m candle, so tests that step by <= 4 min never
    # accidentally cross a candle boundary (wall-clock phase made one test flaky before).
    PHASE_S = 30

    def __init__(self, name="BINANCE", pairs=None, now=None):
        self.name = name
        self.pairs = list(pairs or wr.ORIGINAL_8)
        self.base = {s: 100.0 + 10 * i for i, s in enumerate(self.pairs)}
        self.now = now if now is not None else time.time() // 300 * 300 + self.PHASE_S
        self.calls = []
        self.ob_fail = set()
        self.override = {}
        self.trade_candles = {}
        self.trade_fail = set()
        self.price = {}
        self.down = False
        self.http_429 = 0
        self.used_weight = 0
        self.markets = {s.replace("/", "") for s in self.pairs}

    def connect(self):
        if self.down:
            raise ConnectionError(f"{self.name} unreachable")

    def has(self, s):
        return s in self.pairs

    def ohlcv(self, sym, tf, limit, since=None):
        self.calls.append(("ohlcv", sym, tf, limit, since))
        if self.down:
            raise ConnectionError("down")
        if since is not None:
            if sym in self.trade_fail:
                raise TimeoutError("candles timeout")
            return [r for r in self.trade_candles.get(sym, []) if r[0] >= since]
        o = self.override.get((sym, tf))
        if o is not None:
            return o(self.now) if callable(o) else o
        return mk_rows(limit, tf, int(self.now * 1000), start=self.base.get(sym, 100.0))

    def order_book(self, sym, limit=20):
        self.calls.append(("ob", sym, limit))
        if sym in self.ob_fail:
            raise ConnectionError("order book boom")
        return {"bids": [[1.0, 10.0]] * 20, "asks": [[1.0, 10.0]] * 20}

    def prices(self, syms):
        if self.down:
            raise ConnectionError("down")
        return {s: self.price.get(s, self.base.get(s, 100.0)) for s in syms if s in self.pairs}

    def tickers_24h(self):
        return None


def failing_source(s, coins):
    raise ConnectionError("deriv source down")


def install_scores(monkeypatch, spot, scores):
    """Force score_sides results per symbol (keyed by the fake's unique base price)."""
    by_price = {round(spot.base[s]): v for s, v in scores.items()}

    def fake(r5, r1, ob_imb):
        ls, ss = by_price.get(round(float(r5["close"])), (0, 0))
        return ls, ss, ["test long reason"], ["test short reason"]
    monkeypatch.setattr(wr, "score_sides", fake)


def make_bot(cfg, spot, deriv_sources=None, ai=None):
    router = wr.SpotRouter(factories=[(spot.name, lambda: spot)])
    deriv = wr.DerivRouter(sources=deriv_sources or [("NONE", failing_source)])
    bot = wr.Bot(cfg, spot=router, deriv=deriv, ai=ai)
    bot.start()
    bot.coins = [s for s in spot.pairs]
    return bot
