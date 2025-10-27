import asyncio
import json
import os
import re
import pytz
import random
from datetime import datetime, timezone
from aiohttp import ClientSession, ClientTimeout, ClientResponseError, BasicAuth
from aiohttp_socks import ProxyConnector
from colorama import Fore, Style, init as colorama_init

# init colorama (agar warna jalan di Windows juga)
colorama_init(autoreset=True)

# zona waktu WIB
WIB = pytz.timezone('Asia/Jakarta')

# header HEAD google (disalin dari request kamu)
GOOGLE_HEADERS = {
    "accept": "*/*",
    "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "priority": "u=1, i",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "no-cors",
    "sec-fetch-site": "none",
    "sec-fetch-storage-access": "active",
    "x-browser-channel": "stable",
    "x-browser-copyright": "Copyright 2025 Google LLC. All rights reserved.",
    "x-browser-validation": "GmxFHkay2DZYmUuquumNHEHyU78=",
    "x-browser-year": "2025",
    "x-client-data": "CI+2yQEIo7bJAQipncoBCNDvygEIlqHLAQi5pMsBCIegzQEIjo7PAQ=="
}

# fallback bearer token dari log kamu
FALLBACK_BEARER = (
    "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9."
    "eyJpc3MiOiJodHRwczovL25hbXNvLm5ldHdvcmsiLCJpYXQiOjE3NjE0ODMxNzgs"
    "ImV4cCI6MTc2MTQ4Njc3OCwiZW1haWwiOiJmb3hjb21qYXlhMTBAZ21haWwuY29t"
    "Iiwic2VudHJ5X2lkIjoiNzFNOC1LODE1LUo5UzEiLCJzZXNzaW9uX2lkIjoiM2Fi"
    "YmM0YjdhNDA3NDFjZjA2NzI1M2JiYzE1NDMwY2IifQ."
    "BAJOCkyyOxrsN0tFJU3euCmlYMAM-oicDU6T1kr6fVo"
)


