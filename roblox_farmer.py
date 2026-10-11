#!/usr/bin/env python3
# roblox_farmer.py — clean termux version
# deps: requests pillow numpy pytesseract
# system: tesseract

import os
import re
import io
import json
import time
import math
import base64
import random
import string
import requests
import numpy as np
import pytesseract
from PIL import Image, ImageFilter, ImageOps

# ─────────────────────────────────────────────
CONFIG = {
    "proxies": [],
    "accounts_file": "accounts.txt",
    "user_agent": "Mozilla/5.0 (Linux; Android 13; SM-S908U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "arkose_pubkey": "476068BF-9607-4799-B53D-966BE98E2B81",
    "delay_create": (10, 22),
    "delay_follow": (2, 6),
    "delay_answer": (2.5, 4.5),
    "max_retries": 3,
}

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; D = "\033[0m"
def ok(s):   print(f"{G}[+]{D} {s}")
def err(s):  print(f"{R}[-]{D} {s}")
def info(s): print(f"{B}[*]{D} {s}")
def warn(s): print(f"{Y}[!]{D} {s}")

# ─────────────────────────────────────────────
# CAPTCHA — rotation via PIL only
# ─────────────────────────────────────────────
def _to_gray_array(img):
    return np.asarray(img.convert("L"), dtype=np.uint8)

