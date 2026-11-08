"""Entegre test: proxy'yi baslatir, curl ile iki hattan trafik gecirir.

Calistirma:  python entegre_test.py
"""

from __future__ import annotations

import subprocess
import sys
import time

import cekirdek


def curl(argumanlar: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["curl.exe", *argumanlar],
        capture_output=True,
        text=True,
        timeout=45,
    )


def main() -> int:
    adaptorler = cekirdek.adaptorleri_bul()
    if len(adaptorler) < 2:
        print(f"UYARI: {len(adaptorler)} adaptor bulundu; en az 2 gerekli.")

    print("Adaptorler:")
    for adaptor in adaptorler:
        print(f"  - {adaptor.ad:12s} {adaptor.ip}")

    secici = cekirdek.Secici(adaptorler, cekirdek.MOD_SIRALI)
    istatistik = cekirdek.Istatistik()
    yonetici = cekirdek.SunucuYoneticisi()
    port = 8899

    if not yonetici.baslat(secici, istatistik, port):
        print(f"HATA: sunucu baslatilamadi: {yonetici.son_hata}")
        return 1
    time.sleep(0.3)
    print(f"\nProxy 127.0.0.1:{port} uzerinde calisiyor.\n")

    hatalar = 0
    print("--- HTTP proxy (HTTPS CONNECT) ---")
    for i in range(4):
        sonuc = curl(["-s", "-x", f"http://127.0.0.1:{port}", "--max-time", "25", "https://api.ipify.org"])
        cikti = (sonuc.stdout or "").strip()
        if not cikti:
            hatalar += 1
        print(f"  {i + 1}. istek: cikis IP = {cikti or '(bos)'} {('- ' + sonuc.stderr.strip()[:60]) if sonuc.stderr.strip() else ''}")

    print("--- SOCKS5 ---")
    for i in range(4):
        sonuc = curl(["-s", "--socks5", f"127.0.0.1:{port}", "--max-time", "25", "https://api.ipify.org"])
        cikti = (sonuc.stdout or "").strip()
        if not cikti:
            hatalar += 1
        print(f"  {i + 1}. istek: cikis IP = {cikti or '(bos)'} {('- ' + sonuc.stderr.strip()[:60]) if sonuc.stderr.strip() else ''}")

    print("--- duz HTTP (absolute-form) ---")
    sonuc = curl(["-s", "-x", f"http://127.0.0.1:{port}", "--max-time", "25", "http://api.ipify.org"])
    cikti = (sonuc.stdout or "").strip()
    if not cikti:
        hatalar += 1
    print(f"  cikis IP = {cikti or '(bos)'}")

    print("\n--- adaptor bazli istatistik ---")
    for ad, deger in istatistik.ozet().items():
        print(
            f"  {ad:12s} baglanti={int(deger['baglanti']):3d}  "
            f"gelen={cekirdek.boyut_metni(int(deger['gelen']))}  "
            f"giden={cekirdek.boyut_metni(int(deger['giden']))}"
        )

    yonetici.durdur()

    if hatalar:
        print(f"\nSONUC: {hatalar} istek basarisiz.")
        return 1
    print("\nSONUC: tum istekler basarili.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
