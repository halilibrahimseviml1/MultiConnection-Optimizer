"""Baglanti Birlestirici - cekirdek.

PC'deki birden fazla internet hattini (Wi-Fi + Ethernet/USB tethering) tek bir
yerel proxy uzerinden, baglanti bazinda dagitir.

Nasil calisir?
  * Her yeni baglanti (tarayici sekmesi, indirme, oyun guncellemesi...) icin
    en uygun adaptor secilir.
  * Giden soket, secilen adaptorun yerel IP'sine baglanir (source binding);
    boylece Windows trafigi o arayuzden cikar.
  * Secilen hat koparsa baglanti otomatik olarak diger hatta tasinir.
  * Modlar: "agirlikli" (hiza gore), "sirali" (round robin), "oncelikli".

Sinir: tek bir TCP indirmesi iki hatta bolunemez (MPTCP/karsi taraf destegi
gerekir). Bu uygulama ayni anda birden fazla baglanti kullanan islerde
iki hattin toplam kapasitesini kullanir.
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import re
import socket
import ssl
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------
VARSAYILAN_PORT = 8877
HIZ_HOST = "speed.cloudflare.com"
HIZ_YOL = "/__down?bytes=10000000"
HIZ_SURESI = 4.0
COOLDOWN_SN = 30

MOD_AGIRLIKLI = "agirlikli"
MOD_SIRALI = "sirali"
MOD_ONCELIKLI = "oncelikli"

MOD_ADLARI = {
    MOD_AGIRLIKLI: "Agirlikli (hiza gore)",
    MOD_SIRALI: "Sirali (round robin)",
    MOD_ONCELIKLI: "Oncelikli (ilk calisan)",
}


# ---------------------------------------------------------------------------
# Veri yapilari
# ---------------------------------------------------------------------------
def boyut_metni(bayt: float) -> str:
    birimler = ["B", "KB", "MB", "GB", "TB"]
    deger = float(bayt)
    for birim in birimler:
        if deger < 1024 or birim == birimler[-1]:
            if birim == "B":
                return f"{int(deger)} B"
            return f"{deger:.1f} {birim}"
        deger /= 1024
    return f"{deger:.1f} TB"


@dataclass
class Adaptor:
    ad: str
    ip: str
    gecit: str = ""
    durum: str = "Up"
    hiz: float = 0.0
    ifindex: int = 0

    @property
    def calisir(self) -> bool:
        return self.durum.lower() == "up" and bool(self.ip) and not self.ip.startswith("169.254.")


# ---------------------------------------------------------------------------
# Adaptor kesfi
# ---------------------------------------------------------------------------
_PS_KOMUT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
Get-NetIPConfiguration | Where-Object { $_.IPv4Address -and $_.IPv4DefaultGateway } |
ForEach-Object {
  $ip = ($_.IPv4Address | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress
  $ag = Get-NetAdapter -Name $_.InterfaceAlias
  [pscustomobject]@{
    ad      = $_.InterfaceAlias
    ip      = $ip
    gecit   = $_.IPv4DefaultGateway.NextHop
    durum   = $ag.Status
    ifindex = $ag.ifIndex
  }
} | ConvertTo-Json -Compress
"""


def adaptorleri_bul() -> list[Adaptor]:
    """Default gecidi olan, IPv4 adresi bulunan adaptorleri listeler."""
    try:
        sonuc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_KOMUT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=40,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    metin = (sonuc.stdout or "").strip()
    if not metin:
        return []
    try:
        veri = json.loads(metin)
    except json.JSONDecodeError:
        return []
    if isinstance(veri, dict):
        veri = [veri]

    adaptorler: list[Adaptor] = []
    for kayit in veri:
        ip = (kayit.get("ip") or "").strip()
        if not ip or ip.startswith("169.254.") or ip.startswith("127."):
            continue
        adaptorler.append(
            Adaptor(
                ad=(kayit.get("ad") or "?").strip(),
                ip=ip,
                gecit=(kayit.get("gecit") or "").strip(),
                durum=(kayit.get("durum") or "?").strip(),
                ifindex=int(kayit.get("ifindex") or 0),
            )
        )
    return adaptorler