class NamsoBot:
    def __init__(self):
        self.BASE_API = "https://sentry-api.namso.network/devv/api"
        self.accounts = []  # list of (email, password)
        self.proxies = []   # list of proxy strings
        self.proxy_index = 0
        self.account_proxy_map = {}  # email -> proxy str
        self.use_proxy = False
        self.rotate_bad_proxy = False

    # ---------- LOGGING / OUTPUT ----------

    def ts(self):
        return datetime.now().astimezone(WIB).strftime('%x %X %Z')

    def log(self, msg):
        print(
            f"{Fore.CYAN + Style.BRIGHT}[ {self.ts()} ]{Style.RESET_ALL}"
            f"{Fore.WHITE + Style.BRIGHT} | {Style.RESET_ALL}{msg}",
            flush=True
        )

    def mask_email(self, email):
        if "@" not in email:
            return email
        local, domain = email.split("@", 1)
        if len(local) <= 6:
            # contoh "abc***xyz"
            masked_local = local[0:1] + "***" + local[-1:]
        else:
            masked_local = local[:3] + "***" + local[-3:]
        return masked_local + "@" + domain

    def status_line(self, email, proxy, color, message):
        self.log(
            f"{Fore.CYAN + Style.BRIGHT}[ Account:{Style.RESET_ALL}"
            f"{Fore.WHITE + Style.BRIGHT} {self.mask_email(email)} {Style.RESET_ALL}"
            f"{Fore.MAGENTA + Style.BRIGHT}-{Style.RESET_ALL}"
            f"{Fore.CYAN + Style.BRIGHT} Proxy:{Style.RESET_ALL}"
            f"{Fore.WHITE + Style.BRIGHT} {proxy if proxy else 'NONE'} {Style.RESET_ALL}"
            f"{Fore.MAGENTA + Style.BRIGHT} - {Style.RESET_ALL}"
            f"{Fore.CYAN + Style.BRIGHT}Status:{Style.RESET_ALL}"
            f"{color + Style.BRIGHT} {message} {Style.RESET_ALL}"
            f"{Fore.CYAN + Style.BRIGHT}]{Style.RESET_ALL}"
        )

    def welcome(self):
        print(
            f"""
{Fore.GREEN + Style.BRIGHT}Namso Auto BOT
{Fore.YELLOW + Style.BRIGHT}<WATERMARK>"""
        )

    # ---------- FILE INPUT ----------

    def load_accounts(self):
        """
        Sumber: akun.txt
        Format tiap baris: email:password
        """
        filename = "akun.txt"
        if not os.path.exists(filename):
            self.log(f"{Fore.RED}File {filename} tidak ditemukan{Style.RESET_ALL}")
            return

        loaded = []
        with open(filename, "r", encoding="utf-8") as f:
            for line in f.read().splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                email, pwd = line.split(":", 1)
                email = email.strip()
                pwd = pwd.strip()
                if email and pwd:
                    loaded.append((email, pwd))

        self.accounts = loaded
        self.log(
            f"{Fore.GREEN + Style.BRIGHT}Total akun: {Style.RESET_ALL}"
            f"{Fore.WHITE + Style.BRIGHT}{len(self.accounts)}{Style.RESET_ALL}"
        )

    async def load_proxies(self):
        """
        proxy.txt format:
        satu proxy per baris
        support:
          http://user:pass@host:port
          http://host:port
          socks4://user:pass@host:port
          socks5://host:port
        """
        filename = "proxy.txt"
        if not os.path.exists(filename):
            self.log(f"{Fore.YELLOW + Style.BRIGHT}File {filename} tidak ditemukan. Jalan tanpa proxy.{Style.RESET_ALL}")
            self.proxies = []
            return

        with open(filename, "r", encoding="utf-8") as f:
            self.proxies = [ln.strip() for ln in f.read().splitlines() if ln.strip()]

        if not self.proxies:
            self.log(f"{Fore.YELLOW + Style.BRIGHT}proxy.txt kosong. Jalan tanpa proxy.{Style.RESET_ALL}")
        else:
            self.log(
                f"{Fore.GREEN + Style.BRIGHT}Total proxy: {Style.RESET_ALL}"
                f"{Fore.WHITE + Style.BRIGHT}{len(self.proxies)}{Style.RESET_ALL}"
            )

    # ---------- PROXY MANAGEMENT ----------

    def ensure_scheme(self, proxy_str):
        """
        Tambah http:// kalau user cuma kasih host:port
        """
        if re.match(r"^(https?://|socks4://|socks5://)", proxy_str):
            return proxy_str
        return "http://" + proxy_str

    def assign_proxy_to_account(self, email):
        """
        Ambil proxy berikutnya dari pool untuk akun ini.
        """
        if email not in self.account_proxy_map:
            if not self.proxies:
                self.account_proxy_map[email] = None
            else:
                proxy = self.ensure_scheme(self.proxies[self.proxy_index])
                self.account_proxy_map[email] = proxy
                self.proxy_index = (self.proxy_index + 1) % len(self.proxies)
        return self.account_proxy_map[email]

    def rotate_proxy_for_account(self, email):
        """
        Paksa akun ganti proxy ke proxy selanjutnya.
        """
        if not self.proxies:
            self.account_proxy_map[email] = None
        else:
            proxy = self.ensure_scheme(self.proxies[self.proxy_index])
            self.account_proxy_map[email] = proxy
            self.proxy_index = (self.proxy_index + 1) % len(self.proxies)
        return self.account_proxy_map[email]

    def build_proxy_config(self, proxy_str):
        """
        Return (connector, proxy, proxy_auth) sesuai tipe proxy.
        aiohttp rules:
          - SOCKS: pakai ProxyConnector
          - HTTP(S): pakai parameter proxy=..., proxy_auth=BasicAuth(...) jika ada user:pass
        """
        if not proxy_str:
            return None, None, None

        if proxy_str.startswith("socks4://") or proxy_str.startswith("socks5://"):
            connector = ProxyConnector.from_url(proxy_str)
            return connector, None, None

        # http://user:pass@host:port atau http://host:port
        if proxy_str.startswith("http://") or proxy_str.startswith("https://"):
            m = re.match(r"^(https?://)([^:@]+):([^@]+)@(.+)$", proxy_str)
            if m:
                scheme, user, pwd, host_port = m.groups()
                clean_url = scheme + host_port  # buang user:pass dari URL untuk aiohttp proxy=...
                auth = BasicAuth(user, pwd)
                return None, clean_url, auth
            else:
                # tidak ada auth
                return None, proxy_str, None

        raise RuntimeError("Proxy format tidak dikenal: " + str(proxy_str))

    # ---------- HTTP HELPERS ----------

    async def do_get(self, session: ClientSession, url, headers=None,
                     proxy_cfg=None, timeout=30):
        connector, proxy_url, proxy_auth = proxy_cfg if proxy_cfg else (None, None, None)
        async with ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=timeout)
        ) as local_sess:
            async with local_sess.get(url, headers=headers,
                                      proxy=proxy_url, proxy_auth=proxy_auth) as resp:
                text = await resp.text()
                return resp.status, text, resp

    async def do_post(self, session: ClientSession, url, headers=None,
                      data=None, proxy_cfg=None, timeout=30):
        connector, proxy_url, proxy_auth = proxy_cfg if proxy_cfg else (None, None, None)
        async with ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=timeout)
        ) as local_sess:
            async with local_sess.post(url, headers=headers, data=data,
                                       proxy=proxy_url, proxy_auth=proxy_auth) as resp:
                text = await resp.text()
                return resp.status, text, resp

    async def do_head(self, session: ClientSession, url, headers=None,
                      proxy_cfg=None, timeout=30):
        connector, proxy_url, proxy_auth = proxy_cfg if proxy_cfg else (None, None, None)
        async with ClientSession(
            connector=connector,
            timeout=ClientTimeout(total=timeout)
        ) as local_sess:
            async with local_sess.head(url, headers=headers,
                                       proxy=proxy_url, proxy_auth=proxy_auth) as resp:
                # HEAD no body
                return resp.status, "", resp

    # ---------- TOKEN EXTRACTION ----------

    def extract_bearer_token(self, raw_text):
        """
        Kita coba parse JSON dari connectAuth untuk ambil token.
        Kalau gagal, fallback ke token hardcode.
        """
        try:
            js = json.loads(raw_text)
            if isinstance(js, dict):
                if "Authorization" in js and isinstance(js["Authorization"], str):
                    return js["Authorization"]
                if "token" in js and isinstance(js["token"], str):
                    return "Bearer " + js["token"]
                if "access_token" in js and isinstance(js["access_token"], str):
                    return "Bearer " + js["access_token"]
        except Exception:
            pass
        return FALLBACK_BEARER

    # ---------- FLOW PER AKUN ----------

    async def run_cycle_for_account(self, email, password):
        """
        Jalankan full flow sesuai request kamu, lalu ulang terus.
        Ini loop tanpa batas buat akun ini.
        """
        while True:
            # siapkan proxy utk akun ini
            proxy_str = self.assign_proxy_to_account(email) if self.use_proxy else None
            proxy_cfg = self.build_proxy_config(proxy_str)

            # 1. connectAuth (POST)
            connect_auth_url = f"{self.BASE_API}/connectAuth"
            connect_auth_headers = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "content-type": "application/json",
                "sec-ch-ua": "\"Google Chrome\";v=\"141\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"141\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "\"Linux\"",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            connect_payload = json.dumps({"email": email, "password": password})

            try:
                status_auth, text_auth, _ = await self.do_post(
                    None, connect_auth_url,
                    headers=connect_auth_headers,
                    data=connect_payload,
                    proxy_cfg=proxy_cfg,
                    timeout=60
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"connectAuth ERR {e}")
                if self.rotate_bad_proxy and self.use_proxy:
                    proxy_str = self.rotate_proxy_for_account(email)
                await asyncio.sleep(5)
                continue

            self.status_line(email, proxy_str,
                             Fore.GREEN if 200 <= status_auth < 300 else Fore.YELLOW,
                             f"connectAuth {status_auth}")
            bearer_token = self.extract_bearer_token(text_auth)

            # 2. GET https://api.ipify.org/?format=json
            ipify_url = "https://api.ipify.org/?format=json"
            ipify_headers = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "priority": "u=1, i",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            try:
                st_ipify, tx_ipify, _ = await self.do_get(
                    None, ipify_url,
                    headers=ipify_headers,
                    proxy_cfg=proxy_cfg,
                    timeout=30
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"ipify ERR {e}")
                st_ipify, tx_ipify = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_ipify < 300 else Fore.YELLOW,
                                 f"ipify {st_ipify} {tx_ipify[:80]}")

            # 3. HEAD https://www.google.com/
            try:
                st_g_pre, _, _ = await self.do_head(
                    None, "https://www.google.com/",
                    headers=GOOGLE_HEADERS,
                    proxy_cfg=proxy_cfg,
                    timeout=30
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"google HEAD pre ERR {e}")
                st_g_pre = None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_g_pre < 300 else Fore.YELLOW,
                                 f"google HEAD pre {st_g_pre}")

            # 4. GET ipinfo.io/<IP>/json
            ipinfo_url = "https://ipinfo.io/114.8.231.206/json"
            ipinfo_headers = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "priority": "u=1, i",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            try:
                st_ipinfo, tx_ipinfo, _ = await self.do_get(
                    None, ipinfo_url,
                    headers=ipinfo_headers,
                    proxy_cfg=proxy_cfg,
                    timeout=30
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"ipinfo ERR {e}")
                st_ipinfo, tx_ipinfo = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_ipinfo < 300 else Fore.YELLOW,
                                 f"ipinfo {st_ipinfo} {tx_ipinfo[:80]}")

            # 5. GET restcountries.com/v3.1/alpha/ID
            try:
                st_country, tx_country, _ = await self.do_get(
                    None, "https://restcountries.com/v3.1/alpha/ID",
                    headers={},  # browser kirim referrer kosong. requests: header kosong ok.
                    proxy_cfg=proxy_cfg,
                    timeout=30
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"restcountries ERR {e}")
                st_country, tx_country = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_country < 300 else Fore.YELLOW,
                                 f"restcountries {st_country} {tx_country[:80]}")

            # 6. HEAD google lagi
            try:
                st_g_mid, _, _ = await self.do_head(
                    None, "https://www.google.com/",
                    headers=GOOGLE_HEADERS,
                    proxy_cfg=proxy_cfg,
                    timeout=30
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"google HEAD mid ERR {e}")
                st_g_mid = None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_g_mid < 300 else Fore.YELLOW,
                                 f"google HEAD mid {st_g_mid}")

            # 7. POST taskSubmit
            task_submit_url = f"{self.BASE_API}/taskSubmit"
            task_submit_headers_post = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "authorization": bearer_token,
                "content-type": "application/json",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            task_submit_payload = json.dumps({"email": email})
            try:
                st_taskP, tx_taskP, _ = await self.do_post(
                    None, task_submit_url,
                    headers=task_submit_headers_post,
                    data=task_submit_payload,
                    proxy_cfg=proxy_cfg,
                    timeout=60
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"taskSubmit POST ERR {e}")
                st_taskP, tx_taskP = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_taskP < 300 else Fore.YELLOW,
                                 f"taskSubmit POST {st_taskP} {tx_taskP[:80]}")

            # 8. HEAD google after POST
            try:
                st_g_after, _, _ = await self.do_head(
                    None, "https://www.google.com/",
                    headers=GOOGLE_HEADERS,
                    proxy_cfg=proxy_cfg,
                    timeout=30
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"google HEAD aft ERR {e}")
                st_g_after = None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_g_after < 300 else Fore.YELLOW,
                                 f"google HEAD aft {st_g_after}")

            # 9. GET taskSubmit
            task_submit_headers_get = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "authorization": bearer_token,
                "sec-ch-ua": "\"Google Chrome\";v=\"141\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"141\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "\"Linux\"",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            try:
                st_taskG, tx_taskG, _ = await self.do_get(
                    None, task_submit_url,
                    headers=task_submit_headers_get,
                    proxy_cfg=proxy_cfg,
                    timeout=60
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"taskSubmit GET ERR {e}")
                st_taskG, tx_taskG = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_taskG < 300 else Fore.YELLOW,
                                 f"taskSubmit GET {st_taskG} {tx_taskG[:80]}")

            # 10. GET fetchStatus
            fetch_status_url = f"{self.BASE_API}/fetchStatus"
            fetch_status_headers = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "authorization": bearer_token,
                "sec-ch-ua": "\"Google Chrome\";v=\"141\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"141\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "\"Linux\"",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            try:
                st_fetch, tx_fetch, _ = await self.do_get(
                    None, fetch_status_url,
                    headers=fetch_status_headers,
                    proxy_cfg=proxy_cfg,
                    timeout=60
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"fetchStatus GET ERR {e}")
                st_fetch, tx_fetch = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_fetch < 300 else Fore.YELLOW,
                                 f"fetchStatus {st_fetch} {tx_fetch[:80]}")

            # 11. POST healthCheck
            health_check_url = f"{self.BASE_API}/healthCheck"
            health_check_headers = {
                "accept": "*/*",
                "accept-language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                "authorization": bearer_token,
                "sec-ch-ua": "\"Google Chrome\";v=\"141\", \"Not?A_Brand\";v=\"8\", \"Chromium\";v=\"141\"",
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": "\"Linux\"",
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "none",
                "sec-fetch-storage-access": "active"
            }
            try:
                st_health, tx_health, _ = await self.do_post(
                    None, health_check_url,
                    headers=health_check_headers,
                    data=None,
                    proxy_cfg=proxy_cfg,
                    timeout=60
                )
            except Exception as e:
                self.status_line(email, proxy_str, Fore.RED, f"healthCheck POST ERR {e}")
                st_health, tx_health = None, None
            else:
                self.status_line(email, proxy_str,
                                 Fore.GREEN if 200 <= st_health < 300 else Fore.YELLOW,
                                 f"healthCheck {st_health} {tx_health[:80]}")

            # 12. HEAD google spam berulang (mirror traffic browser looping)
            for i in range(20):
                try:
                    st_head, _, _ = await self.do_head(
                        None, "https://www.google.com/",
                        headers=GOOGLE_HEADERS,
                        proxy_cfg=proxy_cfg,
                        timeout=30
                    )
                    self.status_line(
                        email,
                        proxy_str,
                        Fore.WHITE if 200 <= st_head < 300 else Fore.YELLOW,
                        f"google HEAD spam {i+1} -> {st_head}"
                    )
                except Exception as e:
                    self.status_line(email, proxy_str, Fore.RED, f"google HEAD spam ERR {e}")

            # cooldown antarfarming
            await asyncio.sleep(5)

    # ---------- PRE-RUN INTERACTIVE OPTIONS ----------

    def ask_mode(self):
        # pilih pakai proxy atau tidak
        while True:
            print(Fore.WHITE + Style.BRIGHT + "1. Run With Proxy" + Style.RESET_ALL)
            print(Fore.WHITE + Style.BRIGHT + "2. Run Without Proxy" + Style.RESET_ALL)
            raw = input(
                Fore.BLUE + Style.BRIGHT + "Choose [1/2] -> " + Style.RESET_ALL
            ).strip()
            if raw in ("1", "2"):
                self.use_proxy = (raw == "1")
                break
            print(Fore.RED + Style.BRIGHT + "Please enter either 1 or 2." + Style.RESET_ALL)

        if self.use_proxy:
            while True:
                raw2 = input(
                    Fore.BLUE + Style.BRIGHT +
                    "Rotate Invalid Proxy? [y/n] -> " +
                    Style.RESET_ALL
                ).strip().lower()
                if raw2 in ("y", "n"):
                    self.rotate_bad_proxy = (raw2 == "y")
                    break
                print(Fore.RED + Style.BRIGHT + "Invalid input. Enter 'y' or 'n'." + Style.RESET_ALL)

    # ---------- MAIN ----------

    async def main(self):
        # load akun
        self.load_accounts()
        if not self.accounts:
            self.log(Fore.RED + Style.BRIGHT + "No accounts loaded." + Style.RESET_ALL)
            return

        # tanya mode proxy
        self.ask_mode()

        # kalau mode proxy aktif, load proxy pool
        if self.use_proxy:
            await self.load_proxies()

        # banner
        os.system('cls' if os.name == 'nt' else 'clear')
        self.welcome()
        self.log(
            f"{Fore.GREEN + Style.BRIGHT}Account's Total: {Style.RESET_ALL}"
            f"{Fore.WHITE + Style.BRIGHT}{len(self.accounts)}{Style.RESET_ALL}"
        )
        self.log(Fore.CYAN + Style.BRIGHT + "=" * 75 + Style.RESET_ALL)
        self.log(
            Fore.YELLOW + Style.BRIGHT +
            "WARNING: looping tanpa batas. Risiko ban IP / suspend akun tinggi." +
            Style.RESET_ALL
        )

        # spawn task async per akun
        tasks = []
        for (email, pwd) in self.accounts:
            tasks.append(asyncio.create_task(self.run_cycle_for_account(email, pwd)))

        await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        bot = NamsoBot()
        asyncio.run(bot.main())
    except KeyboardInterrupt:
        print(
            f"{Fore.CYAN + Style.BRIGHT}[ {datetime.now().astimezone(WIB).strftime('%x %X %Z')} ]{Style.RESET_ALL}"
            f"{Fore.WHITE + Style.BRIGHT} | {Style.RESET_ALL}"
            f"{Fore.RED + Style.BRIGHT}[ EXIT ] Namso - BOT{Style.RESET_ALL}      ",
        )
