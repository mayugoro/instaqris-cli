#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SpeedCash Merchant / InstaQRIS CLI v3 — saldo, mutasi, QRIS (generate + cek status), struk.

Logika QRIS v3 (tanpa zbar — semua dari server asli):
- Setelah generate: qr_string + nominal diambil dari `v6/qr/by-bill-number` (data asli SpeedCash).
- Field turunan CLI hanya: created_at & expired_at (expired_at = created + 10 menit); status hasil cek.
- Cek status (paralel by-bill-number + riwayat transaksi):
    * masih ada di by-bill-number          -> PENDING
    * pembayaran muncul di riwayat         -> PAID
    * tidak ada di keduanya                -> EXPIRED
  (pengaman: kalau bukti bayar ada di riwayat, status menang PAID walaupun payload masih ada)

Sesi login di ~/.config/speedcash-cli/session.json (chmod 600).
Log QRIS di ~/.config/speedcash-cli/qris_log.json
Kredensial riset di ~/.config/speedcash-cli/config.json (lihat README).
Dijalankan di Termux HP (server menolak request dari VPS).

Interaktif:
  python3 instaqris_cli.py

Non-interaktif:
  python3 instaqris_cli.py login [--phone 62xxxx] [--via wa|sms]
  python3 instaqris_cli.py saldo
  python3 instaqris_cli.py riwayat [YYYY-MM-DD]
  python3 instaqris_cli.py mutasi [YYYY-MM-DD]
  python3 instaqris_cli.py qris <nominal> [--no-history]
  python3 instaqris_cli.py qris-status <bill_number>
  python3 instaqris_cli.py struk <id_transaksi>
  python3 instaqris_cli.py refresh
  python3 instaqris_cli.py raw <METHOD> <path> [--body '{"a":1}'] [--encrypt]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import requests

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception:
    AESGCM = None

BASE = "https://sc-mathilda.bmsecure.id"
CFG = Path.home() / ".config" / "speedcash-cli" / "config.json"


def _load_local_config() -> dict:
    """Kredensial opsional: oauth_basic, aes_key_b64, uuid.

    Dibaca dari config.json (chmod 600) atau env INSTAQRIS_OAUTH_BASIC /
    INSTAQRIS_AES_KEY / INSTAQRIS_UUID. Kredensial statis aplikasi sudah
    dibundel di kode (APP_*), jadi bagian ini hanya untuk override /
    menetapkan uuid perangkat.
    """
    cfg = {}
    try:
        v = json.loads(CFG.read_text(encoding="utf-8"))
        if isinstance(v, dict):
            cfg = v
    except Exception:
        cfg = {}
    return {
        "oauth_basic": (cfg.get("oauth_basic") or os.environ.get("INSTAQRIS_OAUTH_BASIC") or "").strip(),
        "aes_key_b64": (cfg.get("aes_key_b64") or os.environ.get("INSTAQRIS_AES_KEY") or "").strip(),
        "uuid": (cfg.get("uuid") or os.environ.get("INSTAQRIS_UUID") or "").strip(),
    }


# Nilai statis level APLIKASI — sama untuk semua instalasi (embedded di aplikasinya).
# Disertakan agar CLI langsung bisa dipakai login dengan akun masing-masing;
# bisa di-override lewat config.json / env bila perlu.
APP_OAUTH_BASIC = "Basic NmQ1NDc2NTctOWQwMi00MjhhLWJhOTktNmJmNmMyZTIwOTcxOmJlYzQyNDVjLWU4Y2YtNDAyOC1iOWYyLWNlYTBjNjI5ODU3Mw=="
APP_AES_KEY_B64 = "Yzg1MDhmNDYzZjRlMWExOGJkNTk5MmVmNzFkOGQyNzk="

_CFG = _load_local_config()
BASIC = _CFG["oauth_basic"] or APP_OAUTH_BASIC
AES_KEY_B64 = _CFG["aes_key_b64"] or APP_AES_KEY_B64
DEFAULT_UUID = _CFG["uuid"]
VER = {"version_code": "435", "version_name": "3.9.1", "via": "SC_MERCHANT", "appid": "SPEEDCASH", "app_id": "SPEEDCASH"}
NON_ENCRYPT = ("/v4/oauth/token", "/v4/auth/login", "/v4/auth/check_user", "/v4/auth/register", "/v4/otp/generate", "/v4/otp/validate")
SESSION = Path.home() / ".config" / "speedcash-cli" / "session.json"
QRLOG = Path.home() / ".config" / "speedcash-cli" / "qris_log.json"
OUTDIR = Path.home() / "downloads"
DEVICE_SPEC = {"build_brand": "OnePlus", "build_cpu_abis": ["arm64-v8a", "armeabi-v7a", "armeabi"], "build_model": "HD1900"}
QR_EXP_MIN = 10  # masa berlaku QRIS dinamis (menit) — default SpeedCash

DEBUG = False

WIB = timezone(timedelta(hours=7))