# ---------------------------------------------------------------------------
# Hiz olcumu
# ---------------------------------------------------------------------------
def hiz_olc(ip: str, sure: float = HIZ_SURESI) -> float:
    """Belirtilen yerel IP'ye baglanarak indirme hizini Mbps olarak olcer."""
    baglanti = None
    try:
        baglanti = http.client.HTTPSConnection(
            HIZ_HOST,
            timeout=8,
            context=ssl.create_default_context(),
            source_address=(ip, 0),
        )
        basla = time.monotonic()
        baglanti.request("GET", HIZ_YOL, headers={"User-Agent": "BaglantiBirlestirici/1.0"})
        cevap = baglanti.getresponse()
        okunan = 0
        while time.monotonic() - basla < sure:
            parca = cevap.read(65536)
            if not parca:
                break
            okunan += len(parca)
        gecen = max(time.monotonic() - basla, 0.001)
        return round((okunan * 8) / gecen / 1_000_000, 2)
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return 0.0
    finally:
        if baglanti is not None:
            try:
                baglanti.close()
            except Exception:
                pass


def hizlari_olc(adaptorler: list[Adaptor], paralel: bool = True, ilerleme=None) -> list[Adaptor]:
    """Tum adaptorlerin hizini olcer (paralel olursa daha adil sonuc)."""
    if paralel:
        sonuclar: dict[str, float] = {}

        def isci(adaptor: Adaptor) -> None:
            sonuclar[adaptor.ip] = hiz_olc(adaptor.ip)
            if ilerleme:
                ilerleme(adaptor)

        isler = [threading.Thread(target=isci, args=(a,), daemon=True) for a in adaptorler]
        for is_ in isler:
            is_.start()
        for is_ in isler:
            is_.join(timeout=15)
        for adaptor in adaptorler:
            adaptor.hiz = sonuclar.get(adaptor.ip, 0.0)
        # sifir cikan olursa bir kez daha dene (gecici dalgalanma olabilir)
        for adaptor in adaptorler:
            if adaptor.hiz <= 0:
                adaptor.hiz = hiz_olc(adaptor.ip, sure=2.0)
    else:
        for adaptor in adaptorler:
            adaptor.hiz = hiz_olc(adaptor.ip)
            if ilerleme:
                ilerleme(adaptor)
    return adaptorler


# ---------------------------------------------------------------------------
# Baglanti secici
# ---------------------------------------------------------------------------
class Secici:
    """Her yeni baglanti icin hangi adaptorun kullanilacagina karar verir."""

    def __init__(self, adaptorler: list[Adaptor], mod: str = MOD_AGIRLIKLI):
        self.adaptorler = list(adaptorler)
        self.mod = mod
        self._sira = 0
        self._carpan: dict[str, float] = {a.ip: 0.0 for a in self.adaptorler}
        self._cooldown: dict[str, float] = {}
        self._kilit = threading.RLock()

    # -- durum ------------------------------------------------------------
    def adaptor_guncelle(self, ip: str, hiz: float | None = None, durum: str | None = None) -> None:
        for adaptor in self.adaptorler:
            if adaptor.ip == ip:
                if hiz is not None:
                    adaptor.hiz = hiz
                if durum is not None:
                    adaptor.durum = durum

    def basarisiz(self, ip: str) -> None:
        with self._kilit:
            self._cooldown[ip] = time.monotonic() + COOLDOWN_SN

    def kullanilabilir(self) -> list[Adaptor]:
        simdi = time.monotonic()
        with self._kilit:
            return [
                a
                for a in self.adaptorler
                if a.calisir and self._cooldown.get(a.ip, 0.0) <= simdi
            ]

    # -- secim ------------------------------------------------------------
    def sec(self, atla: set[str] | None = None) -> Adaptor | None:
        atla = atla or set()
        with self._kilit:
            adaylar = [a for a in self.kullanilabilir() if a.ip not in atla]
            if not adaylar:
                return None

            if self.mod == MOD_ONCELIKLI:
                return adaylar[0]

            if self.mod == MOD_SIRALI:
                toplam = len(self.adaptorler)
                for _ in range(toplam):
                    adaptor = self.adaptorler[self._sira % toplam]
                    self._sira += 1
                    if adaptor.ip in atla or adaptor not in adaylar:
                        continue
                    return adaptor
                return adaylar[0]

            # Agirlikli: smooth weighted round robin
            for adaptor in self.adaptorler:
                self._carpan[adaptor.ip] = self._carpan.get(adaptor.ip, 0.0) + max(
                    adaptor.hiz, 1.0
                )
            toplam_agirlik = sum(self._carpan.get(a.ip, 0.0) for a in self.adaptorler)
            en_iyi = max(adaylar, key=lambda a: self._carpan.get(a.ip, 0.0))
            self._carpan[en_iyi.ip] = self._carpan.get(en_iyi.ip, 0.0) - toplam_agirlik
            return en_iyi

    def ozet(self) -> str:
        parcalar = []
        for adaptor in self.adaptorler:
            durum = "aktif" if adaptor.calisir else "pasif"
            parcalar.append(f"{adaptor.ad} ({adaptor.ip}, {durum}, {adaptor.hiz:.1f} Mbps)")
        return " | ".join(parcalar)


