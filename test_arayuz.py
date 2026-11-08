"""Arayuz + proxy entegre testi (pencere gostermeden).

Calistirma:  python test_arayuz.py
"""

from __future__ import annotations

import subprocess
import sys
import time
import tkinter as tk

import arayuz


def main() -> int:
    arayuz._dpi_ayarla()
    kok = tk.Tk()
    kok.withdraw()
    uygulama = arayuz.Uygulama(kok)

    if not uygulama.adaptorler:
        print("HATA: adaptor bulunamadi")
        kok.destroy()
        return 1

    # hiz olcumunu beklemeden esit agirlikla basla
    for adaptor in uygulama.adaptorler:
        adaptor.hiz = adaptor.hiz or 1.0

    port = 8898
    uygulama.port_var.set(str(port))
    uygulama._baslat()
    hata = 0

    if not uygulama.yonetici.calisiyor:
        print("HATA: proxy baslamadi ->", uygulama.yonetici.son_hata)
        kok.destroy()
        return 1

    time.sleep(0.3)
    print(f"proxy 127.0.0.1:{port} uzerinde calisiyor\n")

    for i in range(3):
        sonuc = subprocess.run(
            ["curl.exe", "-s", "-x", f"http://127.0.0.1:{port}", "--max-time", "25", "https://api.ipify.org"],
            capture_output=True,
            text=True,
            timeout=40,
        )
        cikti = (sonuc.stdout or "").strip()
        print(f"  HTTP proxy istegi {i + 1}: {cikti or '(bos)'}")
        if not cikti:
            hata = 1

    sonuc = subprocess.run(
        ["curl.exe", "-s", "--socks5", f"127.0.0.1:{port}", "--max-time", "25", "https://api.ipify.org"],
        capture_output=True,
        text=True,
        timeout=40,
    )
    cikti = (sonuc.stdout or "").strip()
    print(f"  SOCKS5 istegi     : {cikti or '(bos)'}")
    if not cikti:
        hata = 1

    ozet = uygulama.istatistik.ozet()
    print("\n  Hat bazli baglanti sayilari:")
    for ad, deger in ozet.items():
        print(f"    {ad}: {int(deger['baglanti'])} baglanti, {int(deger['gelen'])} bayt geldi")
    if len(ozet) < 2:
        print("  UYARI: trafik tek hatta kaldi.")

    uygulama._durdur()
    print("\n  Durduruldu:", not uygulama.yonetici.calisiyor)
    kok.destroy()
    print("SONUC:", "HATA" if hata else "OK")
    return hata


if __name__ == "__main__":
    sys.exit(main())