def _edge_score(gray):
    img = Image.fromarray(gray)
    edges = img.filter(ImageFilter.FIND_EDGES)
    arr = np.asarray(edges, dtype=np.float32)
    h, w = arr.shape
    # center vertical strip
    strip = arr[:, w // 3: 2 * w // 3].sum()
    # top-half center column bonus (arrow "up")
    top = arr[: h // 2, w // 2 - 8: w // 2 + 8].sum()
    return strip + top * 0.7

def solve_rotation(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except Exception as e:
        err(f"open img: {e}")
        return 0

    # crop center square
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    img = img.crop((left, top, left + s, top + s))

    best_angle, best_score = 0, -1.0
    for angle in range(0, 360, 3):
        rot = img.rotate(angle, resample=Image.BILINEAR, expand=False)
        arr = _to_gray_array(rot)
        score = _edge_score(arr)
        if score > best_score:
            best_score = score
            best_angle = angle
    return best_angle

def solve_text(img_bytes, charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
    except Exception:
        return ""
    img = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    img = img.point(lambda x: 0 if x < 128 else 255, "1")
    img = img.filter(ImageFilter.MedianFilter(3))
    results = []
    for psm in ("--psm 8", "--psm 7", "--psm 13"):
        try:
            t = pytesseract.image_to_string(img, config=f"{psm} -c tessedit_char_whitelist={charset}").strip()
            t = re.sub(r"[^A-Z0-9]", "", t.upper())
            if t: results.append(t)
        except Exception:
            pass
    results.sort(key=len, reverse=True)
    return results[0] if results else ""

# ─────────────────────────────────────────────
# ARKOSE
# ─────────────────────────────────────────────
class Arkose:
    API = "https://client-api.arkoselabs.com"

    def __init__(self, session, site, pubkey):
        self.s = session
        self.site = site
        self.pubkey = pubkey

    def solve(self):
        for attempt in range(1, CONFIG["max_retries"] + 1):
            info(f"arkose attempt {attempt}/{CONFIG['max_retries']}")
            tok = self._once()
            if tok:
                return tok
            time.sleep(random.uniform(1.5, 3.0))
        return None

    def _once(self):
        # 1. init
        try:
            r = self.s.post(
                f"{self.API}/fc/gt2/public_key/{self.pubkey}",
                data={"public_key": self.pubkey, "site": self.site},
                timeout=25,
            )
            j = r.json()
        except Exception as e:
            err(f"init: {e}")
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
                timeout=25,
            )
            data = r.json()
        except Exception as e:
            err(f"getchallenge: {e}")
            return None

        payload = data.get("challenge", data)
        img_b64 = payload.get("imageData") or payload.get("image")

        if not img_b64:
            err("no image in challenge")
            return None

        try:
            raw = base64.b64decode(img_b64.split(",")[-1])
        except Exception as e:
            err(f"b64 decode: {e}")
            return None

        angle = solve_rotation(raw)
        ok(f"rotation guess → {angle}°")

        time.sleep(random.uniform(*CONFIG["delay_answer"]))

        try:
            r = self.s.post(
                f"{self.API}/fc/caas/answer",
                json={
                    "public_key": self.pubkey,
                    "challengeID": cid,
                    "answer": json.dumps({"angle": angle}),
                },
                timeout=25,
            )
            resp = r.json()
        except Exception as e:
            err(f"answer: {e}")
            return None

        if resp.get("solved") and resp.get("token"):
            return resp["token"]
        err(f"not solved: {str(resp)[:120]}")
        return None

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def rs(n):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def gen_user():
    return f"{rs(random.randint(5, 7))}{random.randint(100, 99999)}"

def gen_pass():
    return f"{rs(3).upper()}{random.randint(1000, 9999)}{rs(2).lower()}!"

def gen_bday():
    return (random.randint(1982, 2003), random.randint(1, 12), random.randint(1, 28))

def pick_proxy():
    return random.choice(CONFIG["proxies"]) if CONFIG["proxies"] else None

def session(proxy=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": CONFIG["user_agent"],
        "Accept-Language": "en-US,en;q=0.9",
    })
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s

def sleep(rng):
    time.sleep(random.uniform(*rng))

def get_csrf(s, url="https://www.roblox.com/home"):
    try:
        s.get(url, timeout=20)
    except Exception:
        pass
    return s.cookies.get("XSRF-TOKEN")

# ─────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────
def create_one():
    proxy = pick_proxy()
    s = session(proxy)
    if proxy:
        info(f"proxy {proxy.split('@')[-1]}")

    username = gen_user()
    password = gen_pass()
    y, m, d = gen_bday()

    try:
        s.get("https://www.roblox.com/", timeout=25)
        s.get("https://www.roblox.com/account/signupredir", timeout=25)
    except Exception as e:
        err(f"warmup: {e}")
        return None

    a = Arkose(s, "https://www.roblox.com/account/signupredir", CONFIG["arkose_pubkey"])
    token = a.solve()
    if not token:
        err("captcha failed")
        return None
    ok("captcha token")

    csrf = get_csrf(s, "https://www.roblox.com/account/signupredir")
    payload = {
        "username": username,
        "password": password,
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
            headers={"X-CSRF-TOKEN": csrf or "", "Referer": "https://www.roblox.com/account/signupredir"},
            timeout=30,
            allow_redirects=False,
        )
    except Exception as e:
        err(f"signup: {e}")
        return None

    if r.status_code not in (200, 302):
        err(f"signup http {r.status_code}")
        return None

    time.sleep(random.uniform(2, 4))
    cookie = login(s, username, password)
    if not cookie:
        err(f"{username}: created, login failed")
        return None

    ok(f"created {username} / {password}")
    return username, password, cookie

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
        err(f"login: {e}")
        return None
    return s.cookies.get(".ROBLOSECURITY")

# ─────────────────────────────────────────────
# FOLLOW
# ─────────────────────────────────────────────
def follow_one(target, cookie):
    proxy = pick_proxy()
    s = session(proxy)
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

# ─────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────
def cmd_create(n):
    n = min(n, 50)
    made = 0
    for i in range(1, n + 1):
        print(f"\n{B}── create {i}/{n} ──{D}")
        try:
            res = create_one()
        except Exception as e:
            err(f"crash: {e}")
            res = None
        if res:
            save(*res)
            made += 1
        if i < n:
            sleep(CONFIG["delay_create"])
    ok(f"{made}/{n} created")

def cmd_follow(target, limit=None):
    accs = load()
    if not accs:
        err("no accounts. /create first.")
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
        sleep(CONFIG["delay_follow"])
    ok(f"{hit}/{len(accs)} follows → {target}")

def cmd_list():
    accs = load()
    if not accs:
        info("empty")
        return
    for i, a in enumerate(accs, 1):
        print(f"  {i:>4}. {a['user']}")

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
BANNER = f"""{G}
╔══════════════════════════════════════╗
║   roblox_farmer  —  /create /follow  ║
║   termux · no opencv · no whisper    ║
╚══════════════════════════════════════╝
{D}"""

def main():
    print(BANNER)
    info("type /help")

    while True:
        try:
            raw = input(f"{G}>{D} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        parts = raw.split()
        cmd = parts[0].lower()

        if cmd in ("/quit", "/exit", "/q"):
            break
        elif cmd == "/help":
            print("commands: /create N | /follow USER [N] | /list | /clear | /quit")
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
        elif cmd == "/clear":
            if os.path.exists(CONFIG["accounts_file"]):
                os.remove(CONFIG["accounts_file"])
            ok("cleared")
        else:
            err(f"unknown: {cmd}")

if __name__ == "__main__":
    main()
