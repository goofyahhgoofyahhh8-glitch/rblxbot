#!/usr/bin/env python3
# roblox_farmer v2
# accounts saved to accounts.txt AND shown in terminal on creation

import os, re, io, json, time, base64, random, string, requests
import numpy as np
import pytesseract
from PIL import Image, ImageFilter

CONFIG = {
    "proxies": [],
    "accounts_file": "accounts.txt",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "arkose_pubkey_fallback": "476068BF-9607-4799-B53D-966BE98E2B81",
    "delay_create": (10, 20),
    "delay_follow": (2, 5),
    "delay_answer": (2.0, 4.0),
    "max_retries": 5,
}

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"; C="\033[96m"; D="\033[0m"
def ok(s):   print(f"{G}[+]{D} {s}")
def err(s):  print(f"{R}[-]{D} {s}")
def info(s): print(f"{B}[*]{D} {s}")
def warn(s): print(f"{Y}[!]{D} {s}")
def head(s): print(f"{C}{s}{D}")

# ─────────────────────────────────────────────
# CAPTCHA
# ─────────────────────────────────────────────
def solve_rotation(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except Exception as e:
        err(f"img open: {e}")
        return 0
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w-s)//2, (h-s)//2, (w-s)//2 + s, (h-s)//2 + s))
    best_a, best_s = 0, -1.0
    for angle in range(0, 360, 2):
        rot = img.rotate(angle, resample=Image.BILINEAR, expand=False)
        edges = rot.filter(ImageFilter.FIND_EDGES)
        arr = np.asarray(edges, dtype=np.float32)
        hh, ww = arr.shape
        strip = arr[:, ww//3:2*ww//3].sum()
        top = arr[:hh//2, ww//2-8:ww//2+8].sum()
        score = strip + top * 0.7
        if score > best_s:
            best_s, best_a = score, angle
    return best_a

def solve_text(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except Exception:
        return ""
    img = img.resize((img.width*3, img.height*3), Image.LANCZOS)
    img = img.point(lambda x: 0 if x < 128 else 255, "1")
    img = img.filter(ImageFilter.MedianFilter(3))
    out = []
    for psm in ("--psm 8", "--psm 7", "--psm 13"):
        try:
            t = pytesseract.image_to_string(img, config=f"{psm} -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789").strip()
            t = re.sub(r"[^A-Z0-9]", "", t.upper())
            if t: out.append(t)
        except Exception:
            pass
    out.sort(key=len, reverse=True)
    return out[0] if out else ""

# ─────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────
def new_session(proxy=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": CONFIG["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s

def human_sleep(rng):
    time.sleep(random.uniform(*rng))

def rand_str(n):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def gen_username():
    return f"{rand_str(random.randint(5,7))}{random.randint(100,99999)}"

def gen_password():
    return f"{rand_str(4).upper()}{random.randint(1000,9999)}{rand_str(3)}!"

def gen_birthday():
    return (random.randint(1985, 2003), random.randint(1,12), random.randint(1,28))

def pick_proxy():
    return random.choice(CONFIG["proxies"]) if CONFIG["proxies"] else None

# ─────────────────────────────────────────────
# ARKOSE
# ─────────────────────────────────────────────
class Arkose:
    API = "https://client-api.arkoselabs.com"

    def __init__(self, session, site, pubkey):
        self.s = session
        self.site = site
        self.pubkey = pubkey

    def _hdr(self, extra=None):
        h = {
            "Origin": "https://www.roblox.com",
            "Referer": self.site,
            "User-Agent": CONFIG["user_agent"],
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }
        if extra:
            h.update(extra)
        return h

    def solve(self):
        for i in range(1, CONFIG["max_retries"] + 1):
            info(f"arkose attempt {i}/{CONFIG['max_retries']}")
            tok = self._once()
            if tok:
                return tok
            human_sleep((1.5, 3.0))
        return None

    def _once(self):
        # 1. init
        try:
            r = self.s.post(
                f"{self.API}/fc/gt2/public_key/{self.pubkey}",
                data={
                    "public_key": self.pubkey,
                    "site": self.site,
                    "userbrowser": CONFIG["user_agent"],
                    "capi_version": "1.5.5",
                    "capi_mode": "lightbox",
                    "style_theme": "default",
                    "rnd": str(random.random()),
                },
                headers=self._hdr(),
                timeout=25,
            )
            if r.status_code != 200:
                err(f"init http {r.status_code}: {r.text[:120]}")
                return None
            j = r.json()
        except Exception as e:
            err(f"init {e}")
            return None

        if j.get("error"):
            err(f"init err: {j['error']}")
            return None

        ch = j.get("challenge", {}) or {}
        cid = ch.get("challengeID") or j.get("challengeID")
        if not cid:
            err("no challenge id")
            return None

        # 2. get challenge
        try:
            r = self.s.post(
                f"{self.API}/fc/caas/getchallenge",
                json={"public_key": self.pubkey, "challengeID": cid},
                headers=self._hdr({"Content-Type": "application/json"}),
                timeout=25,
            )
            data = r.json()
        except Exception as e:
            err(f"getchallenge {e}")
            return None

        payload = data.get("challenge", data)
        img_b64 = payload.get("imageData") or payload.get("image")
        audio_b64 = payload.get("audioData") or payload.get("audio")

        if not img_b64:
            err(f"no image. keys: {list(payload.keys())[:8]}")
            return None

        try:
            raw = base64.b64decode(img_b64.split(",")[-1])
        except Exception as e:
            err(f"b64 {e}")
            return None

        angle = solve_rotation(raw)
        ok(f"rotation guess {angle}°")

        human_sleep(CONFIG["delay_answer"])

        # 3. answer
        try:
            r = self.s.post(
                f"{self.API}/fc/caas/answer",
                json={
                    "public_key": self.pubkey,
                    "challengeID": cid,
                    "answer": json.dumps({"angle": angle}),
                },
                headers=self._hdr({"Content-Type": "application/json"}),
                timeout=25,
            )
            resp = r.json()
        except Exception as e:
            err(f"answer {e}")
            return None

        if resp.get("solved") and resp.get("token"):
            return resp["token"]

        err(f"not solved: {str(resp)[:140]}")
        return None

# ─────────────────────────────────────────────
# ROBLOX
# ─────────────────────────────────────────────
def get_csrf(s, url="https://www.roblox.com/home"):
    try:
        s.get(url, timeout=20)
    except Exception:
        pass
    return s.cookies.get("XSRF-TOKEN")

def fetch_arkose_pubkey(s):
    # roblox serves the key from this endpoint
    endpoints = [
        "https://www.roblox.com/arkose/GetArkosePublicKey",
        "https://auth.roblox.com/v2/arkose-public-key",
        "https://apis.roblox.com/arkose/v1/public-key",
    ]
    for url in endpoints:
        try:
            r = s.get(url, timeout=15)
            if r.status_code == 200:
                try:
                    j = r.json()
                except Exception:
                    m = re.search(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", r.text)
                    if m:
                        ok(f"pubkey via {url}")
                        return m.group(0)
                    continue
                for k in ("publicKey", "PublicKey", "key", "pubkey", "arkosePublicKey"):
                    if j.get(k):
                        ok(f"pubkey via {url}")
                        return j[k]
        except Exception:
            continue
    warn("pubkey fetch failed, using fallback")
    return CONFIG["arkose_pubkey_fallback"]

def login(s, u, p):
    csrf = get_csrf(s, "https://www.roblox.com/login")
    try:
        s.post(
            "https://www.roblox.com/login/v1/login",
            json={"ctype": "Username", "cvalue": u, "password": p},
            headers={"X-CSRF-TOKEN": csrf or ""},
            timeout=25,
        )
    except Exception as e:
        err(f"login post: {e}")
        return None
    return s.cookies.get(".ROBLOSECURITY")

def create_one():
    proxy = pick_proxy()
    s = new_session(proxy)
    if proxy:
        info(f"proxy {proxy.split('@')[-1]}")

    u = gen_username()
    p = gen_password()
    y, m, d = gen_birthday()

    # warm up: land on roblox, get cookies, then signup page
    try:
        s.get("https://www.roblox.com/", timeout=25)
        time.sleep(random.uniform(0.8, 1.8))
        s.get("https://www.roblox.com/account/signupredir", timeout=25)
    except Exception as e:
        err(f"warmup {e}")
        return None

    pubkey = fetch_arkose_pubkey(s)
    a = Arkose(s, "https://www.roblox.com/account/signupredir", pubkey)
    token = a.solve()
    if not token:
        err("captcha failed")
        return None
    ok("captcha token acquired")

    csrf = get_csrf(s, "https://www.roblox.com/account/signupredir")
    payload = {
        "username": u,
        "password": p,
        "birthdayMonth": f"{m:02d}",
        "birthdayDay": f"{d:02d}",
        "birthdayYear": str(y),
        "gender": random.choice(["male", "female"]),
        "isTosAgreementBoxChecked": "true",
        "agreementIds": "0c6f7b3b-c7a2-4e12-a1e6-9d8b9f4d5c1a",
        "fc-token": token,
    }
    try:
        r = s.post(
            "https://www.roblox.com/account/signupredir",
            data=payload,
            headers={
                "X-CSRF-TOKEN": csrf or "",
                "Referer": "https://www.roblox.com/account/signupredir",
                "Origin": "https://www.roblox.com",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
            allow_redirects=False,
        )
    except Exception as e:
        err(f"signup {e}")
        return None

    if r.status_code not in (200, 302):
        err(f"signup http {r.status_code} body={r.text[:120]}")
        return None

    time.sleep(random.uniform(2, 4))
    cookie = login(s, u, p)
    if not cookie:
        err(f"{u}: created, login failed")
        return None

    return u, p, cookie

# ─────────────────────────────────────────────
# FOLLOW
# ─────────────────────────────────────────────
def follow_one(target, cookie):
    proxy = pick_proxy()
    s = new_session(proxy)
    s.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")

    csrf = get_csrf(s, "https://www.roblox.com/home")
    if not csrf:
        return False, "no csrf"

    try:
        r = s.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [target], "excludeBannedUsers": False},
            timeout=20,
        )
        data = r.json().get("data", [])
        if not data:
            return False, "user not found"
        uid = data[0]["id"]
    except Exception as e:
        return False, f"resolve {e}"

    try:
        r = s.post(
            f"https://friends.roblox.com/v1/users/{uid}/follow",
            headers={"X-CSRF-TOKEN": csrf, "Referer": f"https://www.roblox.com/users/{uid}/profile"},
            timeout=20,
        )
    except Exception as e:
        return False, f"follow {e}"

    if r.status_code == 200: return True, "ok"
    if r.status_code == 429: return False, "rate limited"
    if r.status_code == 403: return False, "cookie dead"
    return False, f"http {r.status_code}"

# ─────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────
def load():
    if not os.path.exists(CONFIG["accounts_file"]):
        return []
    out = []
    with open(CONFIG["accounts_file"]) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split(":", 2)
            if len(parts) == 3:
                out.append({"user": parts[0], "pass": parts[1], "cookie": parts[2]})
    return out

def save(u, p, c):
    with open(CONFIG["accounts_file"], "a") as f:
        f.write(f"{u}:{p}:{c}\n")
    # also a clean creds-only file, human readable
    with open("creds.txt", "a") as f:
        f.write(f"{u}:{p}\n")

# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────
def cmd_create(n):
    n = min(n, 100)
    made = 0
    for i in range(1, n + 1):
        head(f"\n──── create {i}/{n} ────")
        try:
            res = create_one()
        except Exception as e:
            err(f"crash: {e}")
            res = None
        if res:
            u, p, c = res
            save(u, p, c)
            made += 1
            print()
            ok(f"  ✓ ACCOUNT CREATED")
            ok(f"  username: {u}")
            ok(f"  password: {p}")
            print()
        if i < n:
            human_sleep(CONFIG["delay_create"])
    head(f"\n═══════════════════════════")
    ok(f"{made}/{n} created this batch")
    ok(f"all creds saved to: {os.path.abspath('creds.txt')}")
    head(f"═══════════════════════════")

def cmd_follow(target, limit=None):
    accs = load()
    if not accs:
        err("no accounts. run /create first.")
        return
    if limit:
        accs = accs[:limit]
    hit = 0
    for i, a in enumerate(accs, 1):
        print(f"{B}[{i}/{len(accs)}]{D} {a['user']}", end="  ")
        try:
            good, msg = follow_one(target, a["cookie"])
        except Exception as e:
            good, msg = False, str(e)
        if good:
            hit += 1
            print(f"{G}✓{D}")
        else:
            print(f"{R}✗{D} {msg}")
        human_sleep(CONFIG["delay_follow"])
    ok(f"{hit}/{len(accs)} follows landed on {target}")

def cmd_list():
    accs = load()
    if not accs:
        info("empty")
        return
    for i, a in enumerate(accs, 1):
        print(f"  {i:>4}. {a['user']} : {a['pass']}")

def cmd_creds():
    if not os.path.exists("creds.txt"):
        info("no creds yet")
        return
    print(open("creds.txt").read())

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
BANNER = f"""{G}
╔═══════════════════════════════════════════════════╗
║   roblox_farmer v2  ·  /create  /follow           ║
║   live arkose pubkey  ·  full browser headers     ║
║   creds auto-saved to creds.txt                   ║
╚═══════════════════════════════════════════════════╝
{D}"""

def main():
    print(BANNER)
    info("commands: /create N | /follow USER [N] | /list | /creds | /clear | /quit")

    while True:
        try:
            raw = input(f"{G}>{D} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw: continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ("/quit", "/exit", "/q"):
            break
        elif cmd == "/help":
            print("commands: /create N | /follow USER [N] | /list | /creds | /clear | /quit")
        elif cmd == "/create":
            n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
            cmd_create(n)
        elif cmd == "/follow":
            if len(parts) < 2:
                err("usage: /follow USERNAME [amount]")
                continue
            limit = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            cmd_follow(parts[1], limit)
        elif cmd == "/list":
            cmd_list()
        elif cmd == "/creds":
            cmd_creds()
        elif cmd == "/clear":
            for f in ("accounts.txt", "creds.txt"):
                if os.path.exists(f):
                    os.remove(f)
            ok("cleared")
        else:
            err(f"unknown: {cmd}")

if __name__ == "__main__":
    main()
    
