"""Yonetici (UAC) gerektiren islemler icin yardimci.

Arayuz bu betigi `ShellExecuteW(..., "runas", ...)` ile baslatir. Islem bitince
`--sonuc` ile verilen JSON dosyasina sonuc yazar; arayuz o dosyayi bekler.

Kullanim:
  python yonetici_islem.py --islem metrik --spec spec.json --yedek yedek.json --sonuc sonuc.json
  python yonetici_islem.py --islem metrik-geri --yedek yedek.json --sonuc sonuc.json
  python yonetici_islem.py --islem ikili --spec spec.json --yedek yedek.json --sonuc sonuc.json
  python yonetici_islem.py --islem wifi-guc --sonuc sonuc.json
"""

from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cekirdek  # noqa: E402


def _yonetici_mi() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _yaz(yol: str | None, veri: dict) -> int:
    if yol:
        try:
            Path(yol).write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    print(json.dumps(veri, ensure_ascii=False))
    return 0 if veri.get("basarili") else 1


def _dest_prefix(hedef: str) -> str:
    """Rota dogrulamasi icin hedefi Get-NetRoute 'dest' bicimine cevirir."""
    hedef = (hedef or "").strip()
    if "/" in hedef:
        try:
            return str(ipaddress.ip_network(hedef, strict=False))
        except ValueError:
            return hedef
    return f"{hedef}/32"


def islem_metrik(spec_yolu: str, yedek_yolu: str) -> dict:
    spec = json.loads(Path(spec_yolu).read_text(encoding="utf-8-sig"))
    hatlar: dict = spec.get("hatlar", {})
    mevcut = cekirdek.metrikleri_oku()

    yedek = Path(yedek_yolu)
    if not yedek.exists() and mevcut:
        yedek.write_text(json.dumps({"metrikler": mevcut}, ensure_ascii=False, indent=2), encoding="utf-8")

    detay = []
    basarili = True
    for ad, deger in hatlar.items():
        ok = cekirdek.metrik_ayarla(str(ad), int(deger))
        basarili = basarili and ok
        detay.append({"hat": str(ad), "deger": int(deger), "basarili": ok})

    guncel = cekirdek.metrikleri_oku()
    # netsh yonetici olmadan da 0 dondurebiliyor: sonucu okuyarak dogrula
    for kayit in detay:
        okundu = guncel.get(kayit["hat"])
        kayit["guncel"] = okundu
        kayit["basarili"] = bool(kayit["basarili"] and okundu == kayit["deger"])
    basarili = all(kayit["basarili"] for kayit in detay) and bool(detay)

    return {
        "basarili": basarili,
        "mesaj": "Rota metrikleri uygulandi." if basarili else "Metrikler uygulanamadi (yonetici izni gerekli).",
        "detay": detay,
        "guncel": guncel,
    }


def islem_metrik_geri(yedek_yolu: str) -> dict:
    yedek = Path(yedek_yolu)
    if not yedek.exists():
        return {"basarili": False, "mesaj": "Geri alinacak kayit bulunamadi."}
    veri = json.loads(yedek.read_text(encoding="utf-8-sig"))
    metrikler: dict = veri.get("metrikler", {})
    detay = []
    basarili = True
    for ad, deger in metrikler.items():
        ok = cekirdek.metrik_ayarla(str(ad), int(deger))
        basarili = basarili and ok
        detay.append({"hat": str(ad), "deger": int(deger), "basarili": ok})
    guncel = cekirdek.metrikleri_oku()
    for kayit in detay:
        okundu = guncel.get(kayit["hat"])
        kayit["guncel"] = okundu
        kayit["basarili"] = bool(kayit["basarili"] and okundu == kayit["deger"])
    basarili = all(kayit["basarili"] for kayit in detay) and bool(detay)

    # Ikili yonlendirme icin eklenmis rotalar varsa geri sil.
    silinen_rotalar = []
    for hedef in veri.get("rotalar", []):
        ok = cekirdek.rota_sil(hedef)
        silinen_rotalar.append({"hedef": hedef, "basarili": ok})
        basarili = basarili and ok

    return {
        "basarili": basarili,
        "mesaj": "Eski metrikler geri yuklendi." if basarili else "Geri yukleme tamamlanamadi (yonetici izni gerekli).",
        "detay": detay,
        "silinen_rotalar": silinen_rotalar,
        "guncel": cekirdek.metrikleri_oku(),
    }