class SessionExpired(Exception):
    pass


def log(msg: str = "") -> None:
    print(msg)


def dbg(msg: str) -> None:
    if DEBUG:
        print("[dbg]", msg)


def rp(n) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return str(n)


def wib_now() -> datetime:
    return datetime.now(WIB)


def wib_str(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, WIB).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "?"


def load_session() -> dict:
    try:
        return json.loads(SESSION.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_session(s: dict) -> None:
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(SESSION, 0o600)
    except OSError:
        pass


def encrypt_envelope(plaintext: str) -> str:
    """AES-256-GCM envelope ala app: b64(content) SP hex(iv12) SP hex(tag16)."""
    if AESGCM is None:
        raise RuntimeError("Butuh modul cryptography: pip install cryptography")
    if not AES_KEY_B64:
        raise RuntimeError(f"aes_key_b64 belum diisi — isi {CFG} (lihat README) atau set INSTAQRIS_AES_KEY")
    key = base64.b64decode(AES_KEY_B64)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)  # ct||tag
    return base64.b64encode(ct[:-16]).decode() + " " + iv.hex() + " " + ct[-16:].hex()


def auth_header(kind: str) -> dict:
    s = load_session()
    if kind == "access" and s.get("access_token"):
        return {"authorization": "Bearer " + s["access_token"]}
    if kind == "client" and s.get("client_token"):
        return {"authorization": "Bearer " + s["client_token"]}
    raise SessionExpired("Sesi kosong — login dulu.")


def api(method: str, path: str, body=None, tok: str = "access", encrypt: bool = False,
        headers: dict | None = None, form: str | None = None, timeout: int = 30) -> requests.Response:
    h = {"accept": "application/json, text/plain, */*"}
    if tok in ("access", "client"):
        h.update(auth_header(tok))
    if headers:
        h.update(headers)
    data = None
    if form is not None:
        h["content-type"] = "application/x-www-form-urlencoded;charset=UTF-8"
        data = form.encode("utf-8")
    elif body is not None:
        h["content-type"] = "text/plain"
        raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        if encrypt and path.split("?")[0] not in NON_ENCRYPT:
            raw = encrypt_envelope(raw)
            dbg(f"encrypt({len(raw)}B envelope) {path}")
        data = raw.encode("utf-8")
    r = requests.request(method, BASE + path, headers=h, data=data, timeout=timeout)
    dbg(f"{method} {path.split('?')[0]} -> {r.status_code}")
    if r.status_code == 401:
        raise SessionExpired(f"401 {r.text[:160]}")
    return r


# ---------------- AUTH ----------------

