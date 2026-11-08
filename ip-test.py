import http.client, ssl, socket, time
ips = {"Wi-Fi": "192.168.1.45", "Samsung/USB": "10.145.240.31"}
for ad, ip in ips.items():
    t0 = time.time()
    try:
        c = http.client.HTTPSConnection("api.ipify.org", timeout=12,
                                        context=ssl.create_default_context(),
                                        source_address=(ip, 0))
        c.request("GET", "/")
        cevap = c.getresponse().read().decode().strip()
        print(f"{ad:12s} {ip:15s} -> cikis IP {cevap}  ({time.time()-t0:.2f}s)")
        c.close()
    except Exception as e:
        print(f"{ad:12s} {ip:15s} -> HATA {type(e).__name__}: {e}")