def islem_ikili(spec_yolu: str, yedek_yolu: str) -> dict:
    spec = json.loads(Path(spec_yolu).read_text(encoding="utf-8-sig"))
    hedefler: list = spec.get("hedefler", [])
    en_iyi: dict = spec.get("en_iyi_hat", {})
    diger: dict | None = spec.get("diger_hat")
    tek_hat_modu: bool = bool(spec.get("tek_hat_modu", True))

    yedek = Path(yedek_yolu)
    if not yedek.exists():
        yedek.write_text(
            json.dumps(
                {"metrikler": cekirdek.metrikleri_oku(), "rotalar": hedefler},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    detay = []
    basarili = True

    if tek_hat_modu:
        # Tek hat: oyun trafigi de varsayilan trafik de ayni hattan gider.
        ok = cekirdek.metrik_ayarla(en_iyi.get("ad", ""), 1)
        basarili = basarili and ok
        detay.append({"islem": "metrik", "hat": en_iyi.get("ad", ""), "deger": 1, "basarili": ok})
        if diger:
            ok2 = cekirdek.metrik_ayarla(diger.get("ad", ""), 100)
            basarili = basarili and ok2
            detay.append({"islem": "metrik", "hat": diger.get("ad", ""), "deger": 100, "basarili": ok2})
    else:
        # Ikili mod: varsayilan trafik ikinci hattan (metrik 20); oyun trafigi
        # statik rota ile en iyi hatta sabitlenir (metrik 1), yedegi diger hat (100).
        ok1 = cekirdek.metrik_ayarla(en_iyi.get("ad", ""), 100)
        basarili = basarili and ok1
        detay.append({"islem": "metrik", "hat": en_iyi.get("ad", ""), "deger": 100, "basarili": ok1})
        ok2 = cekirdek.metrik_ayarla(diger.get("ad", ""), 20)
        basarili = basarili and ok2
        detay.append({"islem": "metrik", "hat": diger.get("ad", ""), "deger": 20, "basarili": ok2})
        for hedef in hedefler:
            ok_r1 = cekirdek.rota_ekle(hedef, en_iyi.get("gecit", ""), int(en_iyi.get("ifindex", 0)), 1)
            ok_r2 = cekirdek.rota_ekle(hedef, diger.get("gecit", ""), int(diger.get("ifindex", 0)), 100)
            basarili = basarili and ok_r1 and ok_r2
            detay.append({"islem": "rota", "hedef": hedef, "gecit": en_iyi.get("gecit", ""), "metrik": 1, "basarili": ok_r1})
            detay.append({"islem": "rota", "hedef": hedef, "gecit": diger.get("gecit", ""), "metrik": 100, "basarili": ok_r2})

    # Dogrulama (route/Set-NetIPInterface yalanci basari donebilir): read-back.
    guncel_metrikler = cekirdek.metrikleri_oku()
    guncel_rotalar = cekirdek.rotalari_oku()

    if tek_hat_modu:
        beklenen = {en_iyi.get("ad", ""): 1}
        if diger:
            beklenen[diger.get("ad", "")] = 100
    else:
        beklenen = {en_iyi.get("ad", ""): 100, diger.get("ad", ""): 20}
    metrik_tam = all(guncel_metrikler.get(ad) == deger for ad, deger in beklenen.items() if ad)

    rota_tam = True
    if not tek_hat_modu:
        for hedef in hedefler:
            dest = _dest_prefix(hedef)
            kayit = any(
                r["next_hop"] == en_iyi.get("gecit", "") and r["metric"] == 1 and r["dest"] == dest
                for r in guncel_rotalar
            )
            if not kayit:
                rota_tam = False
            detay.append({"islem": "dogrulama-rota", "hedef": hedef, "dest": dest, "basarili": kayit})

    basarili = basarili and metrik_tam and rota_tam

    return {
        "basarili": basarili,
        "mesaj": ("Ikili yonlendirme uygulandi." if basarili
                  else "Ikili yonlendirme uygulanamadi (yonetici izni gerekli)."),
        "detay": detay,
        "guncel": {"metrikler": guncel_metrikler, "rotalar": guncel_rotalar},
    }


def islem_wifi_guc() -> dict:
    ok = cekirdek.wifi_guc_tasarrufu_kapat()
    return {
        "basarili": ok,
        "mesaj": "Kablosuz adaptor guc tasarrufu kapatildi." if ok else "Guc tasarrufu kapatilamadi.",
    }


def main() -> int:
    ayristirici = argparse.ArgumentParser()
    ayristirici.add_argument("--islem", required=True, choices=["metrik", "metrik-geri", "ikili", "wifi-guc"])
    ayristirici.add_argument("--spec", default="")
    ayristirici.add_argument("--yedek", default="")
    ayristirici.add_argument("--sonuc", default="")
    argumanlar = ayristirici.parse_args()

    if not _yonetici_mi():
        return _yaz(argumanlar.sonuc, {
            "basarili": False,
            "mesaj": "Yonetici izni yok. Islem UAC onayi ile baslatilmali.",
        })

    try:
        if argumanlar.islem == "metrik":
            veri = islem_metrik(argumanlar.spec, argumanlar.yedek)
        elif argumanlar.islem == "metrik-geri":
            veri = islem_metrik_geri(argumanlar.yedek)
        elif argumanlar.islem == "ikili":
            veri = islem_ikili(argumanlar.spec, argumanlar.yedek)
        else:
            veri = islem_wifi_guc()
    except Exception as hata:  # noqa: BLE001
        veri = {"basarili": False, "mesaj": f"Hata: {hata}"}

    return _yaz(argumanlar.sonuc, veri)


if __name__ == "__main__":
    sys.exit(main())