# ---------------------------------------------------------------------------
# Istatistik
# ---------------------------------------------------------------------------
class Istatistik:
    def __init__(self) -> None:
        self._kilit = threading.Lock()
        self._veri: dict[str, dict[str, float]] = {}
        self.toplam_baglanti = 0
        self.baslangic = time.monotonic()

    def _kayit(self, ad: str) -> dict[str, float]:
        return self._veri.setdefault(
            ad, {"baglanti": 0.0, "gelen": 0.0, "giden": 0.0}
        )

    def baglanti_arttir(self, ad: str) -> None:
        with self._kilit:
            self._kayit(ad)["baglanti"] += 1
            self.toplam_baglanti += 1

    def bayt_ekle(self, ad: str, gelen: int, giden: int) -> None:
        with self._kilit:
            kayit = self._kayit(ad)
            kayit["gelen"] += gelen
            kayit["giden"] += giden

    def ozet(self) -> dict[str, dict[str, float]]:
        with self._kilit:
            return {ad: dict(deger) for ad, deger in self._veri.items()}

    def toplam(self) -> tuple[float, float]:
        with self._kilit:
            gelen = sum(k["gelen"] for k in self._veri.values())
            giden = sum(k["giden"] for k in self._veri.values())
            return gelen, giden


# ---------------------------------------------------------------------------
# Proxy sunucusu
# ---------------------------------------------------------------------------
class BirlesikProxy:
    """SOCKS5 + HTTP proxy'yi ayni portta calistirir."""

    def __init__(self, secici: Secici, istatistik: Istatistik, host: str = "127.0.0.1", port: int = VARSAYILAN_PORT):
        self.secici = secici
        self.istatistik = istatistik
        self.host = host
        self.port = port
        self._sunucu: asyncio.AbstractServer | None = None
        self._gorevler: set[asyncio.Task] = set()

    # -- yasam dongusu ----------------------------------------------------
    async def basla(self) -> None:
        self._sunucu = await asyncio.start_server(self._istemci, self.host, self.port)
        await self._sunucu.start_serving()

    async def dur(self) -> None:
        if self._sunucu is not None:
            self._sunucu.close()
            await self._sunucu.wait_closed()
            self._sunucu = None
        for gorev in list(self._gorevler):
            gorev.cancel()
        self._gorevler.clear()

    # -- istemci karsilama -------------------------------------------------
    async def _istemci(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        gorev = asyncio.current_task()
        if gorev:
            self._gorevler.add(gorev)
        try:
            ilk = await asyncio.wait_for(reader.readexactly(1), timeout=15)
            if ilk == b"\x05":
                await self._socks5(reader, writer, ilk)
            else:
                await self._http(reader, writer, ilk)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionResetError):
            pass
        except Exception:
            pass
        finally:
            if gorev:
                self._gorevler.discard(gorev)
            try:
                writer.close()
            except Exception:
                pass

    # -- upstream ----------------------------------------------------------
    async def _baglan(self, hedef: str, port: int):
        atla: set[str] = set()
        son_hata: Exception | None = None
        deneme = 0
        while deneme < max(len(self.secici.adaptorler), 1) + 1:
            deneme += 1
            adaptor = self.secici.sec(atla)
            if adaptor is None:
                break
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(hedef, port, local_addr=(adaptor.ip, 0)),
                    timeout=12,
                )
                self.istatistik.baglanti_arttir(adaptor.ad)
                return reader, writer, adaptor
            except (OSError, asyncio.TimeoutError) as hata:
                son_hata = hata
                atla.add(adaptor.ip)
                self.secici.basarisiz(adaptor.ip)
        raise son_hata or OSError("Kullanilabilir baglanti hatti yok")

    async def _aktar(self, kaynak, hedef, yon: str, adaptor: Adaptor) -> None:
        try:
            while True:
                veri = await kaynak.read(65536)
                if not veri:
                    break
                hedef.write(veri)
                await hedef.drain()
                if yon == "gelen":
                    self.istatistik.bayt_ekle(adaptor.ad, len(veri), 0)
                else:
                    self.istatistik.bayt_ekle(adaptor.ad, 0, len(veri))
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, OSError):
            pass

    async def _kopru(self, istemci_r, istemci_w, hedef_r, hedef_w, adaptor: Adaptor) -> None:
        gorevler = [
            asyncio.create_task(self._aktar(istemci_r, hedef_w, "giden", adaptor)),
            asyncio.create_task(self._aktar(hedef_r, istemci_w, "gelen", adaptor)),
        ]
        try:
            await asyncio.wait(gorevler, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for gorev in gorevler:
                gorev.cancel()
            for writer in (hedef_w, istemci_w):
                try:
                    writer.close()
                except Exception:
                    pass

    # -- SOCKS5 ------------------------------------------------------------
    async def _socks5(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, surum: bytes) -> None:
        if surum != b"\x05":
            return
        nmethods = (await reader.readexactly(1))[0]
        await reader.readexactly(nmethods)
        writer.write(b"\x05\x00")
        await writer.drain()

        istek_surum = (await reader.readexactly(1))[0]
        if istek_surum != 5:
            return
        cmd, _, atyp = await reader.readexactly(3)
        if cmd != 1:
            writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return

        if atyp == 1:
            hedef = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 3:
            uzunluk = (await reader.readexactly(1))[0]
            hedef = (await reader.readexactly(uzunluk)).decode("idna", "replace")
        elif atyp == 4:
            hedef = str(ipaddress_coz(await reader.readexactly(16)))
        else:
            return
        port = struct.unpack("!H", await reader.readexactly(2))[0]

        try:
            hedef_r, hedef_w, adaptor = await self._baglan(hedef, port)
        except OSError:
            writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            return

        writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        await self._kopru(reader, writer, hedef_r, hedef_w, adaptor)

    # -- HTTP proxy --------------------------------------------------------
    async def _http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, ilk_bayt: bytes) -> None:
        try:
            basliklar = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return
        basliklar = ilk_bayt + basliklar
        metin = basliklar.decode("latin-1", "replace")

        satirlar = metin.split("\r\n")
        istek_satiri = satirlar[0]
        parcalar = istek_satiri.split(" ")
        if len(parcalar) < 3:
            return
        yontem, hedef_yol, surum = parcalar[0], parcalar[1], parcalar[2]

        # CONNECT: HTTPS tuneli
        if yontem.upper() == "CONNECT":
            if ":" in hedef_yol:
                hedef, port_metni = hedef_yol.rsplit(":", 1)
                try:
                    port = int(port_metni)
                except ValueError:
                    port = 443
            else:
                hedef, port = hedef_yol, 443
            try:
                hedef_r, hedef_w, adaptor = await self._baglan(hedef, port)
            except OSError:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                await writer.drain()
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await self._kopru(reader, writer, hedef_r, hedef_w, adaptor)
            return

        # Duzyazi (absolute-form) istek
        parca = urlsplit(hedef_yol)
        if parca.scheme not in ("http", ""):
            writer.write(b"HTTP/1.1 501 Not Implemented\r\n\r\n")
            await writer.drain()
            return
        hedef = parca.hostname
        port = parca.port or 80
        if not hedef:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            await writer.drain()
            return

        if parca.path == "":
            yol = "/"
        else:
            yol = parca.path
        if parca.query:
            yol += "?" + parca.query

        temiz_basliklar = []
        for satir in satirlar[1:]:
            if not satir:
                continue
            alt = satir.lower()
            if alt.startswith(("proxy-", "connection:", "keep-alive:")):
                continue
            temiz_basliklar.append(satir)
        temiz_basliklar.append("Connection: close")
        yeni_istek = f"{yontem} {yol} {surum}\r\n" + "\r\n".join(temiz_basliklar) + "\r\n\r\n"

        try:
            hedef_r, hedef_w, adaptor = await self._baglan(hedef, port)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            await writer.drain()
            return

        hedef_w.write(yeni_istek.encode("latin-1", "replace"))
        await hedef_w.drain()
        await self._kopru(reader, writer, hedef_r, hedef_w, adaptor)


