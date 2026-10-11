#!/usr/bin/env python3
"""
roblox_farmer.py — DIY Roblox account creator + follower bot
termux-ready single file. no paid APIs.

commands:
  /create N          create N accounts (max 50 per batch)
  /follow USERNAME   follow USERNAME from all saved accounts
  /follow USERNAME N follow from first N accounts
  /list              show saved accounts
  /clear             wipe accounts.txt
  /help              this
  /quit              exit

requires:
  - residential proxies for anything past ~5 accounts
  - ~40% hit rate on arkose rotation via DIY solver
  - patience
"""

import os
import re
import sys
import json
import time
import math
import random
import string
import base64
import requests
import numpy as np
import cv2
import pytesseract

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
CONFIG = {
    # proxies — format "http://user:pass@host:port" or "socks5://..."
    "proxies": [
        # "http://user:pass@geo.iproyal.com:12321",
    ],
    "accounts_file": "accounts.txt",
    "user_agent": "Mozilla/5.0 (Linux; Android 13; SM-S908U) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "arkose_pubkey": "476068BF-9607-4799-B53D-966BE98E2B81",
    "delay_create": (10, 25),
    "delay_follow": (2, 6),
    "delay_captcha_answer": (2.5, 4.5),   # human pacing on arkose
    "max_retries_captcha": 3,
}

# ─────────────────────────────────────────────────────────────
# TERMINAL COLORS
# ─────────────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"; D = "\033[0m"
def ok(s):   print(f"{G}[+]{D} {s}")
def err(s):  print(f"{R}[-]{D} {s}")
def info(s): print(f"{B}[*]{D} {s}")
def warn(s): print(f"{Y}[!]{D} {s}")

# ─────────────────────────────────────────────────────────────
# CAPTCHA SOLVER — inline
# ─────────────────────────────────────────────────────────────
_whisper = None

def _get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        info("loading whisper-tiny (first run, ~75MB download)")
        _whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper

def _decode_img(b):
    return cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_GRAYSCALE)

