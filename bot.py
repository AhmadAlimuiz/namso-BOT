#!/usr/bin/env python3
# vip_prod_10m_cc.py — flow tetap, report 10m, header mirip chrome-extension

from aiohttp import ClientSession, ClientTimeout, ClientResponseError, BasicAuth
from aiohttp_socks import ProxyConnector
from colorama import Fore, Style, init as colorama_init
from datetime import datetime, timezone
import asyncio, pytz, os, json, re, time, base64, logging

# ===== Config =====
EXT_ID        = "ccdooaopgkfbikbdiekinfheklhbemcd"
URL_SHARES    = "https://sentry-api.namso.network/validator/pyld_rcv000.php"
URL_PING      = "https://app.namso.network/dashboard/api.php/ping?role=extension"
REPORT_INTERVAL = 600  # 10 menit

# ===== UA =====
try:
    from fake_useragent import FakeUserAgent
    UA = FakeUserAgent().random
except Exception:
    UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

# ===== Color/log init =====
colorama_init(autoreset=True)
WIB_TZ = pytz.timezone("Asia/Jakarta")
os.makedirs("logs", exist_ok=True)
state_logger = logging.getLogger("state"); state_logger.setLevel(logging.INFO)
event_logger = logging.getLogger("event"); event_logger.setLevel(logging.INFO)
for lg, fname in ((state_logger, "logs/state.log"), (event_logger, "logs/event.log")):
    fh = logging.FileHandler(fname); fh.setFormatter(logging.Formatter("%(asctime)s %(message)s")); lg.addHandler(fh)

def now_wib(): return datetime.now().astimezone(WIB_TZ).strftime('%x %X %Z')
def log_console(msg): print(f"{Fore.CYAN}[ {now_wib()} ]{Style.RESET_ALL}{Fore.WHITE} | {Style.RESET_ALL}{msg}{Style.RESET_ALL}", flush=True)
def log_state(msg): state_logger.info(msg)
def log_event(msg): event_logger.info(msg)
def clear(): os.system('cls' if os.name == 'nt' else 'clear')

# ===== JWT utils =====
def jwt_claims(token: str) -> dict:
    try:
        p = token.split(".")[1]
        pad = "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p + pad).decode("utf-8","ignore"))
    except Exception:
        return {}

def resolve_name(token: str, fallback: str) -> str:
    c = jwt_claims(token)
    for k in ("username","email","sentry_id"):
        v = c.get(k)
        if isinstance(v, str) and v.strip():
            return v.split("@",1)[0] if k=="email" and "@" in v else v
    return fallback

