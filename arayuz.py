"""Baglanti Birlestirici - masaustu arayuzu (Tkinter).

Iki sekme:
  * Birlestirme: Wi-Fi + Ethernet/USB hatlarini tek proxy uzerinden birlestirir.
  * Oyun Modu  : hat bazinda ping/jitter/kayip olcer, en iyi hatti Windows rota
                 onceligine uygular (yonetici onayi ile), canli ping grafigi cizer.

Calistirma:  python arayuz.py
Ekran goruntusu: python arayuz.py --ekran-goruntusu cikti.png [--canli] [--sekme oyun]
"""

from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cekirdek

RENK = {
    "arka": "#10121a",
    "panel": "#191c27",
    "panel2": "#222736",
    "cizgi": "#2e3446",
    "metin": "#e9edf7",
    "soluk": "#95a0b6",
    "vurgu": "#ffb03b",
    "yesil": "#4ade80",
    "mavi": "#4c8dff",
    "kirmizi": "#f87171",
}
YAZI = "Segoe UI"

KLASOR = Path(__file__).resolve().parent
YEDEK_YOLU = KLASOR / "metrik_yedek.json"
SPEC_YOLU = KLASOR / "metrik_spec.json"
SONUC_YOLU = KLASOR / "yonetici_sonuc.json"
GRAFIK_NOKTA = 60


def _dpi_ayarla() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _yonetici_mi() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _python_yolu() -> str:
    """Yonetici yardimcisini baslatmak icin python.exe yolu."""
    aday = Path(sys.executable)
    if aday.name.lower() == "pythonw.exe":
        konsol = aday.with_name("python.exe")
        if konsol.exists():
            return str(konsol)
    return str(aday)