def ipaddress_coz(ham: bytes):
    import ipaddress

    return ipaddress.IPv6Address(ham)


# ---------------------------------------------------------------------------
# Sunucu yoneticisi (GUI dostu: ayri thread'de asyncio dongusu)
# ---------------------------------------------------------------------------
class SunucuYoneticisi:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._proxy: BirlesikProxy | None = None
        self._durdur: asyncio.Event | None = None
        self.son_hata: str | None = None

    @property
    def calisiyor(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def baslat(self, secici: Secici, istatistik: Istatistik, port: int = VARSAYILAN_PORT) -> bool:
        if self.calisiyor:
            return False

        self.son_hata = None
        hazir = threading.Event()

        def calistir() -> None:
            try:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
                self._loop.run_until_complete(self._ana(secici, istatistik, port, hazir))
            except Exception as hata:  # noqa: BLE001
                self.son_hata = str(hata)
            finally:
                if self._loop is not None:
                    self._loop.close()
                    self._loop = None

        self._thread = threading.Thread(target=calistir, name="birlesik-proxy", daemon=True)
        self._thread.start()
        hazir.wait(timeout=8)
        return self.son_hata is None

    async def _ana(self, secici: Secici, istatistik: Istatistik, port: int, hazir: threading.Event) -> None:
        self._durdur = asyncio.Event()
        self._proxy = BirlesikProxy(secici, istatistik, port=port)
        try:
            await self._proxy.basla()
        except OSError as hata:
            self.son_hata = f"Sunucu baslatilamadi: {hata}"
            hazir.set()
            return
        hazir.set()
        await self._durdur.wait()
        await self._proxy.dur()

    def durdur(self) -> None:
        if self._loop is not None and self._durdur is not None:
            self._loop.call_soon_threadsafe(self._durdur.set)
        if self._thread is not None:
            self._thread.join(timeout=8)
        self._thread = None
        self._proxy = None


# ---------------------------------------------------------------------------
# Windows sistem proxy'si
# ---------------------------------------------------------------------------
_WININET = None


def _wininet_yenile() -> None:
    global _WININET
    try:
        import ctypes

        if _WININET is None:
            _WININET = ctypes.windll.wininet
        # INTERNET_OPTION_SETTINGS_CHANGED = 39, INTERNET_OPTION_REFRESH = 37
        _WININET.InternetSetOptionW(0, 39, 0, 0)
        _WININET.InternetSetOptionW(0, 37, 0, 0)
    except Exception:
        pass


def sistem_proxy_ayarla(port: int = VARSAYILAN_PORT, host: str = "127.0.0.1") -> bool:
    try:
        import winreg

        anahtar_yolu = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, anahtar_yolu, 0, winreg.KEY_SET_VALUE) as anahtar:
            winreg.SetValueEx(anahtar, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(anahtar, "ProxyServer", 0, winreg.REG_SZ, f"{host}:{port}")
            winreg.SetValueEx(anahtar, "ProxyOverride", 0, winreg.REG_SZ, "<local>")
        _wininet_yenile()
        return True
    except OSError:
        return False


def sistem_proxy_kaldir() -> bool:
    try:
        import winreg

        anahtar_yolu = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, anahtar_yolu, 0, winreg.KEY_SET_VALUE) as anahtar:
            winreg.SetValueEx(anahtar, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        _wininet_yenile()
        return True
    except OSError:
        return False


def sistem_proxy_durumu() -> str | None:
    try:
        import winreg

        anahtar_yolu = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, anahtar_yolu) as anahtar:
            try:
                etkin, _ = winreg.QueryValueEx(anahtar, "ProxyEnable")
            except FileNotFoundError:
                return None
            if not etkin:
                return None
            try:
                sunucu, _ = winreg.QueryValueEx(anahtar, "ProxyServer")
            except FileNotFoundError:
                return None
            return str(sunucu)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Ping / gecikme olcumu (Oyun Modu)
