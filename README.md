# InstaQRIS CLI (SpeedCash Merchant)

> **⚠️ Proyek riset & edukasi. Bukan untuk fraud.**
> CLI ini dibuat untuk **mempelajari cara kerja layanan** pada **akun milikmu
> sendiri**. Dilarang menggunakannya untuk menyalahgunakan layanan, menipu,
> atau merugikan pihak mana pun. Segala risiko & tanggung jawab penggunaan
> ada pada pengguna.

CLI terminal ringan untuk **SpeedCash Merchant / InstaQRIS** — ditulis dengan
Python, jalan di **Termux (Android)** maupun Linux. Semua data diambil
real-time dari server resmi menggunakan sesi login akunmu sendiri.

## Fitur

- Login via OTP WhatsApp
- **Saldo** dan **Mutasi saldo**
- **Riwayat transaksi** (per tanggal) + **Struk** (cetak ulang)
- **Detail merchant**
- **Generate QRIS** — hasilnya `qr_string` asli + bill number langsung dari
  server (tanpa menyimpan file gambar di perangkat)
- **Cek status QRIS** — `PENDING` / `PAID` / `EXPIRED`
- **Refresh token** dan **raw** (probing request untuk kebutuhan riset)

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

## Konfigurasi & penyimpanan

Semua state tersimpan **lokal** di `~/.config/speedcash-cli/`:

- `session.json` — sesi login, default path:
  `~/.config/speedcash-cli/session.json` (chmod 600)
- `qris_log.json` — catatan kecil per bill QRIS (untuk `created_at`/`expired_at`)
- `config.json` — kredensial milikmu (chmod 600):

```json
{
  "oauth_basic": "Basic <base64 client_id:client_secret>",
  "aes_key_b64": "<key AES-GCM untuk body request>",
  "uuid": "<device uuid — opsional; dibuat otomatis bila kosong>"
}
```

Nilai kredensial **tidak disertakan di repo ini** — gunakan milikmu sendiri.
Alternatif: env `INSTAQRIS_OAUTH_BASIC`, `INSTAQRIS_AES_KEY`, `INSTAQRIS_UUID`.

> Ingin CLI portabel (state menempel di sebelah file, bukan di home)?
> Ubah `SESSION` / `QRLOG` / `CFG` menjadi
> `Path(__file__).resolve().parent / "<nama>.json"`.

## Privasi

- **Tidak ada data yang dikirim ke server eksternal / pihak ketiga.**
  Semua request dari CLI hanya menuju server resmi aplikasi langsung dari
  perangkatmu — tidak ada telemetry, tidak ada backend perantara.
- Sesi & konfigurasi hanya tersimpan lokal (chmod 600).

## Duplikasi & kontribusi

Silakan **fork / duplikasi / modifikasi** dan tambahkan fitur baru sesukamu —
PR & issue sangat welcome. Tetap gunakan hanya untuk riset dan pada akun
sendiri.

## Lisensi

MIT — bebas dipakai, dimodifikasi, dan didistribusikan dengan tetap
menyertakan atribusi. Proyek ini **tidak berafiliasi** dengan
SpeedCash / InstaQRIS; nama & logo pihak ketiga adalah milik pemiliknya
masing-masing.