def solve_rotation(img_bytes):
    """Return the rotation angle (degrees) that makes the arrow point up."""
    img = _decode_img(img_bytes)
    if img is None:
        return 0
    h, w = img.shape
    s = min(h, w)
    cy, cx = h // 2, w // 2
    img = img[cy - s//2:cy + s//2, cx - s//2:cx + s//2]

    best_angle, best_score = 0, -1
    for angle in range(0, 360, 2):
        M = cv2.getRotationMatrix2D((s//2, s//2), angle, 1.0)
        rot = cv2.warpAffine(img, M, (s, s),
                             flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        edges = cv2.Canny(rot, 80, 200)
        # center vertical strip mass
        strip = edges[:, s//3:2*s//3].sum()
        # top-half center column bonus (arrow "up")
        top = edges[: s//2, s//2 - 8 : s//2 + 8].sum()
        score = strip + top * 0.7
        if score > best_score:
            best_score, best_angle = score, angle
    return best_angle

def solve_audio(audio_bytes):
    """Whisper on arkose audio challenge. Returns digit string."""
    tmp = f"/tmp/cap_{int(time.time()*1000)}.mp3"
    with open(tmp, "wb") as f:
        f.write(audio_bytes)
    try:
        model = _get_whisper()
        segments, _ = model.transcribe(tmp, language="en", beam_size=3)
        text = " ".join(s.text for s in segments).strip()
        digits = re.findall(r"\d", text)
        return "".join(digits)
    except Exception as e:
        err(f"whisper: {e}")
        return ""
    finally:
        try: os.remove(tmp)
        except: pass

def solve_text(img_bytes, charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"):
    """OCR fallback for text captchas."""
    arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    img = cv2.resize(img, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    _, th = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(th) < 127:
        th = cv2.bitwise_not(th)
    th = cv2.medianBlur(th, 3)
    th = cv2.dilate(th, np.ones((2, 2), np.uint8), iterations=1)
    results = []
    for psm in ("--psm 8", "--psm 7", "--psm 13"):
        try:
            t = pytesseract.image_to_string(th, config=f"{psm} -c tessedit_char_whitelist={charset}").strip()
            t = re.sub(r"[^A-Z0-9]", "", t.upper())
            if t: results.append(t)
        except: pass
    results.sort(key=len, reverse=True)
    return results[0] if results else ""

# ─────────────────────────────────────────────────────────────
# ARKOSE SESSION
# ─────────────────────────────────────────────────────────────
class ArkoseSession:
    def __init__(self, session, site_url, pubkey):
        self.s = session
        self.site = site_url
        self.pubkey = pubkey
        self.api = "https://client-api.arkoselabs.com"

    def _post(self, url, **kw):
        kw.setdefault("timeout", 25)
        return self.s.post(url, **kw)

    def solve(self):
        """Try up to N times. Returns arkose token or None."""
        for attempt in range(1, CONFIG["max_retries_captcha"] + 1):
            info(f"arkose attempt {attempt}/{CONFIG['max_retries_captcha']}")
            token = self._solve_once()
            if token:
                return token
            time.sleep(random.uniform(1.5, 3.0))
        return None

    def _solve_once(self):
        # 1. init
        try:
            r = self._post(
                f"{self.api}/fc/gt2/public_key/{self.pubkey}",
                data={"public_key": self.pubkey, "site": self.site},
            )
            j = r.json()
        except Exception as e:
            err(f"arkose init: {e}")
            return None

        if j.get("error"):
            err(f"arkose init error: {j['error']}")
            return None

        challenge = j.get("challenge", {})
        cid = challenge.get("challengeID") or j.get("challengeID")
        if not cid:
            err("arkose: no challenge id")
            return None

        # 2. request a challenge
        try:
            r = self._post(
                f"{self.api}/fc/caas/getchallenge",
                json={"public_key": self.pubkey, "challengeID": cid},
            )
            ch = r.json()
        except Exception as e:
            err(f"arkose getchallenge: {e}")
            return None

        ctype = ch.get("challengeType")
        data = ch.get("challenge", ch)

        # 3a. image / rotation
        img_b64 = data.get("imageData") or data.get("image")
        if img_b64 and ctype in (1, 4, None):
            try:
                raw = base64.b64decode(img_b64.split(",")[-1])
                angle = solve_rotation(raw)
                ok(f"rotation → {angle}°")
                time.sleep(random.uniform(*CONFIG["delay_captcha_answer"]))
                r = self._post(
                    f"{self.api}/fc/caas/answer",
                    json={
                        "public_key": self.pubkey,
                        "challengeID": cid,
                        "answer": json.dumps({"angle": angle}),
                    },
                )
                resp = r.json()
                if resp.get("solved") and resp.get("token"):
                    return resp["token"]
            except Exception as e:
                err(f"rotation: {e}")

        # 3b. audio
        audio_b64 = data.get("audioData") or data.get("audio")
        if audio_b64:
            try:
                raw = base64.b64decode(audio_b64.split(",")[-1])
                digits = solve_audio(raw)
                ok(f"audio → {digits}")
                if digits:
                    time.sleep(random.uniform(*CONFIG["delay_captcha_answer"]))
                    r = self._post(
                        f"{self.api}/fc/caas/answer",
                        json={
                            "public_key": self.pubkey,
                            "challengeID": cid,
                            "answer": digits,
                        },
                    )
                    resp = r.json()
                    if resp.get("solved") and resp.get("token"):
                        return resp["token"]
            except Exception as e:
                err(f"audio: {e}")

        return None

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────
def rand_str(n):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))

def gen_username():
    return f"{rand_str(random.randint(5, 7))}{random.randint(100, 99999)}"

def gen_password():
    return f"{rand_str(3).upper()}{random.randint(1000, 9999)}{rand_str(2).lower()}!"

def gen_birthday():
    return (random.randint(1982, 2003), random.randint(1, 12), random.randint(1, 28))

def pick_proxy():
    return random.choice(CONFIG["proxies"]) if CONFIG["proxies"] else None

def new_session(proxy=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": CONFIG["user_agent"],
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s

def human_sleep(rng):
    time.sleep(random.uniform(*rng))

def get_csrf(s, url="https://www.roblox.com/home"):
    try:
        s.get(url, timeout=20)
    except: pass
    return s.cookies.get("XSRF-TOKEN")

# ─────────────────────────────────────────────────────────────
# ACCOUNT CREATION
# ─────────────────────────────────────────────────────────────
def create_one():
    proxy = pick_proxy()
    s = new_session(proxy)
    if proxy:
        info(f"proxy: {proxy.split('@')[-1]}")

    username = gen_username()
    password = gen_password()
    y, m, d = gen_birthday()

    # 1. warm up session
    try:
        s.get("https://www.roblox.com/", timeout=25)
        s.get("https://www.roblox.com/account/signupredir", timeout=25)
    except Exception as e:
        err(f"warmup: {e}")
        return None

    # 2. captcha
    arkose = ArkoseSession(s, "https://www.roblox.com/account/signupredir", CONFIG["arkose_pubkey"])
    token = arkose.solve()
    if not token:
        err("arkose failed")
        return None
    ok("arkose token acquired")

    # 3. submit signup
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
    headers = {"X-CSRF-TOKEN": csrf or "", "Referer": "https://www.roblox.com/account/signupredir"}
    try:
        r = s.post(
            "https://www.roblox.com/account/signupredir",
            data=payload,
            headers=headers,
            timeout=30,
            allow_redirects=False,
        )
    except Exception as e:
        err(f"signup post: {e}")
        return None

    if r.status_code not in (200, 302):
        err(f"signup status {r.status_code}")
        return None

    # 4. login to grab cookie
    time.sleep(random.uniform(2, 4))
    cookie = login(s, username, password)
    if not cookie:
        err(f"{username}: created, login failed")
        return None

    ok(f"created  {username}  /  {password}")
    return username, password, cookie

def login(s, username, password):
    csrf = get_csrf(s, "https://www.roblox.com/login")
    try:
        s.post(
            "https://www.roblox.com/login/v1/login",
            json={"ctype": "Username", "cvalue": username, "password": password},
            headers={"X-CSRF-TOKEN": csrf or ""},
            timeout=25,
        )
    except Exception as e:
        err(f"login post: {e}")
        return None
    return s.cookies.get(".ROBLOSECURITY")

# ─────────────────────────────────────────────────────────────
# FOLLOW
# ─────────────────────────────────────────────────────────────
def follow_one(target, cookie):
    proxy = pick_proxy()
    s = new_session(proxy)
    s.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")

    csrf = get_csrf(s, "https://www.roblox.com/home")
    if not csrf:
        return False, "no csrf"

    # resolve user id
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
        return False, f"resolve: {e}"

    # follow
    try:
        r = s.post(
            f"https://friends.roblox.com/v1/users/{uid}/follow",
            headers={
                "X-CSRF-TOKEN": csrf,
                "Referer": f"https://www.roblox.com/users/{uid}/profile",
            },
            timeout=20,
        )
    except Exception as e:
        return False, f"follow: {e}"

    if r.status_code == 200:
        return True, "ok"
    if r.status_code == 429:
        return False, "rate limited"
    if r.status_code == 403:
        return False, "cookie dead"
    return False, f"http {r.status_code}"

# ─────────────────────────────────────────────────────────────
# STORAGE
# ─────────────────────────────────────────────────────────────
def load_accounts():
    if not os.path.exists(CONFIG["accounts_file"]):
        return []
    out = []
    with open(CONFIG["accounts_file"], "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split(":", 2)
            if len(parts) == 3:
                out.append({"user": parts[0], "pass": parts[1], "cookie": parts[2]})
    return out

def save_account(user, pw, cookie):
    with open(CONFIG["accounts_file"], "a") as f:
        f.write(f"{user}:{pw}:{cookie}\n")

def clear_accounts():
    if os.path.exists(CONFIG["accounts_file"]):
        os.remove(CONFIG["accounts_file"])

# ─────────────────────────────────────────────────────────────
# COMMANDS
# ─────────────────────────────────────────────────────────────
def cmd_create(n):
    if n > 50:
        warn("capping at 50 per batch")
        n = 50
    made = 0
    for i in range(1, n + 1):
        print(f"\n{B}── create {i}/{n} ──{D}")
        try:
            res = create_one()
        except Exception as e:
            err(f"crash: {e}")
            res = None
        if res:
            save_account(*res)
            made += 1
        if i < n:
            human_sleep(CONFIG["delay_create"])
    ok(f"done — {made}/{n} created")

def cmd_follow(target, limit=None):
    accounts = load_accounts()
    if not accounts:
        err("no accounts. run /create first.")
        return
    if limit:
        accounts = accounts[:limit]

    hit = 0
    for i, a in enumerate(accounts, 1):
        print(f"{B}[{i}/{len(accounts)}]{D} {a['user']}", end="  ")
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

    ok(f"done — {hit}/{len(accounts)} follows landed on {target}")

def cmd_list():
    accs = load_accounts()
    if not accs:
        info("empty")
        return
    for i, a in enumerate(accs, 1):
        print(f"  {i:>4}. {a['user']}")

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
BANNER = f"""{G}
╔══════════════════════════════════════════════╗
║   roblox_farmer  —  /create  /follow         ║
║   diy captcha  ·  termux ready               ║
╚══════════════════════════════════════════════╝
{D}"""

def main():
    print(BANNER)
    info("type /help for commands")

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
            print(__doc__)
        elif cmd == "/create":
            n = 1
            if len(parts) > 1 and parts[1].isdigit():
                n = int(parts[1])
            cmd_create(n)
        elif cmd == "/follow":
            if len(parts) < 2:
                err("usage: /follow USERNAME [amount]")
                continue
            limit = None
            if len(parts) > 2 and parts[2].isdigit():
                limit = int(parts[2])
            cmd_follow(parts[1], limit)
        elif cmd == "/list":
            cmd_list()
        elif cmd == "/clear":
            clear_accounts()
            ok("accounts.txt wiped")
        else:
            err(f"unknown: {cmd}")

if __name__ == "__main__":
    main()
