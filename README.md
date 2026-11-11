# InstaQRIS CLI (SpeedCash Merchant)

> **⚠️ Proyek riset & edukasi. Bukan untuk fraud.**
> CLI ini dibuat untuk **mempelajari cara kerja layanan** pada **akun milikmu
> sendiri**. Dilarang menggunakannya untuk menyalahgunakan layanan, menipu,
> atau merugikan pihak mana pun. Segala risiko & tanggung jawab penggunaan
> ada pada pengguna.

CLI terminal ringan untuk **SpeedCash Merchant / InstaQRIS** — ditulis dengan
Python, jalan di **Termux (Android)** maupun Linux. Semua data diambil
real-time dari server resmi menggunakan sesi login akunmu sendiri.

**Langsung bisa dipakai:** kredensial statis aplikasi sudah disertakan di CLI —
kamu cukup login dengan nomor & OTP milikmu (via **WhatsApp** atau **SMS**,
sama seperti di aplikasinya). Setiap pengguna memakai akun masing-masing.

## Fitur

- Login via **OTP WhatsApp atau SMS** — pilih kanalnya saat login, sama seperti di aplikasinya
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
python3 instaqris_cli.py login      # login OTP (pilih: WhatsApp / SMS)
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

### Kanal OTP saat login

Sama seperti di aplikasinya, saat login kamu bisa memilih kanal OTP:

- **WhatsApp** — pilih `1` (default, cukup tekan Enter)
- **SMS** — pilih `2`

Non-interaktif: `python3 instaqris_cli.py login --via wa` atau `--via sms`.

## Dependensi

- Python 3.10+
- `pip install requests cryptography`
- Termux: `pkg install python`

## Konfigurasi & penyimpanan

Semua state tersimpan **lokal** di `~/.config/speedcash-cli/`:

- `session.json` — sesi login, default path:
  `~/.config/speedcash-cli/session.json` (chmod 600)
- `qris_log.json` — catatan kecil per bill QRIS (untuk `created_at`/`expired_at`)
- `config.json` — **opsional** (chmod 600):

```json
{
  "uuid": "<device uuid — dibuat otomatis bila kosong>",
  "oauth_basic": "<opsional — override kredensial statis bawaan>",
  "aes_key_b64": "<opsional — override kredensial statis bawaan>"
}
```

Kredensial statis level aplikasi (berlaku umum untuk aplikasi ini) sudah
dibundel di CLI, jadi login bisa langsung dipakai dengan akun masing-masing
via OTP. `config.json` hanya diperlukan bila ingin override atau menetapkan
uuid perangkat. Alternatif env: `INSTAQRIS_UUID`, `INSTAQRIS_OAUTH_BASIC`,
`INSTAQRIS_AES_KEY`.

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