class StayVIP:
    def __init__(self):
        self.points      = {}   # idx -> last shares
        self.next_sync   = {}   # idx -> epoch
        self.last_report = {}   # idx -> epoch (untuk 10 menit)
        self.fail_count  = {}   # idx -> recent fail times
        self.proxies     = []
        self.proxy_idx   = 0
        self.acc_proxy   = {}
        self.UA          = UA

    # ---------- IO ----------
    def load_tokens(self):
        fn = "tokens.json"
        if not os.path.exists(fn):
            log_console(f"{Fore.RED}File {fn} tidak ditemukan."); return []
        raw = open(fn,"r",encoding="utf-8").read().strip()
        if not raw: return []
        accs = []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for i, it in enumerate(data):
                    if isinstance(it, dict) and "token" in it:
                        tok = it["token"].strip()
                        nm  = it.get("name") or resolve_name(tok, f"ACC-{i+1}")
                        accs.append({"name": nm, "token": tok})
                    elif isinstance(it, str):
                        tok = it.strip()
                        nm  = resolve_name(tok, f"ACC-{i+1}")
                        accs.append({"name": nm, "token": tok})
        except Exception:
            for i, line in enumerate([l.strip() for l in raw.splitlines() if l.strip()]):
                accs.append({"name": resolve_name(line, f"ACC-{i+1}"), "token": line})
        return accs

    async def load_proxies(self):
        fn = "proxy.txt"
        if not os.path.exists(fn): return
        self.proxies = [l.strip() for l in open(fn,"r",encoding="utf-8").read().splitlines() if l.strip()]
        if self.proxies:
            log_console(f"{Fore.GREEN}Proxies Total: {Fore.WHITE}{len(self.proxies)}")

    # ---------- Proxy ----------
    def _norm(self, p:str)->str:
        return p if p.startswith(("http://","https://","socks4://","socks5://")) else "http://"+p
    def get_proxy(self, idx:int):
        if not self.proxies: return None
        if idx not in self.acc_proxy:
            p = self._norm(self.proxies[self.proxy_idx]); self.acc_proxy[idx] = p
            self.proxy_idx = (self.proxy_idx + 1) % len(self.proxies)
        return self.acc_proxy[idx]
    def rotate(self, idx:int):
        if not self.proxies: return None
        p = self._norm(self.proxies[self.proxy_idx]); self.acc_proxy[idx] = p
        self.proxy_idx = (self.proxy_idx + 1) % len(self.proxies)
        return p
    def build_proxy(self, p:str|None):
        if not p: return None, None, None
        if p.startswith("socks"): return ProxyConnector.from_url(p), None, None
        m = re.match(r"http://(.*?):(.*?)@(.*)", p)
        if m:
            u,pw,host = m.groups()
            return None, f"http://{host}", BasicAuth(u,pw)
        return None, p, None

    # ---------- HTTP ----------
    async def fetch_shares(self, token:str, proxy:str|None):
        headers = {
            "Accept": "*/*",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": f"chrome-extension://{EXT_ID}",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Storage-Access": "active",
            "User-Agent": self.UA,
            "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "Authorization": f"Bearer {token}",
        }
        conn, px, auth = self.build_proxy(proxy)
        try:
            async with ClientSession(connector=conn, timeout=ClientTimeout(total=30)) as s:
                async with s.get(URL_SHARES, headers=headers, proxy=px, proxy_auth=auth) as r:
                    txt = await r.text(); st = r.status
                    if st in (401,403): return {"success":False,"error":"TOKEN INVALID","_status":st}
                    if st != 200:       return {"success":False,"error":f"HTTP {st}","_status":st,"raw":txt}
                    try: data = json.loads(txt)
                    except: return {"success":False,"error":"JSON parse error","_status":st}
                    if isinstance(data,dict) and data.get("success") is False:
                        err = str(data.get("error","invalid")).lower()
                        if any(k in err for k in ("invalid","expired","unauthor")):
                            return {"success":False,"error":"TOKEN INVALID","_status":401}
                    data["_status"] = st; return data
        except (Exception, ClientResponseError) as e:
            return {"success":False,"error":f"net:{e}","_status":0}

    async def send_ping(self, token:str, proxy:str|None):
        headers = {
            "Accept": "*/*",
            "Origin": f"chrome-extension://{EXT_ID}",
            "Content-Type": "application/json",
            "User-Agent": self.UA,
            "sec-ch-ua": '"Google Chrome";v="141", "Not?A_Brand";v="8", "Chromium";v="141"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "Authorization": f"Bearer {token}",
        }
        payload = {
            "user_id":"auto",
            "extension_id": EXT_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00","Z")
        }
        conn, px, auth = self.build_proxy(proxy)
        try:
            async with ClientSession(connector=conn, timeout=ClientTimeout(total=30)) as s:
                async with s.post(URL_PING, headers=headers, json=payload, proxy=px, proxy_auth=auth) as r:
                    _ = await r.text(); return r.status
        except Exception:
            return 0

    # ---------- Fail window ----------
    def mark_fail(self, idx:int):
        lst = self.fail_count.setdefault(idx, [])
        now = time.time(); lst.append(now)
        self.fail_count[idx] = [t for t in lst if now - t <= 120]
        return len(self.fail_count[idx])

    # ---------- Worker (flow TETAP, report tiap 10 menit) ----------
    async def worker(self, idx:int, name:str, token:str, use_proxy:bool):
        proxy = self.get_proxy(idx) if use_proxy else None
        log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: Start Monitoring {name} ]")
        log_state(f"START {name} proxy={proxy or '-'}")
        self.last_report[idx] = 0

        # prefetch pertama
        res = await self.fetch_shares(token, proxy)
        if not res or not res.get("success"):
            err = (res or {}).get("error","fail")
            if err == "TOKEN INVALID":
                log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: {Fore.RED}TOKEN INVALID{Style.RESET_ALL} ]")
                log_event(f"PRE_INVALID {name}"); return
            log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: GET shares gagal: {Fore.YELLOW}{err}{Style.RESET_ALL} ]")
            log_state(f"PRE_FAIL {name} {err}")
            self.next_sync[idx] = int(time.time()) + 15
        else:
            log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: {Fore.GREEN}SUKSES{Style.RESET_ALL} ]")
            self.points[idx]    = float(res.get("shares", 0))
            self.next_sync[idx] = int(res.get("next_sync", int(time.time()) + 600))

        while True:
            now = int(time.time())
            due = self.next_sync.get(idx, 0)
            if due > now:
                await asyncio.sleep(min(2, due - now))
                continue

            res = await self.fetch_shares(token, proxy)
            if not res or not res.get("success"):
                err = res.get("error","fail") if res else "noresp"
                if err == "TOKEN INVALID":
                    log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: {Fore.RED}TOKEN INVALID{Style.RESET_ALL} ]")
                    log_event(f"TOKEN_INVALID {name}"); return
                fails = self.mark_fail(idx)
                if use_proxy and fails >= 3:
                    proxy = self.rotate(idx); log_event(f"ROTATE_PROXY {name} fails={fails} new={proxy}")
                log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: GET shares gagal: {Fore.YELLOW}{err}{Style.RESET_ALL} ]")
                log_state(f"GET_FAIL {name} err={err}")
                self.next_sync[idx] = now + 15
                continue

            self.fail_count[idx] = []

            shares = float(res.get("shares", 0))
            server_time = int(res.get("server_time", now))
            nxt = int(res.get("next_sync", server_time + 600))
            self.next_sync[idx] = nxt

            prev = self.points.get(idx)
            self.points[idx] = shares

            # ---- REPORT HANYA TIAP 10 MENIT ----
            if now - self.last_report[idx] >= REPORT_INTERVAL:
                self.last_report[idx] = now
                log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: Shares {shares:.4f} PTS ]")
                log_state(f"SHARES {name} {shares:.4f}")

            # ---- PING hanya saat bertambah (flow asli) ----
            if prev is not None and shares > prev:
                st = await self.send_ping(token, proxy)
                if st == 200:
                    log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: {Fore.GREEN}POINT BERTAMBAH → {shares:.4f}{Style.RESET_ALL} ]")
                    log_event(f"POINT_UP {name} {shares:.4f}")
                elif st == 429:
                    log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: {Fore.YELLOW}PING rate limited. Backoff 180s{Style.RESET_ALL} ]")
                    log_event(f"PING_429 {name}"); self.next_sync[idx] = now + 180
                elif st in (401,403):
                    log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: {Fore.RED}PING auth {st}{Style.RESET_ALL} ]")
                    log_event(f"PING_AUTH {name} {st}"); self.next_sync[idx] = now + 60
                else:
                    log_console(f"[ Account: {name} - Proxy: {proxy or '-'}  - Status: PING HTTP {st} ]")
                    log_event(f"PING_HTTP {name} {st}"); self.next_sync[idx] = now + 30

            await asyncio.sleep(1)

    async def main(self):
        accounts = self.load_tokens()
        if not accounts:
            log_console(f"{Fore.RED}Tidak ada token bearer di tokens.json."); return

        use_proxy = False
        try:
            print(f"{Fore.WHITE+Style.BRIGHT}1. Run With Proxy{Style.RESET_ALL}")
            print(f"{Fore.WHITE+Style.BRIGHT}2. Run Without Proxy{Style.RESET_ALL}")
            c = int(input(f"{Fore.BLUE+Style.BRIGHT}Choose [1/2] -> {Style.RESET_ALL}").strip())
            use_proxy = (c == 1)
        except Exception:
            pass

        clear()
        print(f"\n{Fore.GREEN}STAY {Fore.BLUE}VIP{Style.RESET_ALL}\n")
        log_console(f"{Fore.GREEN}Total Accounts: {Fore.WHITE}{len(accounts)}")
        log_console(f"{Fore.CYAN}{'='*74}")

        if use_proxy: await self.load_proxies()

        tasks = [asyncio.create_task(self.worker(i, a["name"], a["token"], use_proxy))
                 for i, a in enumerate(accounts)]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(StayVIP().main())
    except KeyboardInterrupt:
        print(f"{Fore.CYAN}[ {now_wib()} ]{Style.RESET_ALL}{Fore.WHITE} | {Style.RESET_ALL}{Fore.RED}[ EXIT ] STAY VIP{Style.RESET_ALL}")
