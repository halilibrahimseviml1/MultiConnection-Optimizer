"""Oyun Modu v2 (ikili yonlendirme) cekirdek testleri.

GUI'siz, admin'siz ve ag gerektirmeyen kisimlari kesin olarak test eder:
  * host_ips: IP / CIDR aynen, cozumlenemeyen alan adi bos liste.
  * ikili_plan: sentetik Adaptor + sonuc dict'leriyle tek/ikili hat kararlari.
  * rotalari_oku: her zaman list dondurur (bos olabilir).

Calistirma:  python test_oyun_modu.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Betik, uygulama klasorunun disindan da calistirilabilir olsun diye yolu ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cekirdek  # noqa: E402


def _adaptor(ip: str = "10.0.0.1", ad: str = "Ethernet", gecit: str = "10.0.0.254",
             ifindex: int = 27) -> cekirdek.Adaptor:
    """Test icin sentetik, calisir durumda bir adaptor uretir."""
    return cekirdek.Adaptor(ad=ad, ip=ip, gecit=gecit, durum="Up", ifindex=ifindex)


def _sonuc(ort: float = 20.0, jitter: float = 1.0, kayip: float = 0.0,
           hata: str = "") -> dict:
    """ping_olc ciktisi biciminde sentetik bir sonuc dict'i uretir."""
    return {
        "hedef_ip": "8.8.8.8",
        "ort": float(ort),
        "min": float(ort) - 1.0,
        "max": float(ort) + 1.0,
        "jitter": float(jitter),
        "kayip": float(kayip),
        "hata": hata,
    }


class HostIpsTest(unittest.TestCase):
    """host_ips: IP/CIDR aynen gecer, cozumlenemeyen alan adi bos doner."""

    def test_ip_adresi_aynen_donuyor(self) -> None:
        self.assertEqual(cekirdek.host_ips("8.8.8.8"), ["8.8.8.8"])

    def test_cidr_aynen_donuyor(self) -> None:
        self.assertEqual(cekirdek.host_ips("1.2.3.0/24"), ["1.2.3.0/24"])

    def test_cozumlenemeyen_alan_adi_bos_donuyor(self) -> None:
        # .invalid TLD'si RFC 2606 geregi asla cozumlenmez.
        self.assertEqual(cekirdek.host_ips("example.invalid"), [])


class IkiliPlanTest(unittest.TestCase):
    """ikili_plan: sentetik Adaptor + sonuc ile karar mantigi."""

    def test_iki_saglikli_hat(self) -> None:
        a1 = _adaptor(ip="10.0.0.1", ad="Ethernet", gecit="10.0.0.254", ifindex=27)
        a2 = _adaptor(ip="192.168.1.10", ad="Wi-Fi", gecit="192.168.1.1", ifindex=10)
        adaptorler = [a1, a2]
        sonuclar = [
            (a1, _sonuc(ort=15.0, jitter=0.5, kayip=0.0)),
            (a2, _sonuc(ort=25.0, jitter=1.0, kayip=2.0)),
        ]

        plan = cekirdek.ikili_plan(adaptorler, sonuclar, "8.8.8.8")

        self.assertIsNotNone(plan)
        self.assertIs(plan["tek_hat_modu"], False)
        self.assertIs(plan["diger_saglikli"], True)
        self.assertIsNotNone(plan["diger_hat"])
        self.assertTrue(plan["hedefler"])  # dolu olmali

    def test_ikinci_hat_kayip_100_tek_hat_moduna_duser(self) -> None:
        a1 = _adaptor(ip="10.0.0.1", ad="Ethernet", gecit="10.0.0.254", ifindex=27)
        a2 = _adaptor(ip="192.168.1.10", ad="Wi-Fi", gecit="192.168.1.1", ifindex=10)
        adaptorler = [a1, a2]
        sonuclar = [
            (a1, _sonuc(ort=15.0, jitter=0.5, kayip=0.0)),
            (a2, _sonuc(ort=30.0, jitter=2.0, kayip=100.0)),
        ]

        plan = cekirdek.ikili_plan(adaptorler, sonuclar, "8.8.8.8")

        self.assertIsNotNone(plan)
        self.assertIs(plan["tek_hat_modu"], True)
        self.assertIs(plan["diger_saglikli"], False)

    def test_hepsi_hata_none_doner(self) -> None:
        a1 = _adaptor(ip="10.0.0.1", ad="Ethernet")
        a2 = _adaptor(ip="192.168.1.10", ad="Wi-Fi")
        adaptorler = [a1, a2]
        sonuclar = [
            (a1, _sonuc(hata="yanit yok", kayip=100.0)),
            (a2, _sonuc(hata="yanit yok", kayip=100.0)),
        ]

        self.assertIsNone(cekirdek.ikili_plan(adaptorler, sonuclar, "8.8.8.8"))

    def test_tek_adaptor_diger_yok_tek_hat_modu(self) -> None:
        a1 = _adaptor(ip="10.0.0.1", ad="Ethernet")
        adaptorler = [a1]
        sonuclar = [(a1, _sonuc(ort=15.0))]

        plan = cekirdek.ikili_plan(adaptorler, sonuclar, "8.8.8.8")

        self.assertIsNotNone(plan)
        self.assertIsNone(plan["diger_hat"])
        self.assertIs(plan["tek_hat_modu"], True)
        self.assertTrue(plan["hedefler"])


class RotalariOkuTest(unittest.TestCase):
    """rotalari_oku: her durumda list dondurur (bos olabilir)."""

    def test_list_donduruyor(self) -> None:
        sonuc = cekirdek.rotalari_oku()
        self.assertIsInstance(sonuc, list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
