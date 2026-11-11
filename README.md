# InstaQRIS CLI (SpeedCash Merchant)

> **⚠️ Proyek riset & edukasi. Bukan untuk fraud.**
> CLI ini dibuat untuk **mempelajari cara kerja aplikasi** (reverse engineering /
> riset API) pada **akun milikmu sendiri**. Dilarang menggunakannya untuk
> menyalahgunakan layanan, menipu, atau merugikan pihak mana pun.
> Segala risiko & tanggung jawab penggunaan ada pada pengguna.

CLI terminal ringan untuk **SpeedCash Merchant / InstaQRIS** — dibangun murni
dari hasil riset (static analysis + capture trafik via Frida gadget pada
perangkat sendiri). Ditulis dengan Python, jalan di **Termux (Android)**
maupun Linux.

## Fitur

- Login via OTP WhatsApp
- Saldo, Riwayat transaksi, Mutasi saldo, Detail merchant
- **Generate QRIS** — `qr_string` asli diambil langsung dari server (endpoint
  internal `by-bill-number`) + bill number; tanpa menyimpan gambar lokal
- **Cek status QRIS** — `PENDING` / `PAID` / `EXPIRED` (verifikasi paralel
  `by-bill-number` + riwayat transaksi)
- Struk transaksi (cetak ulang), Refresh token, dan `raw` untuk probing

## Cara pakai

```bash
python3 instaqris_cli.py            # menu interaktif
python3 instaqris_cli.py login      # login OTP WhatsApp
python3 instaqris_cli.py saldo
python3 instaqris_cli.py riwayat [YYYY-MM-DD]
python3 instaqris_cli.py mutasi [YYYY-MM-DD]
python3 instaqris_cli.py qris <nominal>
python3 instaqris_cli.py qris-status <bill>
python3 instaqris_cli.py struk <id_transaksi>
python3 instaqris_cli.py refresh
python3 instaqris_cli.py raw <METHOD> <path> [--encrypt]
```

Konvensi menu interaktif: `00` = kembali ke menu utama.

## Dependensi

- Python 3.10+
- `pip install requests cryptography`
- Termux: `pkg install python`
- Opsional untuk riset/capture: Frida gadget + tooling-nya

## Konfigurasi & penyimpanan

Semua state tersimpan **lokal** di `~/.config/speedcash-cli/`:

- `session.json` — sesi login, default path:
  `~/.config/speedcash-cli/session.json` (chmod 600)
- `qris_log.json` — catatan kecil per bill QRIS (untuk `created_at`/`expired_at`)
- `config.json` — kredensial riset milikmu (chmod 600):

```json
{
  "oauth_basic": "Basic <base64 client_id:client_secret>",
  "aes_key_b64": "<key AES-GCM untuk enkripsi body request>",
  "uuid": "<device uuid — opsional; dibuat otomatis bila kosong>"
}
```

Nilai-nilai tersebut **tidak disertakan di repo ini** — didapat sendiri dari
proses capture/riset pada perangkatmu (lihat bagian **Riset**).
Alternatif: env `INSTAQRIS_OAUTH_BASIC`, `INSTAQRIS_AES_KEY`, `INSTAQRIS_UUID`.

> Ingin CLI portabel (state menempel di sebelah file, bukan di home)?
> Ubah `SESSION` / `QRLOG` / `CFG` menjadi
> `Path(__file__).resolve().parent / "<nama>.json"`.

## Privasi

- **Tidak ada data yang dikirim ke server eksternal / pihak ketiga.**
  Semua request dari CLI hanya menuju endpoint resmi aplikasi
  (`sc-mathilda.bmsecure.id`) langsung dari perangkatmu — tidak ada
  telemetry, tidak ada backend perantara.
- Sesi & konfigurasi hanya tersimpan lokal (chmod 600).

## Riset — cara mendapatkan kredensial

Ringkas: (1) pasang Frida gadget (mis. via Zygisk) di perangkatmu; (2) hook
okhttp/okio + modul `reactnativeaesgcmcrypto` untuk membaca header mentah
(header `Authorization` di-mask oleh aplikasi) serta key AES; (3) isi semua
nilai yang didapat ke `config.json`. Metode yang dipakai di proyek ini:
static analysis (apktool + Hermes bundle disassembly) dan capture trafik
pada perangkat sendiri.

## Cara kerja (ringkas)

- Aplikasi React Native (Hermes bytecode). Body request dienkripsi AES-GCM
  (kecuali beberapa path auth); header Authorization di-mask oleh app.
- `qr_string` per bill diambil dari `GET v6/qr/by-bill-number`
  (`uuid`, `id_outlet`, `bill_number`, `timestamp` + field versi wajib).
- Status bill diverifikasi paralel: masih ada di `by-bill-number` → `PENDING`;
  muncul di `report/transaksi` → `PAID`; hilang dari keduanya → `EXPIRED`.

## Duplikasi & kontribusi

Silakan **fork / duplikasi / modifikasi** dan tambahkan fitur baru sesukamu —
PR & issue sangat welcome. Tetap gunakan hanya untuk riset dan pada akun
sendiri.

## Lisensi

MIT — bebas dipakai, dimodifikasi, dan didistribusikan dengan tetap
menyertakan atribusi. Proyek ini **tidak berafiliasi** dengan
SpeedCash / InstaQRIS; nama & logo pihak ketiga adalah milik pemiliknya
masing-masing.