def get_client_token(force: bool = False) -> str:
    s = load_session()
    if not force and s.get("client_token") and time.time() < float(s.get("client_exp", 0)):
        return s["client_token"]
    if not BASIC:
        raise RuntimeError(f"oauth_basic belum diisi — isi {CFG} (lihat README) atau set INSTAQRIS_OAUTH_BASIC")
    dbg("oauth/token (client_credentials)")
    r = requests.post(
        BASE + "/v4/oauth/token",
        headers={"accept": "application/json, text/plain, */*",
                 "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                 "authorization": BASIC},
        data="grant_type=client_credentials", timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"oauth gagal: {j}")
    s = load_session()
    s["client_token"] = j["access_token"]
    s["client_exp"] = time.time() + int(j.get("expires_in", 86399)) - 300
    save_session(s)
    log(f"✔ token aplikasi OK (berlaku ~{int(j.get('expires_in', 0)) // 3600} jam)")
    return j["access_token"]


def login_flow(phone: str | None = None, uuid: str | None = None, via: str | None = None) -> bool:
    s = load_session()
    phone = (phone or s.get("phone") or "").strip()
    if not phone:
        phone = input("Nomor HP (62…): ").strip()
    if not phone:
        log("✖ nomor kosong.")
        return False
    uuid = (uuid or s.get("uuid") or DEFAULT_UUID).strip()
    if not uuid:
        uuid = str(uuid4())
        try:
            cfg = {}
            try:
                v = json.loads(CFG.read_text(encoding="utf-8"))
                if isinstance(v, dict):
                    cfg = v
            except Exception:
                cfg = {}
            cfg["uuid"] = uuid
            CFG.parent.mkdir(parents=True, exist_ok=True)
            CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                os.chmod(CFG, 0o600)
            except OSError:
                pass
            log(f"✔ uuid device baru dibuat & disimpan: {uuid}")
        except Exception:
            pass
    if via is None:
        c = input("Metode OTP \u2014 1=WhatsApp  2=SMS  (Enter=WhatsApp, 00=batal): ").strip()
        if c == "00":
            return False
        via = "sms" if c == "2" else "wa"
    via = (via or "wa").strip().lower()
    if via not in ("wa", "sms"):
        via = "wa"
    nama_via = "WhatsApp" if via == "wa" else "SMS"
    try:
        get_client_token()

        r = api("POST", "/v4/auth/check_user", tok="client", body={
            "phone": phone, **VER, "uuid": uuid, "location": "5.1", "is_support_cabang": True})
        j = r.json()
        log(f"check_user: {j.get('rc')} {j.get('rd')}")
        d = j.get("data") or {}
        if d.get("mail"):
            log(f"  email: {d.get('mail')}  (verifikasi WA: {d.get('isUserWA')})")

        r = api("POST", "/v4/otp/generate", tok="client", body={
            "state": "login", "location": "0.0", "uuid": uuid, "phone": phone,
            "type": via, **VER})
        j = r.json()
        if j.get("rc") != "00":
            log(f"✖ otp/generate: {j.get('rc')} {j.get('rd')}")
            return False
        nxt = ((j.get("data") or {}).get("next_request_in_second")) or "?"
        log(f"✔ OTP dikirim via {nama_via} (bisa minta ulang dalam {nxt}s)")
        otp = input("Kode OTP: ").strip()
        if not otp:
            log("✖ OTP kosong.")
            return False

        r = api("POST", "/v4/otp/validate", tok="client", body={
            "state": "login", "location": "0.0", "uuid": uuid, "phone": phone,
            "token": otp, "type": via, **VER})
        j = r.json()
        if j.get("rc") != "00":
            log(f"✖ OTP salah / gagal: {j.get('rc')} {j.get('rd')}")
            return False
        log("✔ OTP valid")

        r = api("POST", "/v4/auth/login", tok="client", body={
            **VER, "notelp_pemilik": phone, "token": otp, "uuid": uuid,
            "clientDeviceSpec": DEVICE_SPEC})
        j = r.json()
        if j.get("rc") != "00" or not j.get("access_token"):
            log(f"✖ login gagal: {j.get('rc')} {j.get('rd')}")
            return False
        d = j.get("data") or {}
        s = load_session()
        s.update({
            "phone": phone, "uuid": uuid,
            "access_token": j["access_token"],
            "outlet_id": str(d.get("id_outlet") or ""),
            "merchant_name": d.get("nama_pemilik") or "",
            "email": d.get("email") or "",
            "nmid": d.get("nmid") or "",
            "mid": d.get("mid") or "",
            "login_at": int(time.time()),
        })
        s.pop("terminal_id", None)  # ambil ulang
        save_session(s)
        log(f"✔ LOGIN SUKSES — outlet {s['outlet_id']} ({s.get('merchant_name')})")
        return True
    except SessionExpired as e:
        log(f"✖ {e}")
        return False
    except requests.RequestException as e:
        log(f"✖ jaringan: {e}")
        return False
    except Exception as e:
        log(f"✖ {e}")
        return False


def ensure_login() -> bool:
    s = load_session()
    if not s.get("access_token"):
        log("Belum login.")
        return login_flow()
    return True


def cmd_refresh(interactive: bool = True) -> None:
    s = load_session()
    if not s.get("access_token"):
        log("Belum login — refresh dilewati.")
        login_flow()
        return
    try:
        get_client_token(force=True)
    except Exception as e:
        log(f"✖ token aplikasi gagal diperbarui: {e}")
    try:
        r = api("GET", f"/v4/merchant/saldo_merchant?id_outlet={s.get('outlet_id')}")
        j = r.json()
        if j.get("rc") == "00":
            log("✔ token sesi MASIH AKTIF (server menerima)")
            d = j.get("data") or {}
            log(f"  saldo: Rp {rp(d.get('merchant_balance', 0))}")
        else:
            log(f"⚠ token sesi dijawab rc={j.get('rc')} — {j.get('rd')}")
    except SessionExpired as e:
        log(f"✖ token sesi DITOLAK: {e}")
        if interactive and input("Login ulang via OTP sekarang? 1=ya / 00=nanti: ").strip() == "1":
            login_flow()
    except Exception as e:
        log(f"⚠ cek token sesi gagal: {e}")
    s = load_session()
    log(f"  token sesi : {s.get('access_token', '-')}")
    log(f"  token app  : {s.get('client_token', '-')}")


# ---------------- QRIS LOG ----------------

def qr_log_load() -> list:
    try:
        v = json.loads(QRLOG.read_text(encoding="utf-8"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def qr_log_save(entries: list) -> None:
    QRLOG.parent.mkdir(parents=True, exist_ok=True)
    QRLOG.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(QRLOG, 0o600)
    except OSError:
        pass


def qr_log_upsert(entry: dict) -> None:
    entries = qr_log_load()
    for i, x in enumerate(entries):
        if int(x.get("bill", 0)) == int(entry.get("bill", -1)):
            entries[i] = entry
            break
    else:
        entries.append(entry)
    qr_log_save(entries)


# ---------------- SUMBER SERVER (by-bill-number & riwayat) ----------------

def fetch_by_bill(bill) -> dict:
    s = load_session()
    ts = int(time.time() * 1000)
    path = (f"/v6/qr/by-bill-number?uuid={s.get('uuid')}&id_outlet={s.get('outlet_id')}"
            f"&bill_number={int(bill)}&timestamp={ts}"
            f"&version_code=435&version_name=3.9.1&via=SC_MERCHANT&appid=SPEEDCASH&app_id=SPEEDCASH")
    return api("GET", path).json()


def _bill_payload(j) -> str | None:
    """Ambil payload QRIS dari respons by-bill-number."""
    d = j.get("data") if isinstance(j, dict) else None
    if isinstance(d, dict):
        return d.get("payload") or None
    if isinstance(d, list):
        for it in d:
            if isinstance(it, dict) and it.get("payload"):
                return it.get("payload")
    return None


def _amount_from_payload(qs: str):
    """Ambil nominal dari string EMVCo (tag 54)."""
    try:
        i = 0
        while i + 4 <= len(qs):
            tag = qs[i:i + 2]
            ln = int(qs[i + 2:i + 4])
            val = qs[i + 4:i + 4 + ln]
            if tag == "54":
                return int(val)
            i += 4 + ln
    except Exception:
        return None
    return None


def _amount_from_bill(j):
    """Nominal dari respons by-bill-number (field nominal/amount; fallback parse payload)."""
    d = j.get("data") if isinstance(j, dict) else None
    items = [d] if isinstance(d, dict) else (d if isinstance(d, list) else [])
    for it in items:
        if not isinstance(it, dict):
            continue
        for k in ("nominal", "amount", "total", "total_amount", "transaction_amount"):
            v = it.get(k)
            if v not in (None, ""):
                try:
                    return int(float(v))
                except Exception:
                    pass
        pl = it.get("payload")
        if pl:
            amt = _amount_from_payload(pl)
            if amt:
                return amt
    return None


def fetch_riwayat(date_str: str | None = None) -> list:
    s = load_session()
    d = date_str or wib_now().strftime("%Y-%m-%d")
    path = (f"/v4/report/transaksi?start_date={d}%2000:00:01&end_date={d}%2023:59:59"
            f"&id_outlet={s.get('outlet_id')}&tipe_transaksi=&with_time=true")
    j = api("GET", path).json()
    return j.get("data") or []


def fetch_mutasi(date_str: str | None = None) -> dict:
    s = load_session()
    d = date_str or wib_now().strftime("%Y-%m-%d")
    path = (f"/v4/report/mutasi?start_date={d}%2000:00:01&end_date={d}%2023:59:59"
            f"&id_outlet={s.get('outlet_id')}&tipe_transaksi=&with_time=true")
    return api("GET", path).json()


def _trx_dt_wib(item: dict):
    """datetime WIB dari item riwayat transaksi."""
    tt = (item.get("transaction_time") or "").strip()
    td = (item.get("transaction_date") or item.get("date") or "").strip()
    try:
        if tt and td:
            if td.endswith("Z"):
                base = datetime.fromisoformat(td.replace("Z", "+00:00")) + timedelta(hours=7)
                d = base.strftime("%Y-%m-%d")
            else:
                d = td[:10]
            return datetime.strptime(d + " " + tt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WIB)
    except Exception:
        pass
    try:
        if td.endswith("Z"):
            return datetime.fromisoformat(td.replace("Z", "+00:00")) + timedelta(hours=7)
    except Exception:
        pass
    return None


def _match_payments(targets: list, riwayat: list) -> None:
    """Tandai bill yang terbayar dari riwayat transaksi (nominal + waktu, FIFO)."""
    used = set()
    for x in qr_log_load():
        p = x.get("paid") or {}
        if p.get("id_transaksi"):
            used.add(str(p["id_transaksi"]))
    pays = []
    for it in riwayat:
        if it.get("tipe_transaksi") not in (None, "IN"):
            continue
        try:
            nom = int(float(it.get("nominal") or 0))
        except Exception:
            continue
        dt = _trx_dt_wib(it)
        if nom and dt:
            pays.append((dt, nom, it))
    pays.sort(key=lambda x: x[0])
    for e in sorted(targets, key=lambda x: int(x.get("created_ts") or 0)):
        if e.get("status") == "paid" or not e.get("nominal"):
            continue
        cts = int(e.get("created_ts") or 0) / 1000
        for dt, pn, it in pays:
            if pn != int(e["nominal"]) or str(it.get("id_transaksi")) in used:
                continue
            if dt.timestamp() < cts - 120:
                continue
            used.add(str(it.get("id_transaksi")))
            e["status"] = "paid"
            e["paid"] = {
                "id_transaksi": it.get("id_transaksi"),
                "nominal": pn,
                "brand": it.get("brand_name") or it.get("via"),
                "waktu": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "sumber": "riwayat",
            }
            break


def check_targets(targets: list, verbose: bool = True) -> None:
    """Cek status bill: paralel by-bill-number + riwayat transaksi (logika v3).

    by-bill-number masih balikin payload -> PENDING
    pembayaran ketemu di riwayat          -> PAID
    tidak ada di keduanya                 -> EXPIRED
    """
    if not targets:
        return
    d_start = min(int(e.get("created_ts") or time.time() * 1000) for e in targets)
    d0 = datetime.fromtimestamp(d_start / 1000, WIB).date()
    d1 = wib_now().date()
    days = []
    cur = d1
    while cur >= d0 and len(days) < 31:
        days.append(cur.strftime("%Y-%m-%d"))
        cur -= timedelta(days=1)

    bb: dict = {}
    riwayat_all: list = []
    with ThreadPoolExecutor(max_workers=min(10, len(targets) + len(days))) as ex:
        f_bb = {ex.submit(fetch_by_bill, int(e["bill"])): int(e["bill"]) for e in targets}
        f_ri = [ex.submit(fetch_riwayat, d) for d in days]
        for f in as_completed(f_bb):
            bill = f_bb[f]
            try:
                j = f.result()
                bb[bill] = (str(j.get("rc")), j.get("rd"), _bill_payload(j), _amount_from_bill(j))
            except SessionExpired:
                raise
            except Exception as ex2:
                bb[bill] = ("ERR", str(ex2), None)
        for f in f_ri:
            try:
                riwayat_all.extend(f.result() or [])
            except SessionExpired:
                raise
            except Exception as ex2:
                if verbose:
                    log(f"[riwayat] sebagian gagal: {ex2}")

    # pengaman: bukti pembayaran di riwayat menang duluan
    _match_payments(targets, riwayat_all)

    for e in targets:
        if e.get("status") == "paid":
            continue
        rc, rd, payload, amt = bb.get(int(e["bill"]), ("ERR", "tidak dicek", None, None))
        if payload:
            e["status"] = "pending"
            e["qr_string"] = payload
            if amt:
                e["nominal"] = amt
            e.pop("last_err", None)
        elif rc == "ERR":
            e["last_err"] = rd
        else:
            e["status"] = "expired"
            e["last_bb"] = f"rc={rc} — {rd}"


def show_custom(entry: dict) -> None:
    """Tampilkan custom JSON (sumber asli server + field turunan CLI)."""
    st = (entry.get("status") or "unknown").lower()
    out = {"bill_number": entry.get("bill"), "nominal": entry.get("nominal")}
    if st == "pending":
        out["qr_string"] = entry.get("qr_string")
    out["status"] = st.upper()
    cts = entry.get("created_ts")
    if cts:
        created = datetime.fromtimestamp(int(cts) / 1000, WIB)
        out["created_at"] = created.strftime("%Y-%m-%d %H:%M:%S")
        out["expired_at"] = (created + timedelta(minutes=QR_EXP_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    if st == "paid" and entry.get("paid"):
        out["paid"] = entry["paid"]
    if st == "expired":
        out["note"] = "tidak ditemukan di by-bill-number & riwayat"
        if entry.get("last_bb"):
            out["detail"] = entry["last_bb"]
    if entry.get("last_err"):
        out["error"] = entry.get("last_err")
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ---------------- GENERATE QRIS ----------------

def cmd_qris(nominal: int, save_history: bool = True) -> None:
    s = load_session()
    term = ensure_terminal()
    bill = random.randint(1000000, 9999999)
    r = api("GET", f"/v4/qr/generate?id_outlet={s.get('outlet_id')}&bill_number={bill}"
                     f"&nominal={int(nominal)}&tipe_frame=basic&terminal_id={term}")
    if "image" not in r.headers.get("content-type", ""):
        log(f"✖ generate gagal: {r.text[:200]}")
        return
    # PNG tidak disimpan ke mana pun — sumber kebenaran: by-bill-number (qr_string + nominal)
    if save_history:
        try:
            hr = api("POST", "/v4/qr/history", encrypt=True, body={
                "id_outlet": int(s.get("outlet_id", 0)), "description": "", "nominal": int(nominal),
                "terminal_id": term, "type": "dynamic", "bill_number": bill})
            hj = hr.json()
            if hj.get("rc") != "00":
                log(f"⚠ riwayat QRIS: rc={hj.get('rc')} {hj.get('rd')}")
        except Exception as e:
            log(f"⚠ riwayat QRIS gagal (tidak fatal): {e}")

    qr_string = None
    srv_nom = None
    try:
        j = fetch_by_bill(bill)
        qr_string = _bill_payload(j)
        srv_nom = _amount_from_bill(j)
        if not qr_string:
            log(f"⚠ by-bill-number: rc={j.get('rc')} — {j.get('rd')}")
    except Exception as e:
        log(f"⚠ by-bill-number gagal: {e}")

    entry = {
        "bill": bill, "nominal": srv_nom or int(nominal), "terminal": term,
        "created_ts": int(time.time() * 1000),
        "status": "pending", "qr_string": qr_string,
    }
    qr_log_upsert(entry)
    print()
    show_custom(entry)
    log(f"(cek status: menu 4 \u2192 ketik bill {bill})")


# ---------------- CEK STATUS QRIS ----------------


def _midnight_ts(dt: datetime) -> int:
    return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def cmd_cek_status(bill: int | None = None, interactive: bool = True) -> None:
    """Sekali pakai: cek 1 bill, tampilkan, kembali ke menu utama."""
    if bill is not None:
        e = next((x for x in qr_log_load() if int(x["bill"]) == int(bill)), None)
        if e is None:
            e = {"bill": int(bill), "nominal": None,
                 "created_ts": _midnight_ts(wib_now()), "status": "unknown"}
        check_targets([e])
        print()
        show_custom(e)
        qr_log_upsert(e)
        return

    if not interactive:
        return

    b = input("Bill number (00 = kembali): ").strip()
    if b == "" or b == "00":
        return
    if not b.isdigit():
        log("✖ masukkan angka bill number.")
        return
    e = next((x for x in qr_log_load() if int(x["bill"]) == int(b)), None)
    if e is None:
        e = {"bill": int(b), "nominal": None,
             "created_ts": _midnight_ts(wib_now()), "status": "unknown"}
    check_targets([e])
    print()
    show_custom(e)
    qr_log_upsert(e)


# ---------------- DATA ----------------

def cmd_saldo() -> None:
    s = load_session()
    r = api("GET", f"/v4/merchant/saldo_merchant?id_outlet={s.get('outlet_id')}")
    j = r.json()
    d = j.get("data") or {}
    log(f"rc {j.get('rc')} — {j.get('rd')}")
    log(f"  Saldo merchant   : Rp {rp(d.get('merchant_balance', 0))}")
    log(f"  Siap ditarik     : Rp {rp(d.get('saldo_available', 0))}")
    log(f"  Sedang diproses  : Rp {rp(d.get('saldo_diproses', 0))}")
    if d.get("nominal_trx_bulan_ini") is not None:
        log(f"  Trx bulan ini    : Rp {d.get('nominal_trx_bulan_ini')}")


def show_riwayat(items: list, date_str: str) -> None:
    if not items:
        log(f"(tidak ada transaksi {date_str})")
        return
    log(f"Riwayat {date_str} — {len(items)} transaksi:")
    for i, t in enumerate(items, 1):
        mark = "+" if t.get("tipe_transaksi") == "IN" else "-"
        log(f" {i:2d}. {mark} Rp {rp(t.get('nominal', 0)):>10}  {t.get('transaction_time', '')}  "
            f"{t.get('brand_name', '')}  [{t.get('id_transaksi', '')}]")


def cmd_riwayat(date_str: str | None = None, interactive: bool = True) -> None:
    d = date_str or wib_now().strftime("%Y-%m-%d")
    while True:
        items = fetch_riwayat(d)
        show_riwayat(items, d)
        if not interactive:
            return
        log()
        log("  \u21b3 ketik nomor = lihat STRUK \U0001f9fe   |   [1] ganti tanggal   |   [00] kembali")
        c = input("Pilih: ").strip()
        if c == "1":
            nd = input(f"Tanggal (YYYY-MM-DD, kosong={d}): ").strip()
            if nd:
                d = nd
        elif c.isdigit() and 1 <= int(c) <= len(items):
            trx = items[int(c) - 1]
            try:
                j = fetch_struk(trx.get("id_transaksi"))
                show_struk(j)
            except Exception as e:
                log(f"✖ struk gagal: {e}")
        else:
            return


def cmd_mutasi(date_str: str | None = None, interactive: bool = True) -> None:
    d = date_str or wib_now().strftime("%Y-%m-%d")
    while True:
        j = fetch_mutasi(d)
        items = j.get("data") or []
        log(f"Mutasi {d} — {j.get('rd')} (saldo: Rp {rp(j.get('balance', 0))})")
        if not items:
            log("(tidak ada mutasi)")
        for t in items:
            inout = "+" if str(t.get("credit") or "0") not in ("0", "0.0", "0.00") else "-"
            amt = t.get("credit") if inout == "+" else t.get("debit")
            log(f"  {inout} Rp {amt}  {t.get('mutation_date', '')}  {t.get('brand_name', '')}  "
                f"[{t.get('trx_reff', '')}]")
            if t.get("merchant_reff"):
                log(f"      {t.get('merchant_reff')}")
        if not interactive:
            return
        log()
        log("  \u21b3 [1] ganti tanggal   [00] kembali")
        c = input("Pilih: ").strip()
        if c == "1":
            nd = input(f"Tanggal (YYYY-MM-DD, kosong={d}): ").strip()
            if nd:
                d = nd
        else:
            return


def ensure_terminal() -> str:
    s = load_session()
    if s.get("terminal_id"):
        return s["terminal_id"]
    j = api("GET", f"/v4/terminal/user?id_outlet={s.get('outlet_id')}&uuid={s.get('uuid')}").json()
    term = ((j.get("data") or {}).get("terminal_id") or "").strip()
    if not term:
        j2 = api("GET", f"/v4/terminal?id_outlet={s.get('outlet_id')}").json()
        arr = j2.get("data") or []
        term = (arr[0].get("terminal_id") if arr else "") or ""
    s = load_session()
    s["terminal_id"] = term
    save_session(s)
    return term


# ---------------- STRUK (cetak ulang) ----------------

def fetch_struk(reff_id) -> dict:
    s = load_session()
    now = datetime.now(timezone.utc)
    body = {
        "version_code": "435", "processing_code": "ADMTRASI",
        "includes": {"reff_id": str(reff_id), "product_code": "CU"},
        "uuid": s.get("uuid"), "via": "SC_MERCHANT", "version_name": "3.9.1",
        "additional_datas": ["0", "", "", "", "", ""],
        "outlet_id": s.get("outlet_id"), "pin": "",
        "appid": "SPEEDCASH", "msg_type": "TRX", "app_id": "SPEEDCASH",
        "transmission_datetime": now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}",
    }
    return api("POST", "/v5/invoices/cetak_ulang", encrypt=True, body=body).json()


def show_struk(j: dict) -> None:
    d = j.get("data") or {}
    sp = d.get("struk_print") or {}
    if not sp:
        log(f"rc {j.get('rc')} — {j.get('rd')}")
        print(json.dumps(d or j, ensure_ascii=False, indent=2)[:2000])
        return
    head = sp.get("head") or {}
    log(f"──── {head.get('title') or 'STRUK'} ────")
    if head.get("status"):
        log(f"  {head.get('status')}" + (f" — {head.get('total')}" if head.get("total") else ""))
    for row in sp.get("body") or []:
        k = row.get("key")
        if k is None:
            continue
        log(f"  {k:16s}: {row.get('value')}")
    tail = sp.get("tail") or sp.get("footer")
    if tail:
        print(json.dumps(tail, ensure_ascii=False, indent=2)[:500])


# ---------------- DETAIL / INFO ----------------

def cmd_detail() -> None:
    s = load_session()
    j = api("GET", f"/v6/merchant/detail?id_outlet={s.get('outlet_id')}").json()
    d = j.get("data") or {}
    log(f"rc {j.get('rc')} — {j.get('rd')}")
    for k, label in [("id_outlet", "Outlet"), ("nama_merchant", "Merchant"), ("nama_pemilik", "Pemilik"),
                     ("notelp_pemilik", "Telepon"), ("mid_merchant", "MID"), ("nmid_merchant", "NMID"),
                     ("status_merchant", "Status"), ("nama_kota_merchant", "Kota"),
                     ("nama_kategori_merchant", "Kategori"), ("kode_qris_merchant", "QRIS statis")]:
        if d.get(k) not in (None, ""):
            log(f"  {label:12s}: {d.get(k)}")


def cmd_info() -> None:
    s = load_session()
    if not s.get("access_token"):
        log("Belum login.")
        return
    log(f"Outlet   : {s.get('outlet_id')}  ({s.get('merchant_name')})")
    log(f"Phone    : {s.get('phone')}")
    log(f"UUID     : {s.get('uuid')}")
    log(f"NMID/MID : {s.get('nmid', '-')} / {s.get('mid', '-')}")
    log(f"Terminal : {s.get('terminal_id') or '(belum diambil)'}")
    if s.get("client_exp"):
        log(f"Token app berlaku s/d : {datetime.fromtimestamp(s['client_exp'], WIB):%Y-%m-%d %H:%M} WIB")
    if s.get("login_at"):
        log(f"Login terakhir        : {datetime.fromtimestamp(s['login_at'], WIB):%Y-%m-%d %H:%M} WIB")
    entries = qr_log_load()
    if entries:
        paid = sum(1 for e in entries if e.get("status") == "paid")
        log(f"Log QRIS : {len(entries)} bill ({paid} terbayar)")


# ---------------- MENU ----------------

def menu() -> None:
    while True:
        s = load_session()
        log()
        log("========= SPEEDCASH MERCHANT =========")
        if s.get("access_token"):
            log(f" {s.get('merchant_name')} — outlet {s.get('outlet_id')}")
        else:
            log(" (belum login — pilih 9)")
        log("  1. Saldo")
        log("  2. Riwayat transaksi")
        log("  3. Generate QRIS")
        log("  4. Cek status QRIS")
        log("  5. Mutasi saldo")
        log("  6. Detail merchant")
        log("  7. Refresh token")
        log("  8. Info sesi")
        log("  9. Login / login ulang")
        log("  0. Keluar")
        c = input("Pilih: ").strip()
        try:
            if c == "0":
                return
            elif c == "1":
                if ensure_login():
                    cmd_saldo()
            elif c == "2":
                if ensure_login():
                    cmd_riwayat()
            elif c == "3":
                if ensure_login():
                    n = input("Nominal QRIS (Rp): ").strip()
                    if n == "00" or not n:
                        continue
                    try:
                        nominal = int(n.replace(".", "").replace(",", ""))
                    except ValueError:
                        log("✖ nominal tidak valid.")
                        continue
                    if nominal < 100:
                        log("✖ minimal Rp 100.")
                        continue
                    cmd_qris(nominal)
            elif c == "4":
                if ensure_login():
                    cmd_cek_status()
            elif c == "5":
                if ensure_login():
                    cmd_mutasi()
            elif c == "6":
                if ensure_login():
                    cmd_detail()
            elif c == "7":
                cmd_refresh()
            elif c == "8":
                cmd_info()
            elif c == "9":
                login_flow()
            else:
                log("Pilihan tidak dikenal.")
        except SessionExpired as e:
            log(f"✖ Sesi kedaluwarsa ({e}).")
            log("  Login ulang…")
            login_flow()
        except requests.RequestException as e:
            log(f"✖ jaringan: {e}")
        except KeyboardInterrupt:
            log("\n(dibatalkan)")
        except Exception as e:
            log(f"✖ {e}")


# ---------------- MAIN ----------------

def main() -> int:
    global DEBUG
    ap = argparse.ArgumentParser(description="SpeedCash Merchant / InstaQRIS CLI v3")
    ap.add_argument("--debug", action="store_true", help="tampilkan detail request")
    sub = ap.add_subparsers(dest="cmd")

    p_login = sub.add_parser("login", help="login via OTP (WhatsApp/SMS)")
    p_login.add_argument("--phone", default=None)
    p_login.add_argument("--uuid", default=None)
    p_login.add_argument("--via", choices=["wa", "sms"], default=None, help="kanal OTP: wa (default) / sms")

    sub.add_parser("saldo")

    p_ri = sub.add_parser("riwayat")
    p_ri.add_argument("tanggal", nargs="?", default=None)

    p_mu = sub.add_parser("mutasi")
    p_mu.add_argument("tanggal", nargs="?", default=None)

    p_qr = sub.add_parser("qris")
    p_qr.add_argument("nominal", type=int)
    p_qr.add_argument("--no-history", action="store_true")

    p_qs = sub.add_parser("qris-status", help="cek status bill QRIS")
    p_qs.add_argument("bill", nargs="?", type=int, default=None)

    p_st = sub.add_parser("struk", help="lihat struk transaksi (cetak ulang)")
    p_st.add_argument("reff", help="id_transaksi")

    sub.add_parser("refresh", help="perbarui token aplikasi + cek token sesi")

    sub.add_parser("detail")
    sub.add_parser("info")

    p_raw = sub.add_parser("raw", help="request manual untuk probing")
    p_raw.add_argument("method")
    p_raw.add_argument("path")
    p_raw.add_argument("--body", default=None)
    p_raw.add_argument("--encrypt", action="store_true")
    p_raw.add_argument("--client", action="store_true", help="pakai token aplikasi (client)")

    args = ap.parse_args()
    DEBUG = args.debug

    try:
        if args.cmd is None:
            menu()
            return 0
        if args.cmd == "login":
            return 0 if login_flow(args.phone, args.uuid, args.via) else 1
        if args.cmd == "saldo":
            if ensure_login():
                cmd_saldo()
            return 0
        if args.cmd == "riwayat":
            if ensure_login():
                cmd_riwayat(args.tanggal, interactive=False)
            return 0
        if args.cmd == "mutasi":
            if ensure_login():
                cmd_mutasi(args.tanggal, interactive=False)
            return 0
        if args.cmd == "qris":
            if ensure_login():
                cmd_qris(args.nominal, save_history=not args.no_history)
            return 0
        if args.cmd == "qris-status":
            if args.bill is None:
                log("Sertakan bill number: python3 instaqris_cli.py qris-status <bill>")
                return 1
            if ensure_login():
                cmd_cek_status(args.bill, interactive=False)
            return 0
        if args.cmd == "struk":
            if ensure_login():
                show_struk(fetch_struk(args.reff))
            return 0
        if args.cmd == "refresh":
            cmd_refresh(interactive=False)
            return 0
        if args.cmd == "detail":
            if ensure_login():
                cmd_detail()
            return 0
        if args.cmd == "info":
            cmd_info()
            return 0
        if args.cmd == "raw":
            body = json.loads(args.body) if args.body else None
            tok = "client" if args.client else "access"
            r = api(args.method.upper(), args.path, body=body, tok=tok, encrypt=args.encrypt)
            log(f"HTTP {r.status_code} {r.headers.get('content-type', '')}")
            ct = r.headers.get("content-type", "")
            if "image" in ct:
                out = OUTDIR / "raw_out.bin"
                OUTDIR.mkdir(parents=True, exist_ok=True)
                out.write_bytes(r.content)
                log(f"(gambar {len(r.content)}B -> {out})")
            else:
                log(r.text[:4000])
            return 0
    except SessionExpired as e:
        log(f"✖ {e} — login dulu: python3 instaqris_cli.py login")
        return 1
    except requests.RequestException as e:
        log(f"✖ jaringan: {e}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