# ---------------------------------------------------------------------------
def hedef_coz(hedef: str) -> str:
    """Alan adi verilmisse IPv4 adresine cevirir."""
    hedef = hedef.strip()
    try:
        socket.inet_aton(hedef)
        return hedef
    except OSError:
        pass
    try:
        return socket.gethostbyname(hedef)
    except OSError:
        return hedef


def ping_olc(kaynak_ip: str, hedef: str, adet: int = 10, bekleme_ms: int = 1000) -> dict:
    """Belirtilen adaptorden hedefe ping atar.

    Donus: {"hedef_ip", "ort", "min", "max", "jitter", "kayip", "hata"}
    Degerler milisaniye; "kayip" yuzde. Hata varsa "hata" dolu olur.
    """
    hedef_ip = hedef_coz(hedef)
    try:
        sonuc = subprocess.run(
            [
                "ping.exe", "-4", "-n", str(adet), "-w", str(bekleme_ms),
                "-S", kaynak_ip, hedef_ip,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=adet * 2 + 15,
        )
    except (OSError, subprocess.SubprocessError) as hata:
        return {"hedef_ip": hedef_ip, "ort": 0.0, "min": 0.0, "max": 0.0,
                "jitter": 0.0, "kayip": 100.0, "hata": str(hata)}

    cikti = sonuc.stdout or ""
    sureler = [int(deger) for deger in re.findall(r"(?:time|süre|sure|zaman)[=<]\s*(\d+)\s*ms", cikti, re.IGNORECASE)]
    yanit_yok = "100%" in cikti and not sureler

    if not sureler:
        return {"hedef_ip": hedef_ip, "ort": 0.0, "min": 0.0, "max": 0.0,
                "jitter": 0.0, "kayip": 100.0, "hata": "yanit yok" if yanit_yok else "olcum basarisiz"}

    kayip = max(0.0, (adet - len(sureler)) / adet * 100.0)
    jitter = 0.0
    if len(sureler) > 1:
        jitter = sum(abs(sureler[i] - sureler[i - 1]) for i in range(1, len(sureler))) / (len(sureler) - 1)

    return {
        "hedef_ip": hedef_ip,
        "ort": round(sum(sureler) / len(sureler), 1),
        "min": float(min(sureler)),
        "max": float(max(sureler)),
        "jitter": round(jitter, 1),
        "kayip": round(kayip, 1),
        "hata": "",
    }


def ping_skoru(sonuc: dict) -> float:
    """Oyun icin dusuk olmasi gereken tek sayi: ping + jitter + kayip cezasi."""
    if sonuc.get("hata"):
        return 10000.0
    return round(
        sonuc["ort"] + sonuc["jitter"] * 1.5 + sonuc["kayip"] * 20.0,
        1,
    )


def ping_hepsi(adaptorler: list[Adaptor], hedef: str, adet: int = 10, bekleme_ms: int = 700) -> list[tuple[Adaptor, dict]]:
    """Tum hatlar icin paralel ping olcumu yapar."""
    sonuclar: dict[str, dict] = {}
    kilit = threading.Lock()

    def isci(adaptor: Adaptor) -> None:
        try:
            sonuc = ping_olc(adaptor.ip, hedef, adet, bekleme_ms)
        except Exception as hata:  # noqa: BLE001
            sonuc = {"hedef_ip": hedef, "ort": 0.0, "min": 0.0, "max": 0.0,
                     "jitter": 0.0, "kayip": 100.0, "hata": str(hata)}
        with kilit:
            sonuclar[adaptor.ip] = sonuc

    isler = [threading.Thread(target=isci, args=(a,), daemon=True) for a in adaptorler]
    for is_ in isler:
        is_.start()
    for is_ in isler:
        is_.join(timeout=adet * 2 + 20)

    return [(adaptor, sonuclar.get(adaptor.ip, {"hedef_ip": hedef, "ort": 0.0, "min": 0.0,
                                                "max": 0.0, "jitter": 0.0, "kayip": 100.0,
                                                "hata": "olculmedi"}))
            for adaptor in adaptorler]


# ---------------------------------------------------------------------------
# Windows rota metrigi (yonetici yardimcisi bu fonksiyonlari kullanir)
# ---------------------------------------------------------------------------
def metrikleri_oku() -> dict[str, int]:
    """Calisan arayuzlerin IPv4 interface metrigini okur."""
    komut = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-NetIPInterface -AddressFamily IPv4 | Where-Object { $_.ConnectionState -eq 'Connected' -and "
        "$_.InterfaceAlias -notmatch 'Loopback' } | "
        "ForEach-Object { [pscustomobject]@{ ad = $_.InterfaceAlias; metrik = $_.InterfaceMetric } } | "
        "ConvertTo-Json -Compress"
    )
    try:
        sonuc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", komut],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
        )
        veri = json.loads((sonuc.stdout or "").strip() or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    if isinstance(veri, dict):
        veri = [veri]
    return {str(k.get("ad")): int(k.get("metrik", 0)) for k in veri if k.get("ad")}


def metrik_ayarla(ad: str, deger: int) -> bool:
    """Tek bir arayuzun interface metrigini ayarlar (YONETICI gerekir).

    netsh, yonetici olmadan da hata kodu 0 dondurebildigi icin cagiran taraf
    sonucu `metrikleri_oku()` ile dogrulamalidir.
    """
    ad_guvenli = ad.replace("'", "''")
    komut = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        f"Set-NetIPInterface -InterfaceAlias '{ad_guvenli}' -AddressFamily IPv4 "
        f"-InterfaceMetric {int(deger)} -ErrorAction Stop; 'TAMAM'"
    )
    try:
        sonuc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", komut],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
        )
        return sonuc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def wifi_guc_tasarrufu_kapat() -> bool:
    """Kablosuz adaptorun guc tasarrufunu kapatir (YONETICI gerekir)."""
    # 19cbb8fa-... = Kablosuz Bagdastirici Ayarlari, 12bbebe6-... = Guc Tasarrufu Modu
    alt_anahtar = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1"
    ayar = "12bbebe6-58d6-4636-95bb-3217ef867c1a"
    komutlar = [
        ["powercfg", "/setacvalueindex", "SCHEME_CURRENT", alt_anahtar, ayar, "0"],
        ["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", alt_anahtar, ayar, "0"],
        ["powercfg", "/setactive", "SCHEME_CURRENT"],
    ]
    for komut in komutlar:
        try:
            sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return False
        if sonuc.returncode != 0:
            return False

    # dogrulama: ayarin tum indeksleri 0 olmali
    try:
        sorgu = subprocess.run(
            ["powercfg", "/query", "SCHEME_CURRENT", alt_anahtar, ayar],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if sorgu.returncode != 0:
            return False
        degerler = re.findall(r"0x[0-9A-Fa-f]{8}", sorgu.stdout or "")
        return bool(degerler) and all(deger.lower() == "0x00000000" for deger in degerler)
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------
# Ikili yonlendirme (Oyun Modu v2): rota + metrik
# ---------------------------------------------------------------------------
def _maske_cevir(prefix: int) -> str:
    """CIDR on ekini (0-32) noktali IPv4 maskesine cevirir."""
    prefix = max(0, min(32, int(prefix)))
    tam = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF if prefix else 0
    return ".".join(str((tam >> (8 * i)) & 0xFF) for i in range(3, -1, -1))


def _ag_adresi(ip: str, prefix: int) -> str:
    """IP + on ekten ag (network) adresini hesaplar."""
    try:
        return str(ipaddress.ip_network(f"{ip}/{int(prefix)}", strict=False).network_address)
    except ValueError:
        return ip


def host_ips(hedef: str) -> list[str]:
    """Hedefi rota icin IPv4 listesine cevirir.

    '8.8.8.8' -> ['8.8.8.8']; '1.2.3.0/24' -> ['1.2.3.0/24'] (CIDR aynen);
    'example.com' -> IPv4 listesi (tekrarsiz, en fazla 16); cozumlenemezse -> [].
    """
    hedef = (hedef or "").strip()
    if not hedef:
        return []
    if "/" in hedef:
        return [hedef]
    try:
        socket.inet_aton(hedef)
        return [hedef]
    except OSError:
        pass
    ips: list[str] = []
    try:
        for kayit in socket.getaddrinfo(hedef, 0, socket.AF_INET):
            ip = kayit[4][0]
            if ip not in ips:
                ips.append(ip)
            if len(ips) >= 16:
                break
    except OSError:
        return []
    return ips


def rota_ekle(hedef: str, gecit: str, ifindex: int, metrik: int) -> bool:
    """Statik IPv4 rotasi ekler (YONETICI gerekir).

    /32 icin: route ADD <ip> MASK 255.255.255.255 <gecit> METRIC <m> IF <if>
    CIDR 'a.b.c.d/n' icin: route ADD <net> MASK <mask> <gecit> METRIC <m> IF <if>
    """
    hedef = (hedef or "").strip()
    if not hedef:
        return False
    if "/" in hedef:
        ip_metni, on_metni = hedef.split("/", 1)
        try:
            on = int(on_metni)
        except ValueError:
            return False
        if not 0 <= on <= 32:
            return False
        ag = _ag_adresi(ip_metni, on)
        maske = _maske_cevir(on)
    else:
        ag = hedef
        maske = "255.255.255.255"
    komut = [
        "route", "ADD", ag, "MASK", maske, gecit,
        "METRIC", str(int(metrik)), "IF", str(int(ifindex)),
    ]
    try:
        sonuc = subprocess.run(
            komut, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return sonuc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def rota_sil(hedef: str) -> bool:
    """Hedef icin eklenmis statik rotayi siler (YONETICI gerekir)."""
    hedef = (hedef or "").strip()
    if not hedef:
        return False
    if "/" in hedef:
        ip_metni, on_metni = hedef.split("/", 1)
        try:
            on = int(on_metni)
        except ValueError:
            return False
        komut = ["route", "DELETE", _ag_adresi(ip_metni, on), "MASK", _maske_cevir(on)]
    else:
        komut = ["route", "DELETE", hedef]
    try:
        sonuc = subprocess.run(
            komut, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return sonuc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def rotalari_oku() -> list[dict]:
    """Aktif IPv4 rotalarini okur: [{"dest","next_hop","ifindex","metric"}]."""
    komut = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-NetRoute -AddressFamily IPv4 | "
        "ForEach-Object { [pscustomobject]@{ dest = $_.DestinationPrefix; next_hop = $_.NextHop; "
        "ifindex = $_.InterfaceIndex; metric = $_.RouteMetric } } | ConvertTo-Json -Compress"
    )
    try:
        sonuc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", komut],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40,
        )
        veri = json.loads((sonuc.stdout or "").strip() or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    if isinstance(veri, dict):
        veri = [veri]
    rotalar = []
    for kayit in veri:
        if not kayit.get("dest"):
            continue
        rotalar.append({
            "dest": str(kayit.get("dest")),
            "next_hop": str(kayit.get("next_hop")),
            "ifindex": int(kayit.get("ifindex") or 0),
            "metric": int(kayit.get("metric") or 0),
        })
    return rotalar


def ikili_plan(adaptorler: list[Adaptor], sonuclar: list[tuple[Adaptor, dict]], hedef: str) -> dict | None:
    """Ikili yonlendirme planini hesaplar (Oyun Modu v2).

    sonuclar: ping_hepsi ciktisi list[(Adaptor, dict)].
    """
    # 1) Hata olmayan sonuclardan en dusuk ping_skoru'na sahip hat en iyidir.
    saglikli = [(a, s) for (a, s) in sonuclar if not s.get("hata")]
    if not saglikli:
        return None
    en_iyi_adap, en_iyi_sonuc = min(saglikli, key=lambda ikili: ping_skoru(ikili[1]))

    # 2) Hedefi cozumle; cozumlenemezse hata dondur.
    ips = host_ips(hedef)
    if not ips:
        return {"hata": "cozumlenemedi"}

    # 3) Diger adaptor (ikinci hat).
    diger = None
    for a in adaptorler:
        if a.ip != en_iyi_adap.ip:
            diger = a
            break

    diger_sonuc = None
    if diger is not None:
        for a, s in sonuclar:
            if a.ip == diger.ip:
                diger_sonuc = s
                break

    diger_saglikli = False
    if diger is not None and diger_sonuc is not None:
        diger_saglikli = not diger_sonuc.get("hata") and (diger_sonuc.get("kayip", 100.0) < 50.0)

    tek_hat_modu = diger is None or not diger_saglikli

    return {
        "hedefler": ips,
        "en_iyi_hat": {"ad": en_iyi_adap.ad, "gecit": en_iyi_adap.gecit, "ifindex": en_iyi_adap.ifindex},
        "diger_hat": ({"ad": diger.ad, "gecit": diger.gecit, "ifindex": diger.ifindex} if diger is not None else None),
        "tek_hat_modu": tek_hat_modu,
        "en_iyi_skor": float(ping_skoru(en_iyi_sonuc)),
        "diger_saglikli": diger_saglikli,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> None:
    adaptorler = adaptorleri_bul()
    if not adaptorler:
        print("Hicbir internet adaptoru bulunamadi.")
        return
    print("Bulunan adaptorler:")
    for adaptor in adaptorler:
        print(f"  - {adaptor.ad:12s} {adaptor.ip:16s} gecit={adaptor.gecit or '-':16s} {adaptor.durum}")

    print("\nHiz olcumu yapiliyor (yaklasik 3 saniye)...")
    hizlari_olc(adaptorler)
    for adaptor in adaptorler:
        print(f"  - {adaptor.ad:12s} {adaptor.hiz:6.1f} Mbps")

    toplam = sum(a.hiz for a in adaptorler)
    print(f"\nToplam olculen kapasite: {toplam:.1f} Mbps")
    print(f"Sistem proxy durumu: {sistem_proxy_durumu() or 'kapali'}")


if __name__ == "__main__":
    _cli()