class Uygulama:
    def __init__(self, kok: tk.Tk) -> None:
        self.kok = kok
        self.kok.title("Bağlantı Birleştirici")
        self.kok.geometry("1060x780")
        self.kok.minsize(920, 680)
        self.kok.configure(bg=RENK["arka"])

        self.adaptorler: list[cekirdek.Adaptor] = []
        self.secici: cekirdek.Secici | None = None
        self.istatistik = cekirdek.Istatistik()
        self.yonetici = cekirdek.SunucuYoneticisi()
        self.kuyruk: queue.Queue[tuple] = queue.Queue()

        self._onceki_bayt = 0.0
        self._onceki_zaman = time.monotonic()
        self._hiz_olcuyor = False

        self.ping_sonuclari: list[tuple[cekirdek.Adaptor, dict]] = []
        self._ping_calisiyor = False
        self._canli_calisiyor = False
        self._canli_veriler: list[float | None] = []
        self._canli_toplam = 0
        self._canli_kayip = 0

        self._stil_ayarla()
        self._arayuz_kur()
        self._adaptorleri_yukle()
        self._durum_guncelle()
        self._kuyruk_isle()

    # =====================================================================
    # Stil
    # =====================================================================
    def _stil_ayarla(self) -> None:
        stil = ttk.Style(self.kok)
        try:
            stil.theme_use("clam")
        except tk.TclError:
            pass

        stil.configure(".", background=RENK["arka"], foreground=RENK["metin"], font=(YAZI, 9))
        stil.configure("K.TFrame", background=RENK["panel"])
        stil.configure("K.TLabel", background=RENK["panel"], foreground=RENK["metin"])
        stil.configure("Bolum.TLabel", background=RENK["panel"], foreground=RENK["soluk"], font=(YAZI, 8, "bold"))

        stil.configure("TButton", background=RENK["panel2"], foreground=RENK["metin"], borderwidth=0,
                       focuscolor=RENK["panel2"], padding=(12, 7))
        stil.map("TButton",
                 background=[("active", RENK["cizgi"]), ("disabled", RENK["panel2"])],
                 foreground=[("disabled", RENK["soluk"])])
        stil.configure("Vurgu.TButton", background=RENK["vurgu"], foreground="#14161f",
                       font=(YAZI, 9, "bold"), padding=(14, 7))
        stil.map("Vurgu.TButton", background=[("active", "#ffc46a"), ("disabled", "#6d5a33")])
        stil.configure("Mavi.TButton", background=RENK["mavi"], foreground="#0d1119",
                       font=(YAZI, 9, "bold"), padding=(14, 7))
        stil.map("Mavi.TButton", background=[("active", "#6ba2ff"), ("disabled", "#33507f")])
        stil.configure("Kirmizi.TButton", background="#7f2b2b", foreground="#ffe9e9", padding=(12, 7))
        stil.map("Kirmizi.TButton", background=[("active", "#a33a3a")])

        stil.configure("K.TRadiobutton", background=RENK["panel"], foreground=RENK["metin"])
        stil.configure("K.TEntry", fieldbackground=RENK["panel2"], foreground=RENK["metin"],
                       insertcolor=RENK["metin"], borderwidth=0, padding=6)

        stil.configure("Treeview", background=RENK["panel2"], fieldbackground=RENK["panel2"],
                       foreground=RENK["metin"], rowheight=27, borderwidth=0)
        stil.configure("Treeview.Heading", background=RENK["panel"], foreground=RENK["soluk"],
                       borderwidth=0, font=(YAZI, 8, "bold"))
        stil.map("Treeview", background=[("selected", "#2c3a5e")], foreground=[("selected", RENK["metin"])])

        stil.configure("Horizontal.TProgressbar", troughcolor=RENK["panel2"], background=RENK["vurgu"],
                       bordercolor=RENK["panel2"], lightcolor=RENK["vurgu"], darkcolor=RENK["vurgu"],
                       borderwidth=0, thickness=6)

        stil.configure("TNotebook", background=RENK["arka"], borderwidth=0)
        stil.configure("TNotebook.Tab", background=RENK["panel"], foreground=RENK["soluk"],
                       padding=(18, 8), borderwidth=0, font=(YAZI, 9, "bold"))
        stil.map("TNotebook.Tab",
                 background=[("selected", RENK["panel2"])],
                 foreground=[("selected", RENK["vurgu"])])

    # =====================================================================
    # Genel arayuz
    # =====================================================================
    def _arayuz_kur(self) -> None:
        ust = tk.Frame(self.kok, bg=RENK["arka"])
        ust.pack(fill="x", padx=22, pady=(14, 0))
        tk.Label(ust, text="Bağlantı Birleştirici", bg=RENK["arka"], fg=RENK["metin"],
                 font=(YAZI, 20, "bold")).pack(anchor="w")
        tk.Label(ust, text="Hattı birleştir · en düşük ping'i seç · canlı izle",
                 bg=RENK["arka"], fg=RENK["soluk"], font=(YAZI, 9)).pack(anchor="w", pady=(2, 0))

        self.defter = ttk.Notebook(self.kok)
        self.defter.pack(fill="both", expand=True, padx=18, pady=(10, 12))

        self.sayfa_birlestirme = tk.Frame(self.defter, bg=RENK["arka"])
        self.sayfa_oyun = tk.Frame(self.defter, bg=RENK["arka"])
        self.defter.add(self.sayfa_birlestirme, text="  Birleştirme  ")
        self.defter.add(self.sayfa_oyun, text="  Oyun Modu  ")

        self._birlestirme_kur(self.sayfa_birlestirme)
        self._oyun_kur(self.sayfa_oyun)

    # ---------------------------------------------------------------------
    # Sekme 1: Birlestirme
    # ---------------------------------------------------------------------
    def _birlestirme_kur(self, sayfa: tk.Frame) -> None:
        kart = tk.Frame(sayfa, bg=RENK["panel"], highlightthickness=1, highlightbackground=RENK["cizgi"])
        kart.pack(fill="x", padx=4, pady=(12, 0))
        tk.Label(kart, text="HATLAR", bg=RENK["panel"], fg=RENK["soluk"],
                 font=(YAZI, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 6))

        cerceve = tk.Frame(kart, bg=RENK["panel"])
        cerceve.pack(fill="x", padx=14, pady=(0, 10))
        sutunlar = ("ad", "ip", "gecit", "hiz", "baglanti", "gelen", "giden")
        self.tablo = ttk.Treeview(cerceve, columns=sutunlar, show="headings", height=3, selectmode="none")
        for anahtar, (baslik, genislik) in {
            "ad": ("Hat", 190), "ip": ("IP adresi", 130), "gecit": ("Ağ geçidi", 130),
            "hiz": ("Ölçülen hız", 105), "baglanti": ("Bağlantı", 80),
            "gelen": ("Gelen", 95), "giden": ("Giden", 95),
        }.items():
            self.tablo.heading(anahtar, text=baslik)
            self.tablo.column(anahtar, width=genislik,
                              anchor="w" if anahtar in ("ad", "ip", "gecit") else "e")
        self.tablo.pack(side="left", fill="x", expand=True)

        kontrol = tk.Frame(sayfa, bg=RENK["panel"], highlightthickness=1, highlightbackground=RENK["cizgi"])
        kontrol.pack(fill="x", padx=4, pady=(12, 0))

        satir1 = tk.Frame(kontrol, bg=RENK["panel"])
        satir1.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(satir1, text="DAĞITIM", bg=RENK["panel"], fg=RENK["soluk"],
                 font=(YAZI, 8, "bold")).pack(side="left", padx=(0, 12))
        self.mod_var = tk.StringVar(value=cekirdek.MOD_AGIRLIKLI)
        for anahtar in (cekirdek.MOD_AGIRLIKLI, cekirdek.MOD_SIRALI, cekirdek.MOD_ONCELIKLI):
            ttk.Radiobutton(satir1, text=cekirdek.MOD_ADLARI[anahtar], value=anahtar,
                            variable=self.mod_var, style="K.TRadiobutton",
                            command=self._mod_degisti).pack(side="left", padx=(0, 14))
        tk.Label(satir1, text="Port:", bg=RENK["panel"], fg=RENK["soluk"]).pack(side="left", padx=(20, 6))
        self.port_var = tk.StringVar(value=str(cekirdek.VARSAYILAN_PORT))
        ttk.Entry(satir1, textvariable=self.port_var, width=7, style="K.TEntry").pack(side="left")

        satir2 = tk.Frame(kontrol, bg=RENK["panel"])
        satir2.pack(fill="x", padx=14, pady=(6, 12))
        self.btn_baslat = ttk.Button(satir2, text="Birleştirmeyi başlat", style="Vurgu.TButton",
                                     command=self._baslat)
        self.btn_baslat.pack(side="left", padx=(0, 8))
        self.btn_durdur = ttk.Button(satir2, text="Durdur", style="Kirmizi.TButton",
                                     command=self._durdur, state="disabled")
        self.btn_durdur.pack(side="left", padx=(0, 8))
        self.btn_hiz = ttk.Button(satir2, text="Hızları ölç", command=self._hiz_olc)
        self.btn_hiz.pack(side="left", padx=(0, 8))
        self.btn_yenile = ttk.Button(satir2, text="Hatları yenile", command=self._adaptorleri_yukle)
        self.btn_yenile.pack(side="left", padx=(0, 8))
        self.btn_proxy = ttk.Button(satir2, text="", style="Mavi.TButton",
                                    command=self._sistem_proxy_degistir)
        self.btn_proxy.pack(side="right")

        gunluk_kart = tk.Frame(sayfa, bg=RENK["panel"], highlightthickness=1, highlightbackground=RENK["cizgi"])
        gunluk_kart.pack(fill="both", expand=True, padx=4, pady=(12, 0))
        tk.Label(gunluk_kart, text="GÜNLÜK", bg=RENK["panel"], fg=RENK["soluk"],
                 font=(YAZI, 8, "bold")).pack(anchor="w", padx=14, pady=(10, 4))
        self.gunluk = tk.Text(gunluk_kart, height=7, bg=RENK["panel2"], fg=RENK["metin"],
                              insertbackground=RENK["metin"], relief="flat", borderwidth=0,
                              font=("Consolas", 9), padx=10, pady=8, wrap="word")
        self.gunluk.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.gunluk.configure(state="disabled")

        alt = tk.Frame(sayfa, bg=RENK["arka"])
        alt.pack(fill="x", padx=4, pady=(8, 0))
        self.ozet_var = tk.StringVar(value="Hazır.")
        tk.Label(alt, textvariable=self.ozet_var, bg=RENK["arka"], fg=RENK["soluk"],
                 font=(YAZI, 9)).pack(side="left")
        self.ilerleme = ttk.Progressbar(sayfa, mode="determinate")
        self.not_label = tk.Label(
            sayfa,
            text="Not: Tek bir indirme hızı iki hatta bölünemez; aynı anda çok bağlantı kullanan işlerde toplam kapasite kullanılır.",
            bg=RENK["arka"], fg=RENK["soluk"], font=(YAZI, 8),
            wraplength=900, justify="left",
        )
        self.not_label.pack(anchor="w", padx=4, pady=(6, 12))

    # ---------------------------------------------------------------------
    # Sekme 2: Oyun modu
    # ---------------------------------------------------------------------
    def _oyun_kur(self, sayfa: tk.Frame) -> None:
        kart = tk.Frame(sayfa, bg=RENK["panel"], highlightthickness=1, highlightbackground=RENK["cizgi"])
        kart.pack(fill="x", padx=4, pady=(12, 0))

        satir = tk.Frame(kart, bg=RENK["panel"])
        satir.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(satir, text="HEDEF", bg=RENK["panel"], fg=RENK["soluk"],
                 font=(YAZI, 8, "bold")).pack(side="left", padx=(0, 12))
        tk.Label(satir, text="Sunucu IP / adres:", bg=RENK["panel"], fg=RENK["soluk"]).pack(side="left")
        self.oyun_hedef_var = tk.StringVar(value="8.8.8.8")
        ttk.Entry(satir, textvariable=self.oyun_hedef_var, width=26, style="K.TEntry").pack(side="left", padx=8)
        self.btn_ping = ttk.Button(satir, text="Ping testi", style="Vurgu.TButton", command=self._ping_test)
        self.btn_ping.pack(side="left", padx=(4, 8))
        self.btn_metrik_goster = ttk.Button(satir, text="Metrikleri göster", command=self._metrik_goster)
        self.btn_metrik_goster.pack(side="left")

        cerceve = tk.Frame(kart, bg=RENK["panel"])
        cerceve.pack(fill="x", padx=14, pady=(0, 8))
        self.oyun_tablo = ttk.Treeview(
            cerceve,
            columns=("ad", "ip", "ping", "min", "jitter", "kayip", "skor"),
            show="headings", height=3, selectmode="none",
        )
        for anahtar, (baslik, genislik) in {
            "ad": ("Hat", 220), "ip": ("IP adresi", 130), "ping": ("Ping (ort)", 100),
            "min": ("Min", 70), "jitter": ("Jitter", 80), "kayip": ("Kayıp", 80), "skor": ("Skor", 80),
        }.items():
            self.oyun_tablo.heading(anahtar, text=baslik)
            self.oyun_tablo.column(anahtar, width=genislik,
                                   anchor="w" if anahtar in ("ad", "ip") else "e")
        self.oyun_tablo.pack(fill="x")

        self.oyun_ozet_var = tk.StringVar(value="Henüz ölçüm yapılmadı.")
        tk.Label(kart, textvariable=self.oyun_ozet_var, bg=RENK["panel"], fg=RENK["vurgu"],
                 font=(YAZI, 9, "bold"), wraplength=940, justify="left").pack(
            anchor="w", padx=14, pady=(0, 8))

        butonlar = tk.Frame(kart, bg=RENK["panel"])
        butonlar.pack(fill="x", padx=14, pady=(0, 12))
        self.btn_en_iyi = ttk.Button(butonlar, text="İki hattı da kullan + oyunu en iyiye sabitle (yönetici)",
                                     style="Mavi.TButton", command=self._en_iyi_uygula)
        self.btn_en_iyi.pack(side="left", padx=(0, 8))
        self.btn_metrik_geri = ttk.Button(butonlar, text="Metrikleri geri al",
                                          command=self._metrik_geri)
        self.btn_metrik_geri.pack(side="left", padx=(0, 8))
        self.btn_wifi_guc = ttk.Button(butonlar, text="Wi-Fi güç tasarrufunu kapat (yönetici)",
                                       command=self._wifi_guc)
        self.btn_wifi_guc.pack(side="left")

        canli_kart = tk.Frame(sayfa, bg=RENK["panel"], highlightthickness=1, highlightbackground=RENK["cizgi"])
        canli_kart.pack(fill="both", expand=True, padx=4, pady=(12, 0))
        canli_ust = tk.Frame(canli_kart, bg=RENK["panel"])
        canli_ust.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(canli_ust, text="CANLI PING", bg=RENK["panel"], fg=RENK["soluk"],
                 font=(YAZI, 8, "bold")).pack(side="left")
        self.btn_canli = ttk.Button(canli_ust, text="Canlı izlemeyi başlat", command=self._canli_degistir)
        self.btn_canli.pack(side="right")
        self.canli_ozet_var = tk.StringVar(value="Canlı izleme kapalı.")
        tk.Label(canli_ust, textvariable=self.canli_ozet_var, bg=RENK["panel"], fg=RENK["metin"],
                 font=(YAZI, 9)).pack(side="left", padx=14)

        self.grafik = tk.Canvas(canli_kart, bg=RENK["panel2"], height=110,
                                highlightthickness=0, bd=0)
        self.grafik.pack(fill="x", padx=14, pady=(0, 10))

        self.oyun_gunluk = tk.Text(canli_kart, height=5, bg=RENK["panel2"], fg=RENK["metin"],
                                   insertbackground=RENK["metin"], relief="flat", borderwidth=0,
                                   font=("Consolas", 9), padx=10, pady=8, wrap="word")
        self.oyun_gunluk.pack(fill="x", padx=14, pady=(0, 12))
        self.oyun_gunluk.configure(state="disabled")

        tk.Label(
            sayfa,
            text="Oyun sunucusu en düşük ping'li hatta sabitlenir, diğer trafik ikinci hattan gider "
                 "(iki hat da kullanılır). Tek bir oyun akışı iki hatta bölünemez; bu, linki "
                 "sıkışıklıktan koruyarak ping'i düşürür.",
            bg=RENK["arka"], fg=RENK["soluk"], font=(YAZI, 8), wraplength=900, justify="left",
        ).pack(anchor="w", padx=4, pady=(8, 12))

    # =====================================================================
    # Yardimcilar
    # =====================================================================
    def _yaz(self, mesaj: str) -> None:
        zaman = time.strftime("%H:%M:%S")
        self.gunluk.configure(state="normal")
        self.gunluk.insert("end", f"[{zaman}] {mesaj}\n")
        self.gunluk.see("end")
        self.gunluk.configure(state="disabled")

    def _yaz_oyun(self, mesaj: str) -> None:
        zaman = time.strftime("%H:%M:%S")
        self.oyun_gunluk.configure(state="normal")
        self.oyun_gunluk.insert("end", f"[{zaman}] {mesaj}\n")
        self.oyun_gunluk.see("end")
        self.oyun_gunluk.configure(state="disabled")

    def _durum_guncelle(self) -> None:
        proxy = cekirdek.sistem_proxy_durumu()
        if proxy:
            self.btn_proxy.configure(text=f"Sistem proxy'sini kaldır ({proxy})")
        else:
            self.btn_proxy.configure(text="Sistem proxy'sini ayarla")

    def _adaptorleri_yukle(self) -> None:
        self.adaptorler = cekirdek.adaptorleri_bul()
        for satir in self.tablo.get_children():
            self.tablo.delete(satir)
        for adaptor in self.adaptorler:
            self.tablo.insert("", "end", iid=adaptor.ip,
                              values=(adaptor.ad, adaptor.ip, adaptor.gecit or "-", "ölçülmedi", 0, "0 B", "0 B"))
        if self.adaptorler:
            self._yaz(f"{len(self.adaptorler)} hat bulundu: "
                      + ", ".join(f"{a.ad} ({a.ip})" for a in self.adaptorler))
        else:
            self._yaz("HATA: internet hattı bulunamadı.")
        self.secici = cekirdek.Secici(self.adaptorler, self.mod_var.get())

    def _mod_degisti(self) -> None:
        if self.secici is not None:
            self.secici.mod = self.mod_var.get()
            self._yaz(f"Dağıtım modu: {cekirdek.MOD_ADLARI[self.secici.mod]}")

    # =====================================================================
    # Birlestirme: hiz olcumu / baslat / durdur / proxy
    # =====================================================================
    def _hiz_olc(self) -> None:
        if self._hiz_olcuyor:
            return
        if not self.adaptorler:
            self._adaptorleri_yukle()
        if not self.adaptorler:
            return

        self._hiz_olcuyor = True
        self.btn_hiz.configure(state="disabled", text="Ölçülüyor…")
        self.ilerleme.configure(mode="indeterminate")
        self.ilerleme.pack(fill="x", padx=4, pady=(6, 0), before=self.not_label)
        self.ilerleme.start(12)
        self._yaz("Hız ölçümü başladı (her hat için ~4 saniye)…")

        def isci() -> None:
            try:
                cekirdek.hizlari_olc(self.adaptorler, paralel=True)
                self.kuyruk.put(("hiz_bitti", None))
            except Exception as hata:  # noqa: BLE001
                self.kuyruk.put(("hiz_hata", str(hata)))

        threading.Thread(target=isci, daemon=True).start()

    def _baslat(self) -> None:
        if not self.adaptorler:
            self._adaptorleri_yukle()
        if not self.adaptorler:
            messagebox.showerror("Hata", "İnternet hattı bulunamadı.")
            return
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Hata", "Port sayı olmalı.")
            return
        if not 1024 <= port <= 65535:
            messagebox.showerror("Hata", "Port 1024-65535 aralığında olmalı.")
            return

        if any(a.hiz <= 0 for a in self.adaptorler):
            self._yaz("İpucu: ağırlıklı dağıtım için önce 'Hızları ölç' butonuna bas (şimdilik eşit ağırlık).")

        self.secici = cekirdek.Secici(self.adaptorler, self.mod_var.get())
        self.istatistik = cekirdek.Istatistik()
        self.yonetici = cekirdek.SunucuYoneticisi()
        if not self.yonetici.baslat(self.secici, self.istatistik, port):
            messagebox.showerror("Hata", f"Proxy başlatılamadı:\n{self.yonetici.son_hata}")
            self._yaz(f"HATA: {self.yonetici.son_hata}")
            return

        self._yaz(f"Birleştirme başladı → 127.0.0.1:{port} (mod: {cekirdek.MOD_ADLARI[self.secici.mod]})")
        self._yaz(f"HTTP proxy: -x http://127.0.0.1:{port}   ·   SOCKS5: --socks5 127.0.0.1:{port}")
        self.btn_baslat.configure(state="disabled")
        self.btn_durdur.configure(state="normal")
        self.btn_hiz.configure(state="normal")
        self.ozet_var.set("Çalışıyor.")
        self._onceki_bayt = 0.0
        self._onceki_zaman = time.monotonic()
        self._canli_guncelle()

    def _durdur(self) -> None:
        self.yonetici.durdur()
        self._sistem_proxy_temizle()
        self.btn_baslat.configure(state="normal")
        self.btn_durdur.configure(state="disabled")
        self.ozet_var.set("Durduruldu.")
        self._yaz("Birleştirme durduruldu.")

    def _sistem_proxy_temizle(self) -> None:
        mevcut = cekirdek.sistem_proxy_durumu()
        if mevcut and "127.0.0.1" in mevcut:
            if cekirdek.sistem_proxy_kaldir():
                self._yaz("Sistem proxy'si otomatik kaldırıldı (internetin kesilmemesi için).")
                self._durum_guncelle()

    def _sistem_proxy_degistir(self) -> None:
        mevcut = cekirdek.sistem_proxy_durumu()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            port = cekirdek.VARSAYILAN_PORT
        if mevcut:
            if cekirdek.sistem_proxy_kaldir():
                self._yaz("Sistem proxy'si kaldırıldı.")
            else:
                self._yaz("HATA: sistem proxy'si kaldırılamadı.")
        else:
            if cekirdek.sistem_proxy_ayarla(port):
                self._yaz(f"Sistem proxy'si 127.0.0.1:{port} olarak ayarlandı (tarayıcılar artık buradan çıkar).")
            else:
                self._yaz("HATA: sistem proxy'si ayarlanamadı.")
        self._durum_guncelle()

    def _canli_guncelle(self) -> None:
        if not self.yonetici.calisiyor:
            return
        ozet = self.istatistik.ozet()
        toplam_gelen = 0.0
        for adaptor in self.adaptorler:
            deger = ozet.get(adaptor.ad, {})
            gelen = deger.get("gelen", 0.0)
            giden = deger.get("giden", 0.0)
            baglanti = int(deger.get("baglanti", 0))
            toplam_gelen += gelen
            hiz = f"{adaptor.hiz:.1f} Mbps" if adaptor.hiz > 0 else "ölçülmedi"
            if self.tablo.exists(adaptor.ip):
                self.tablo.item(adaptor.ip, values=(
                    adaptor.ad, adaptor.ip, adaptor.gecit or "-", hiz,
                    baglanti, cekirdek.boyut_metni(gelen), cekirdek.boyut_metni(giden),
                ))

        simdi = time.monotonic()
        gecen = max(simdi - self._onceki_zaman, 0.001)
        anlik = (toplam_gelen - self._onceki_bayt) * 8 / gecen / 1_000_000
        self._onceki_bayt = toplam_gelen
        self._onceki_zaman = simdi
        self.ozet_var.set(f"Toplam indirilen: {cekirdek.boyut_metni(toplam_gelen)}   ·   "
                          f"anlık: {anlik:.1f} Mbps   ·   bağlantı: {self.istatistik.toplam_baglanti}")
        self.kok.after(700, self._canli_guncelle)

    # =====================================================================
    # Oyun modu: ping testi
    # =====================================================================
    def _ping_test(self) -> None:
        if self._ping_calisiyor:
            return
        if not self.adaptorler:
            self._adaptorleri_yukle()
        if not self.adaptorler:
            messagebox.showerror("Hata", "İnternet hattı bulunamadı.")
            return

        hedef = self.oyun_hedef_var.get().strip() or "8.8.8.8"
        self._ping_calisiyor = True
        self.btn_ping.configure(state="disabled", text="Ölçülüyor…")
        self.oyun_ozet_var.set("Ping ölçümü sürüyor…")
        self._yaz_oyun(f"Ping ölçümü: hedef {hedef}, her hat için 10 paket")

        def isci() -> None:
            try:
                sonuclar = cekirdek.ping_hepsi(self.adaptorler, hedef, adet=10)
                self.kuyruk.put(("ping_bitti", sonuclar))
            except Exception as hata:  # noqa: BLE001
                self.kuyruk.put(("ping_hata", str(hata)))

        threading.Thread(target=isci, daemon=True).start()

    def _ping_sonuc_isle(self, sonuclar: list[tuple[cekirdek.Adaptor, dict]]) -> None:
        self._ping_calisiyor = False
        self.btn_ping.configure(state="normal", text="Ping testi")
        self.ping_sonuclari = sonuclar

        for satir in self.oyun_tablo.get_children():
            self.oyun_tablo.delete(satir)

        puanli = []
        for adaptor, sonuc in sonuclar:
            puan = cekirdek.ping_skoru(sonuc)
            puanli.append((puan, adaptor, sonuc))
            if sonuc.get("hata"):
                degerler = (adaptor.ad + "  ✗", adaptor.ip, "-", "-", "-", "-", "-")
            else:
                degerler = (
                    adaptor.ad, adaptor.ip,
                    f"{sonuc['ort']:.0f} ms", f"{sonuc['min']:.0f}",
                    f"{sonuc['jitter']:.0f} ms", f"%{sonuc['kayip']:.0f}",
                    f"{puan:.0f}",
                )
            self.oyun_tablo.insert("", "end", iid=adaptor.ip, values=degerler)

        puanli.sort(key=lambda kayit: kayit[0])
        if puanli:
            en_iyi_puan, en_iyi, en_iyi_sonuc = puanli[0]
            if en_iyi_sonuc.get("hata"):
                self.oyun_ozet_var.set("Hiçbir hat hedefe ulaşamadı. Hedefi kontrol et.")
                self._yaz_oyun("HATA: hiçbir hat hedefe ulaşamadı.")
                return
            self.oyun_tablo.item(en_iyi.ip, tags=("en_iyi",))
            self.oyun_tablo.tag_configure("en_iyi", foreground=RENK["yesil"])
            self.oyun_ozet_var.set(
                f"En iyi hat: {en_iyi.ad} — {en_iyi_sonuc['ort']:.0f} ms ping, "
                f"{en_iyi_sonuc['jitter']:.0f} ms jitter, %{en_iyi_sonuc['kayip']:.0f} kayıp (skor {en_iyi_puan:.0f})"
            )
            self._yaz_oyun(
                f"Ölçüm bitti. En iyi: {en_iyi.ad} (skor {en_iyi_puan:.0f}) | "
                + " · ".join(
                    f"{a.ad}: {s['ort']:.0f} ms/%{s['kayip']:.0f} kayıp" if not s.get("hata") else f"{a.ad}: yanıt yok"
                    for a, s in sonuclar
                )
            )

    def _en_iyi_hat(self) -> cekirdek.Adaptor | None:
        if not self.ping_sonuclari:
            return None
        gecerli = [(cekirdek.ping_skoru(s), a) for a, s in self.ping_sonuclari if not s.get("hata")]
        if not gecerli:
            return None
        gecerli.sort(key=lambda kayit: kayit[0])
        return gecerli[0][1]

    def _metrik_goster(self) -> None:
        metrikler = cekirdek.metrikleri_oku()
        if not metrikler:
            self._yaz_oyun("Metrikler okunamadı.")
            return
        self._yaz_oyun("Güncel interface metrikleri (küçük = öncelikli): "
                       + ", ".join(f"{ad}={deger}" for ad, deger in metrikler.items()))
        rotalar = cekirdek.rotalari_oku()
        if rotalar:
            self._yaz_oyun("Güncel rotalar: " + " · ".join(
                f"{r['dest']} -> {r['next_hop']} metric={r['metric']}" for r in rotalar))
        else:
            self._yaz_oyun("Güncel rota okunamadı (ya da liste boş).")

    def _guncel_logla(self, guncel: dict | None) -> None:
        """Yönetici sonucundaki `guncel` alanını iki biçime dayanıklı loglar.

        Eski biçim: {"Ethernet 6": 25, "Wi-Fi": 50}
        Yeni biçim (ikili işlem): {"metrikler": {...}, "rotalar": [{"dest","next_hop","metric",...}]}
        """
        if not isinstance(guncel, dict) or not guncel:
            return
        metrikler = guncel.get("metrikler")
        rotalar = guncel.get("rotalar")
        if isinstance(metrikler, dict) and isinstance(rotalar, list):
            self._yaz_oyun("Güncel metrikler: " + (
                ", ".join(f"{ad}={deger}" for ad, deger in metrikler.items())
                if metrikler else "-"))
            if rotalar:
                parcalar = []
                for r in rotalar:
                    if isinstance(r, dict):
                        parcalar.append(
                            f"{r.get('dest', '?')} -> {r.get('next_hop', '?')} metric={r.get('metric', '?')}"
                        )
                    else:
                        parcalar.append(str(r))
                self._yaz_oyun("Güncel rotalar: " + " · ".join(parcalar))
            else:
                self._yaz_oyun("Güncel rotalar: -")
        else:
            self._yaz_oyun("Güncel metrikler: " + ", ".join(
                f"{ad}={deger}" for ad, deger in guncel.items()))

    # =====================================================================
    # Oyun modu: yonetici islemleri
    # =====================================================================
    def _yonetici_calistir(self, argumanlar: list[str], aciklama: str) -> None:
        if SONUC_YOLU.exists():
            try:
                SONUC_YOLU.unlink()
            except OSError:
                pass

        yardimci = KLASOR / "yonetici_islem.py"
        params = f'"{yardimci}" ' + " ".join(argumanlar) + f' --sonuc "{SONUC_YOLU}"'
        try:
            kod = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", _python_yolu(), params, str(KLASOR), 0
            )
        except Exception as hata:  # noqa: BLE001
            self._yaz_oyun(f"{aciklama}: yönetici başlatılamadı ({hata})")
            return

        if kod <= 32:
            self._yaz_oyun(f"{aciklama}: yönetici izni alınamadı (UAC iptal edilmiş olabilir).")
            return

        self._yaz_oyun(f"{aciklama}: yönetici izni istendi — UAC penceresini onayla.")

        def bekle() -> None:
            bitis = time.monotonic() + 120
            while time.monotonic() < bitis:
                if SONUC_YOLU.exists():
                    try:
                        veri = json.loads(SONUC_YOLU.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        time.sleep(0.2)
                        continue
                    self.kuyruk.put(("yonetici_bitti", aciklama, veri))
                    return
                time.sleep(0.25)
            self.kuyruk.put(("yonetici_bitti", aciklama, {"basarili": False, "mesaj": "zaman aşımı"}))

        threading.Thread(target=bekle, daemon=True).start()

    def _en_iyi_uygula(self) -> None:
        if not self.ping_sonuclari:
            messagebox.showinfo("Önce ölçüm yap", "Önce 'Ping testi' ile hatları ölç.")
            return

        hedef = self.oyun_hedef_var.get().strip() or "8.8.8.8"
        plan = cekirdek.ikili_plan(self.adaptorler, self.ping_sonuclari, hedef)
        if plan is None:
            messagebox.showerror("Hata", "Hiçbir hat hedefe ulaşamadı.")
            return
        if plan.get("hata"):
            messagebox.showwarning("Uyarı", f"Hedef çözümlenemedi: {plan['hata']}")
            self._yaz_oyun(f"UYARI: hedef çözümlenemedi ({plan['hata']})")
            return

        SPEC_YOLU.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        self._yaz_oyun(
            f"İkili yönlendirme planı hazır: en iyi hat {plan['en_iyi_hat']['ad']} "
            f"(skor {plan['en_iyi_skor']:.0f}), hedefler: {', '.join(plan['hedefler'])}"
        )
        self._yonetici_calistir(
            ["--islem", "ikili", "--spec", f'"{SPEC_YOLU}"', "--yedek", f'"{YEDEK_YOLU}"'],
            "İkili yönlendirme",
        )

        if plan["tek_hat_modu"]:
            self._yaz_oyun("İkinci hat çalışmıyor; tek hat kullanıldı.")
        else:
            self._yaz_oyun("Oyun hedefleri en iyi hatta sabitlendi; varsayılan trafik ikinci hattan gidecek.")

    def _metrik_geri(self) -> None:
        if not YEDEK_YOLU.exists():
            messagebox.showinfo("Kayıt yok", "Geri alınacak metrik kaydı bulunamadı.")
            return
        self._yonetici_calistir(
            ["--islem", "metrik-geri", "--yedek", f'"{YEDEK_YOLU}"'],
            "Metrikleri geri al",
        )

    def _wifi_guc(self) -> None:
        self._yonetici_calistir(["--islem", "wifi-guc"], "Wi-Fi güç tasarrufu")

    # =====================================================================
    # Oyun modu: canli ping
    # =====================================================================
    def _canli_adaptor(self) -> cekirdek.Adaptor | None:
        en_iyi = self._en_iyi_hat()
        if en_iyi is not None:
            return en_iyi
        return self.adaptorler[0] if self.adaptorler else None

    def _canli_degistir(self) -> None:
        if self._canli_calisiyor:
            self._canli_calisiyor = False
            self.btn_canli.configure(text="Canlı izlemeyi başlat")
            self.canli_ozet_var.set("Canlı izleme durduruldu.")
            self._yaz_oyun("Canlı izleme durduruldu.")
            return

        if not self.adaptorler:
            self._adaptorleri_yukle()
        adaptor = self._canli_adaptor()
        if adaptor is None:
            messagebox.showerror("Hata", "İzlenecek hat bulunamadı.")
            return

        hedef = self.oyun_hedef_var.get().strip() or "8.8.8.8"
        self._canli_calisiyor = True
        self._canli_veriler = []
        self._canli_toplam = 0
        self._canli_kayip = 0
        self.btn_canli.configure(text="İzlemeyi durdur")
        self._yaz_oyun(f"Canlı izleme: {adaptor.ad} ({adaptor.ip}) → {hedef}")

        def isci() -> None:
            while self._canli_calisiyor:
                basla = time.monotonic()
                sonuc = cekirdek.ping_olc(adaptor.ip, hedef, adet=1, bekleme_ms=900)
                if self._canli_calisiyor:
                    self.kuyruk.put(("canli_ping", sonuc))
                kalan = 1.0 - (time.monotonic() - basla)
                if kalan > 0:
                    time.sleep(kalan)

        threading.Thread(target=isci, daemon=True).start()

    def _canli_ping_isle(self, sonuc: dict) -> None:
        deger: float | None = None if sonuc.get("hata") else sonuc["ort"]
        self._canli_veriler.append(deger)
        if len(self._canli_veriler) > GRAFIK_NOKTA:
            self._canli_veriler.pop(0)
        self._canli_toplam += 1
        if deger is None:
            self._canli_kayip += 1

        gecerli = [v for v in self._canli_veriler if v is not None]
        if gecerli:
            ort = sum(gecerli) / len(gecerli)
            self.canli_ozet_var.set(
                f"anlık {deger:.0f} ms" if deger is not None else "anlık: yanıt yok"
            )
            self.canli_ozet_var.set(
                (f"anlık {deger:.0f} ms · " if deger is not None else "anlık kayıp · ")
                + f"ort {ort:.0f} ms · maks {max(gecerli):.0f} ms · kayıp %{self._canli_kayip * 100 / self._canli_toplam:.0f}"
            )
        self._grafik_ciz()

    def _grafik_ciz(self) -> None:
        tuval = self.grafik
        tuval.delete("all")
        genislik = max(tuval.winfo_width(), 120)
        yukseklik = max(tuval.winfo_height(), 60)
        veriler = self._canli_veriler
        if not veriler:
            tuval.create_text(genislik // 2, yukseklik // 2, fill=RENK["soluk"],
                              text="Canlı izleme bekleniyor…", font=(YAZI, 9))
            return

        gecerli = [v for v in veriler if v is not None]
        tepe = max(max(gecerli) if gecerli else 50, 40) * 1.25
        for i in range(1, 4):
            y = yukseklik * i / 4
            tuval.create_line(0, y, genislik, y, fill=RENK["cizgi"])
        tuval.create_text(6, 6, anchor="nw", fill=RENK["soluk"],
                          text=f"0–{tepe:.0f} ms", font=(YAZI, 8))

        adim = genislik / max(len(veriler) - 1, 1)
        noktalar: list[float] = []
        for indeks, deger in enumerate(veriler):
            if deger is None:
                continue
            x = indeks * adim
            y = yukseklik - min(deger / tepe, 1.0) * (yukseklik - 8) - 4
            noktalar.extend([x, y])
        if len(noktalar) >= 4:
            tuval.create_line(*noktalar, fill=RENK["yesil"], width=2, smooth=True)
        if len(noktalar) >= 2:
            tuval.create_oval(noktalar[-2] - 3, noktalar[-1] - 3, noktalar[-2] + 3, noktalar[-1] + 3,
                              fill=RENK["yesil"], outline="")

    # =====================================================================
    # Kuyruk
    # =====================================================================
    def _kuyruk_isle(self) -> None:
        try:
            while True:
                tur, *veri = self.kuyruk.get_nowait()

                if tur == "hiz_bitti":
                    self._hiz_olcuyor = False
                    self.ilerleme.stop()
                    self.ilerleme.configure(mode="determinate", value=0)
                    self.ilerleme.pack_forget()
                    self.btn_hiz.configure(state="normal", text="Hızları ölç")
                    self._yaz("Hız ölçümü bitti → " + " | ".join(f"{a.ad}: {a.hiz:.1f} Mbps" for a in self.adaptorler))
                    for adaptor in self.adaptorler:
                        if self.tablo.exists(adaptor.ip):
                            mevcut = self.tablo.item(adaptor.ip, "values")
                            self.tablo.item(adaptor.ip, values=(
                                mevcut[0], mevcut[1], mevcut[2], f"{adaptor.hiz:.1f} Mbps",
                                mevcut[4], mevcut[5], mevcut[6],
                            ))
                elif tur == "hiz_hata":
                    self._hiz_olcuyor = False
                    self.ilerleme.stop()
                    self.ilerleme.configure(mode="determinate", value=0)
                    self.ilerleme.pack_forget()
                    self.btn_hiz.configure(state="normal", text="Hızları ölç")
                    self._yaz(f"HATA (hız ölçümü): {veri[0]}")
                elif tur == "ping_bitti":
                    self._ping_sonuc_isle(veri[0])
                elif tur == "ping_hata":
                    self._ping_calisiyor = False
                    self.btn_ping.configure(state="normal", text="Ping testi")
                    self.oyun_ozet_var.set(f"Hata: {veri[0]}")
                    self._yaz_oyun(f"HATA (ping): {veri[0]}")
                elif tur == "canli_ping":
                    self._canli_ping_isle(veri[0])
                elif tur == "yonetici_bitti":
                    aciklama, sonuc = veri[0], veri[1]
                    basarili = bool(sonuc.get("basarili"))
                    self._yaz_oyun(f"{aciklama}: {'tamam' if basarili else 'başarısız'} — {sonuc.get('mesaj', '')}")
                    guncel = sonuc.get("guncel")
                    self._guncel_logla(guncel)
                    if basarili and aciklama == "Metrikleri geri al" and YEDEK_YOLU.exists():
                        try:
                            YEDEK_YOLU.unlink()
                        except OSError:
                            pass
                elif tur == "canli_hata":
                    self._yaz_oyun(f"HATA (canlı izleme): {veri[0]}")
        except queue.Empty:
            pass
        self.kok.after(120, self._kuyruk_isle)

    # =====================================================================
    # Kapatma
    # =====================================================================
    def _kapat(self) -> None:
        try:
            self._canli_calisiyor = False
            self.yonetici.durdur()
            self._sistem_proxy_temizle()
        finally:
            self.kok.destroy()


# ---------------------------------------------------------------------------
# Ekran goruntusu / giris noktasi
# ---------------------------------------------------------------------------
def ekran_goruntusu(yol: str, canli: bool = False, sekme: str = "birlestirme") -> int:
    _dpi_ayarla()
    kok = tk.Tk()
    uygulama = Uygulama(kok)
    kok.geometry("1060x780+20+20")
    kok.attributes("-topmost", True)
    kok.lift()
    kok.update_idletasks()
    kok.update()
    time.sleep(0.6)
    kok.update()

    if sekme == "oyun":
        uygulama.defter.select(1)
        if canli:
            uygulama._ping_test()
            bitis = time.monotonic() + 45
            while uygulama._ping_calisiyor and time.monotonic() < bitis:
                kok.update()
                time.sleep(0.05)
            uygulama._canli_degistir()
            bitis = time.monotonic() + 3.0
            while time.monotonic() < bitis:
                kok.update()
                time.sleep(0.05)
    elif canli:
        port = 8897
        uygulama._hiz_olc()
        sinir = time.monotonic() + 12
        while uygulama._hiz_olcuyor and time.monotonic() < sinir:
            kok.update()
            time.sleep(0.05)
        uygulama.port_var.set(str(port))
        uygulama._baslat()

        def trafik() -> None:
            import subprocess

            def tek() -> None:
                subprocess.run(
                    ["curl.exe", "-s", "-o", "NUL", "-x", f"http://127.0.0.1:{port}",
                     "--max-time", "20", "https://speed.cloudflare.com/__down?bytes=4000000"],
                    capture_output=True, timeout=30,
                )
            for _ in range(4):
                isler = [threading.Thread(target=tek, daemon=True) for _ in range(3)]
                for is_ in isler:
                    is_.start()
                for is_ in isler:
                    is_.join(timeout=25)

        threading.Thread(target=trafik, daemon=True).start()
        bitis = time.monotonic() + 6.5
        while time.monotonic() < bitis:
            kok.update()
            time.sleep(0.05)

    try:
        from PIL import ImageGrab
    except ImportError:
        print("PIL yok: pip install pillow")
        kok.destroy()
        return 1

    x, y = kok.winfo_rootx(), kok.winfo_rooty()
    goruntu = ImageGrab.grab(bbox=(x, y, x + kok.winfo_width(), y + kok.winfo_height()))
    goruntu.save(yol)
    print(f"ekran goruntusu kaydedildi: {yol} ({goruntu.size[0]}x{goruntu.size[1]})")
    uygulama._canli_calisiyor = False
    if canli:
        uygulama._durdur()
    kok.destroy()
    return 0


def main() -> int:
    if "--ekran-goruntusu" in sys.argv:
        indeks = sys.argv.index("--ekran-goruntusu")
        yol = sys.argv[indeks + 1] if len(sys.argv) > indeks + 1 else "ekran.png"
        sekme = "oyun" if "oyun" in sys.argv else "birlestirme"
        return ekran_goruntusu(yol, canli="--canli" in sys.argv, sekme=sekme)

    _dpi_ayarla()
    kok = tk.Tk()
    uygulama = Uygulama(kok)
    kok.protocol("WM_DELETE_WINDOW", uygulama._kapat)
    kok.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
