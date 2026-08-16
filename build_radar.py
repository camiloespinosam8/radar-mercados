# -*- coding: utf-8 -*-
"""
build_radar.py — motor del Radar de Mercados.

Baja las series de Yahoo Finance, calcula las metricas, arma el termometro,
guarda el historico diario, detecta los cruces de umbral y avisa por Telegram
solo cuando hay algo accionable.

Uso:
    python build_radar.py                 # ciclo completo
    python build_radar.py --no-telegram   # no avisa (para pruebas)
    python build_radar.py --forzar-aviso  # manda el resumen aunque no haya alertas

Salidas (todas en la misma carpeta):
    datos.json      snapshot que consume index.html
    historico.json  serie diaria, append-only
    radar.log       bitacora de corridas
"""
from __future__ import annotations
import json, math, os, sys, time, argparse, datetime as dt
import urllib.request, urllib.parse, urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(BASE, "datos.json")
HIST = os.path.join(BASE, "historico.json")
LOG = os.path.join(BASE, "radar.log")
SECRETS = r"C:\Users\Cami\Desktop\CLAUDE\Sistema\.secrets\telegram_mayordomo.txt"
# El destino del aviso vive en config_local.json (fuera del repo): un chat_id es
# dato personal y este repositorio es publico.
CONFIG_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_local.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

# ─────────────────────────── universo ───────────────────────────
# sym: (nombre, grupo)
UNIVERSO = {
    # indices y nucleo
    "SPY": ("S&P 500", "IDX"), "QQQ": ("Nasdaq 100", "IDX"),
    "IWM": ("Russell 2000 · small caps", "IDX"), "RSP": ("S&P 500 equiponderado", "IDX"),
    "DIA": ("Dow Jones 30", "IDX"), "VT": ("Acciones mundo entero", "IDX"),
    "^GSPC": ("Índice S&P 500", "IDX"),
    # regiones
    "EFA": ("Desarrollados ex-EEUU", "REG"), "VWO": ("Emergentes", "REG"),
    "EWZ": ("Brasil", "REG"), "ECH": ("Chile", "REG"), "EWW": ("México", "REG"),
    "EZU": ("Zona Euro", "REG"), "EWJ": ("Japón", "REG"), "MCHI": ("China", "REG"),
    "INDA": ("India", "REG"),
    # sectores EEUU
    "XLK": ("Tecnología", "SEC"), "XLC": ("Comunicaciones", "SEC"),
    "XLY": ("Consumo discrecional", "SEC"), "XLI": ("Industriales", "SEC"),
    "XLF": ("Financiero", "SEC"), "XLE": ("Energía", "SEC"), "XLB": ("Materiales", "SEC"),
    "XLV": ("Salud", "SEC"), "XLP": ("Consumo básico", "SEC"), "XLU": ("Utilities", "SEC"),
    "XLRE": ("Inmobiliario", "SEC"),
    # tematicos
    "SMH": ("Semiconductores", "TEM"), "IGV": ("Software", "TEM"),
    "ITA": ("Defensa y aeroespacial", "TEM"), "URA": ("Uranio", "TEM"),
    "XBI": ("Biotecnología", "TEM"), "ARKK": ("Innovación disruptiva", "TEM"),
    # renta fija
    "TLT": ("Bonos EEUU 20+ años", "RF"), "IEF": ("Bonos EEUU 7-10 años", "RF"),
    "LQD": ("Deuda grado inversión", "RF"), "HYG": ("Deuda alto rendimiento", "RF"),
    # materias primas
    "GLD": ("Oro", "MAT"), "SLV": ("Plata", "MAT"), "GDX": ("Mineras de oro", "MAT"),
    "DBC": ("Canasta de materias primas", "MAT"),
    # macro
    "^VIX": ("Índice del miedo", "MAC"), "^TNX": ("Tasa EEUU 10 años", "MAC"),
    "DX-Y.NYB": ("Índice dólar", "MAC"), "CLP=X": ("Dólar / peso chileno", "MAC"),
    "BTC-USD": ("Bitcoin", "MAC"),
    # Chile — bolsa local en pesos
    "^IPSA": ("IPSA · Bolsa de Santiago", "CL"),
    "PARAUCO.SN": ("Parque Arauco", "CL"), "MALLPLAZA.SN": ("Mallplaza", "CL"),
    "ECL.SN": ("Engie Energía", "CL"), "AGUAS-A.SN": ("Aguas Andinas", "CL"),
    "CHILE.SN": ("Banco de Chile", "CL"), "BSANTANDER.SN": ("Santander Chile", "CL"),
    "ITAUCL.SN": ("Itaú Chile", "CL"), "BCI.SN": ("BCI", "CL"),
    "SQM-B.SN": ("SQM-B", "CL"), "COPEC.SN": ("Copec", "CL"),
    "CMPC.SN": ("CMPC", "CL"), "FALABELLA.SN": ("Falabella", "CL"),
    "SMU.SN": ("SMU", "CL"), "RIPLEY.SN": ("Ripley", "CL"),
    "ILC.SN": ("ILC", "CL"), "COLBUN.SN": ("Colbún", "CL"),
    "LTM.SN": ("Latam Airlines", "CL"), "VAPORES.SN": ("Vapores", "CL"),
    "ENELAM.SN": ("Enel Américas", "CL"),
}
# los 12 con recomendacion de sobreponderar de LarrainVial Research
LV_SOBREPONDERAR = ["ECL.SN", "AGUAS-A.SN", "COPEC.SN", "SQM-B.SN", "CHILE.SN",
                    "BSANTANDER.SN", "ITAUCL.SN", "RIPLEY.SN", "SMU.SN",
                    "MALLPLAZA.SN", "PARAUCO.SN"]

def log(msg: str) -> None:
    linea = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(linea)
    except UnicodeEncodeError:      # consola de Windows en cp1252
        print(linea.encode("ascii", "replace").decode("ascii"))
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass

# ─────────────────────────── descarga ───────────────────────────
def bajar(sym: str, reintentos: int = 3):
    # period1/period2 explicitos: con ?range= Yahoo devuelve series truncadas
    # a unas pocas sesiones para varios indices (^TNX venia con 17 en vez de 500).
    p2 = int(time.time()); p1 = p2 - 63072000  # 2 años
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?period1={p1}&period2={p2}&interval=1d")
    for i in range(reintentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read().decode("utf-8"))
            res = (j.get("chart") or {}).get("result") or []
            if not res:
                return None
            q = res[0]
            ts = q.get("timestamp") or []
            ind = q.get("indicators") or {}
            cierres = None
            adj = ind.get("adjclose") or []
            if adj and adj[0].get("adjclose"):
                cierres = adj[0]["adjclose"]
            elif ind.get("quote") and ind["quote"][0].get("close"):
                cierres = ind["quote"][0]["close"]
            if not cierres:
                return None
            pares = [(t, c) for t, c in zip(ts, cierres) if c is not None]
            if not pares:
                return None
            # Yahoo deja de actualizar las barras diarias de algunos mercados
            # (la Bolsa de Santiago viene con ~21 sesiones congeladas: repite el
            # mismo cierre) aunque regularMarketPrice si esta vivo. Si no se
            # recorta esa cola, el retorno del dia sale comparando el precio real
            # contra un cierre de hace un mes y aparecen saltos falsos de +13%.
            congeladas = 1
            for i in range(len(pares) - 1, 0, -1):
                if pares[i][1] == pares[i - 1][1]:
                    congeladas += 1
                else:
                    break
            rezago = congeladas - 1 if congeladas >= 3 else 0
            if rezago:
                pares = pares[:len(pares) - rezago]
            # Yahoo tampoco publica historia para algunos indices (^IPSA solo da
            # el nivel actual). Se aceptan igual, marcados como spot.
            return {"meta": q.get("meta", {}),
                    "ts": [p[0] for p in pares],
                    "px": [p[1] for p in pares],
                    "rezago": rezago,
                    "spot": len(pares) < 30}
        except Exception as e:
            if i == reintentos - 1:
                log(f"  ! {sym}: {type(e).__name__}")
            time.sleep(1.2 * (i + 1))
    return None

def metricas(sym: str, d: dict) -> dict | None:
    P, T = d["px"], d["ts"]
    n = len(P)
    ult = d["meta"].get("regularMarketPrice") or P[-1]
    nom_, grupo_ = UNIVERSO.get(sym, (sym, "MAC"))
    if d.get("spot"):
        # solo nivel: sin historia no hay medias, momentum ni rango
        return {"sym": sym, "nombre": nom_, "grupo": grupo_, "solo_spot": True,
                "px": round(ult, 2), "d1": None, "w1": None, "m1": None, "m3": None,
                "m6": None, "y1": None, "ytd": None, "ma50": None, "ma200": None,
                "v50": None, "v200": None, "cross": None, "hi52": None, "lo52": None,
                "fromHi": None, "rng": None, "vol": None,
                "fecha": d["meta"].get("regularMarketTime"),
                "moneda": d["meta"].get("currency")}
    r2 = lambda x: None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, 2)
    atras = lambda k: (ult / P[n - 1 - k] - 1) * 100 if n - 1 - k >= 0 else None
    def ma(k):
        return sum(P[n - k:]) / k if n >= k else None
    # YTD: ultimo cierre del año anterior
    anio = dt.datetime.utcfromtimestamp(T[-1]).year
    base = None
    for t, p in zip(T, P):
        if dt.datetime.utcfromtimestamp(t).year < anio:
            base = p
    m50, m200 = ma(50), ma(200)
    v = P[-252:] if n >= 252 else P
    hi, lo = max(max(v), ult), min(min(v), ult)
    rets = [math.log(P[i] / P[i - 1]) for i in range(max(1, n - 20), n) if P[i - 1] > 0]
    vol = None
    if len(rets) > 2:
        mu = sum(rets) / len(rets)
        vol = math.sqrt(sum((x - mu) ** 2 for x in rets) / (len(rets) - 1)) * math.sqrt(252) * 100
    nom, grupo = UNIVERSO.get(sym, (sym, "MAC"))
    rez = d.get("rezago", 0)
    if rez >= 3:
        # con la serie recortada, el precio vivo no es comparable contra el cierre
        # anterior: el "dia" y la "semana" no se pueden calcular sin mentir.
        atras_ = atras
        atras = lambda k: atras_(k) if k > rez else None
    return {
        "sym": sym, "nombre": nom, "grupo": grupo,
        "rezago": rez,
        "px": r2(ult), "d1": r2(atras(1)), "w1": r2(atras(5)), "m1": r2(atras(21)),
        "m3": r2(atras(63)), "m6": r2(atras(126)), "y1": r2(atras(251)),
        "ytd": r2((ult / base - 1) * 100) if base else None,
        "ma50": r2(m50), "ma200": r2(m200),
        "v50": r2((ult / m50 - 1) * 100) if m50 else None,
        "v200": r2((ult / m200 - 1) * 100) if m200 else None,
        "cross": ("golden" if m50 > m200 else "death") if (m50 and m200) else None,
        "hi52": r2(hi), "lo52": r2(lo), "fromHi": r2((ult / hi - 1) * 100),
        "rng": r2((ult - lo) / (hi - lo) * 100) if hi > lo else 50.0,
        "vol": r2(vol),
        "fecha": d["meta"].get("regularMarketTime"),
        "moneda": d["meta"].get("currency"),
    }

# ─────────────────────────── termometro ───────────────────────────
def termometro(M: dict) -> dict:
    g = M.get
    spy, rsp, vix = g("^GSPC") or g("SPY"), g("RSP"), g("^VIX")
    hyg, lqd, tnx = g("HYG"), g("LQD"), g("^TNX")
    xly, xlp, xle = g("XLY"), g("XLP"), g("XLE")
    if not all([spy, rsp, vix, hyg, lqd, tnx, xly, xlp, xle]):
        return {"score": None, "pilares": [], "nota": "faltan datos para el termómetro"}
    cl = lambda x: max(0.0, min(100.0, x))
    P = []
    P.append(("Tendencia", "S&P vs media 200", spy["v200"], cl(50 + spy["v200"] * 4.5)))
    amp = rsp["ytd"] - spy["ytd"]
    P.append(("Amplitud", "Equiponderado vs índice", amp, cl(50 + amp * 7)))
    P.append(("Volatilidad", f"VIX {vix['px']:.2f}", vix["px"], cl(100 - (vix["px"] - 10) * 4)))
    cr = hyg["m3"] - lqd["m3"]
    P.append(("Crédito", "Alto rendimiento vs grado inv.", cr, cl(50 + cr * 13)))
    P.append(("Tasas largas", "Bono EEUU 10 años", tnx["px"], cl(100 - tnx["rng"])))
    cd = (xly["m3"] + xle["m3"]) / 2 - xlp["m3"]
    P.append(("Apetito", "Cíclicos vs defensivos", cd, cl(50 + cd * 5)))
    score = round(sum(p[3] for p in P) / len(P))
    etq = ("RIESGO ENCENDIDO" if score >= 72 else
           "RIESGO ENCENDIDO · con reparos" if score >= 58 else
           "NEUTRAL" if score >= 42 else
           "A LA DEFENSIVA" if score >= 28 else "RIESGO APAGADO")
    return {"score": score, "etiqueta": etq,
            "pilares": [{"t": a, "sub": b, "val": round(c, 2), "score": round(d)} for a, b, c, d in P]}

# ─────────────────────────── ranking ───────────────────────────
def puntaje(r: dict) -> float:
    if r["v200"] is None or r["v50"] is None or r["m1"] is None or r["vol"] is None:
        return -999.0
    s = 0.0
    s += min(r["v200"], 20) * 1.4
    s += max(-10, min(r["v50"], 8)) * 1.8
    s += min(r["m1"], 12) * 1.0
    s += 12 if r["cross"] == "golden" else -10
    s += ((r["rng"] or 50) - 50) * .16
    s -= max(0.0, r["v50"] - 5) ** 1.35 * 2.2
    s -= max(0.0, (r["ytd"] or 0) - 25) * .7
    s -= max(0.0, r["vol"] - 18) * .9
    s += max(0.0, 18 - r["vol"]) * .5
    return round(s, 1)

# ─────────────────────────── alertas ───────────────────────────
UMBRALES = {"mov_dia": 4.0, "mov_dia_accion": 7.0, "vix_salto": 25.0,
            "vix_nivel": 25.0, "term_piso": 50, "term_techo": 70}

def detectar_alertas(M: dict, term: dict, prev: dict | None) -> list[dict]:
    A = []
    p_term = (prev or {}).get("termometro")
    p_inst = (prev or {}).get("inst", {})
    sc = term.get("score")

    if p_term is not None and sc is not None:
        if p_term >= UMBRALES["term_piso"] > sc:
            A.append({"n": 3, "t": "El termómetro cayó bajo 50",
                      "d": f"pasó de {p_term} a {sc}. El mercado dejó de estar en modo riesgo encendido."})
        elif p_term < UMBRALES["term_techo"] <= sc:
            A.append({"n": 1, "t": "El termómetro superó 70",
                      "d": f"pasó de {p_term} a {sc}. Régimen de riesgo encendido pleno."})

    vix = M.get("^VIX")
    if vix:
        if vix["d1"] and vix["d1"] >= UMBRALES["vix_salto"]:
            A.append({"n": 3, "t": f"El VIX saltó {vix['d1']:+.1f}% en un día",
                      "d": f"quedó en {vix['px']:.2f}. Entró miedo al sistema."})
        elif vix["px"] >= UMBRALES["vix_nivel"] and (p_inst.get("^VIX", {}).get("px", 0) < UMBRALES["vix_nivel"]):
            A.append({"n": 2, "t": f"El VIX cruzó 25 ({vix['px']:.2f})", "d": "volatilidad en zona de estrés."})

    for sym, r in M.items():
        if r["grupo"] == "MAC" or r["v200"] is None:
            continue
        ant = p_inst.get(sym)
        nom = r["nombre"]
        if ant and ant.get("v200") is not None:
            if ant["v200"] >= 0 > r["v200"]:
                A.append({"n": 2, "t": f"{sym} perdió su media de 200",
                          "d": f"{nom} quebró la tendencia de largo plazo ({r['v200']:+.1f}%)."})
            elif ant["v200"] < 0 <= r["v200"]:
                A.append({"n": 1, "t": f"{sym} recuperó su media de 200",
                          "d": f"{nom} vuelve a tendencia alcista de largo plazo ({r['v200']:+.1f}%)."})
            if ant.get("cross") and r["cross"] and ant["cross"] != r["cross"]:
                alcista = r["cross"] == "golden"
                A.append({"n": 1 if alcista else 2,
                          "t": f"{sym}: cruce {'alcista' if alcista else 'bajista'} confirmado",
                          "d": f"{nom} — la media de 50 cruzó {'sobre' if alcista else 'bajo'} la de 200."})
        # una accion suelta se mueve mucho mas que un ETF: el umbral no puede ser el mismo
        umbral = UMBRALES["mov_dia_accion"] if r["grupo"] == "CL" else UMBRALES["mov_dia"]
        if r["d1"] is not None and not r.get("rezago") and abs(r["d1"]) >= umbral:
            A.append({"n": 2, "t": f"{sym} se movió {r['d1']:+.1f}% en el día",
                      "d": f"{nom} — movimiento fuera de lo normal."})
    A.sort(key=lambda x: x["n"])
    return A[:12]

# ─────────────────────────── calendario ───────────────────────────
def calendario(hoy: dt.date) -> list[dict]:
    """Agenda de datos que mueven al mercado. Las fechas de bancos centrales son
    las oficialmente publicadas; el resto sigue la regla de publicación habitual."""
    ev = []
    FOMC = [("2026-09-16", "Decisión de tasa de la Fed (con dot plot)"),
            ("2026-10-28", "Decisión de tasa de la Fed"),
            ("2026-12-09", "Decisión de tasa de la Fed (con dot plot)")]
    BCCH = [("2026-09-08", "Reunión de Política Monetaria · Banco Central de Chile"),
            ("2026-10-27", "Reunión de Política Monetaria · Banco Central de Chile"),
            ("2026-12-15", "Reunión de Política Monetaria · Banco Central de Chile")]
    for f, t in FOMC:
        ev.append({"f": f, "t": t, "peso": 3, "reg": "EEUU", "toca": ["TLT", "IWM", "XLF"]})
    for f, t in BCCH:
        ev.append({"f": f, "t": t, "peso": 2, "reg": "Chile", "toca": ["^IPSA", "ECH", "CLP=X"]})

    def habil(d: dt.date) -> dt.date:
        """Los datos no se publican fin de semana: corre al lunes siguiente."""
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        return d

    # reglas mensuales: NFP = primer viernes; IPC ~dia 12; ventas minoristas ~dia 15
    for k in range(0, 4):
        m = hoy.month + k
        y = hoy.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        d = dt.date(y, m, 1)
        while d.weekday() != 4:
            d += dt.timedelta(days=1)
        ev.append({"f": d.isoformat(), "t": "Empleo EEUU (nóminas no agrícolas)", "peso": 3,
                   "reg": "EEUU", "toca": ["TLT", "SPY", "IWM"], "aprox": True})
        ev.append({"f": habil(dt.date(y, m, 12)).isoformat(), "t": "IPC de EEUU", "peso": 3,
                   "reg": "EEUU", "toca": ["TLT", "GLD", "QQQ"], "aprox": True})
        ev.append({"f": habil(dt.date(y, m, 15)).isoformat(), "t": "Ventas minoristas EEUU", "peso": 2,
                   "reg": "EEUU", "toca": ["XLY", "IWM"], "aprox": True})
        ev.append({"f": habil(dt.date(y, m, 8)).isoformat(), "t": "IPC de Chile", "peso": 2,
                   "reg": "Chile", "toca": ["^IPSA", "CLP=X"], "aprox": True})
    ev = [e for e in ev if dt.date.fromisoformat(e["f"]) >= hoy]
    ev.sort(key=lambda e: (e["f"], -e["peso"]))
    for e in ev:
        e["dias"] = (dt.date.fromisoformat(e["f"]) - hoy).days
    return ev[:14]

# ─────────────────────────── telegram ───────────────────────────
def token_telegram():
    try:
        with open(SECRETS, encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea.startswith("TELEGRAM_MAYORDOMO_BOT_TOKEN="):
                    return linea.split("=", 1)[1].strip()
    except Exception as e:
        log(f"  ! no pude leer credenciales: {type(e).__name__}")
    return None

def chat_destino():
    try:
        with open(CONFIG_LOCAL, encoding="utf-8") as f:
            return str(json.load(f).get("telegram_chat_id", "")).strip() or None
    except Exception:
        return None

def avisar(texto: str) -> bool:
    tok, chat = token_telegram(), chat_destino()
    if not tok or not chat:
        log("  ! falta token o config_local.json, no aviso")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": texto,
                                       "parse_mode": "Markdown",
                                       "disable_web_page_preview": "true"}).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        log(f"  ! telegram: {type(e).__name__}")
        return False

def armar_aviso(term, alertas, top, peor, cal) -> str:
    L = [f"📈 *Radar de Mercados* — termómetro *{term['score']}/100*",
         f"_{term['etiqueta']}_", ""]
    if alertas:
        L.append("*Lo que cambió:*")
        for a in alertas[:6]:
            ic = {1: "🟢", 2: "🟡", 3: "🔴"}.get(a["n"], "•")
            L.append(f"{ic} {a['t']} — {a['d']}")
        L.append("")
    if top:
        L.append(f"*Mejor puntuado:* {top['sym']} ({top['nombre']}) · {top['ytd']:+.1f}% en el año")
    if peor:
        L.append(f"*Del que me alejaría:* {peor['sym']} ({peor['nombre']}) · {peor['ytd']:+.1f}%")
    prox = [e for e in cal if e["dias"] <= 3 and e["peso"] >= 3]
    if prox:
        L.append("")
        L.append("*Ojo con la agenda:*")
        for e in prox[:3]:
            cuando = "hoy" if e["dias"] == 0 else ("mañana" if e["dias"] == 1 else f"en {e['dias']} días")
            L.append(f"📅 {e['t']} — {cuando}")
    L.append("")
    L.append("https://camiloespinosam8.github.io/radar-mercados/")
    L.append("_Lectura técnica, no asesoría de inversión._")
    return "\n".join(L)

# ─────────────────────────── main ───────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--forzar-aviso", action="store_true")
    args = ap.parse_args()

    log(f"=== corrida · {len(UNIVERSO)} instrumentos ===")
    M, fallidos = {}, []
    for i, sym in enumerate(UNIVERSO, 1):
        d = bajar(sym)
        if not d:
            fallidos.append(sym); continue
        r = metricas(sym, d)
        if r:
            M[sym] = r
        else:
            fallidos.append(sym)
        if i % 10 == 0:
            log(f"  {i}/{len(UNIVERSO)}…")
        time.sleep(0.22)
    log(f"  bajados {len(M)} · fallidos {len(fallidos)}{': ' + ','.join(fallidos) if fallidos else ''}")
    if len(M) < 25:
        log("  ! muy pocos datos, aborto sin escribir")
        return 1

    term = termometro(M)

    # fecha de mercado anclada a SPY (cripto y divisas operan fin de semana)
    ts = (M.get("SPY") or {}).get("fecha")
    fmkt = dt.datetime.utcfromtimestamp(ts).date().isoformat() if ts else dt.date.today().isoformat()

    hist = []
    if os.path.exists(HIST):
        try:
            hist = json.load(open(HIST, encoding="utf-8"))
        except Exception:
            hist = []
    prev = hist[-1] if hist and hist[-1].get("fecha") != fmkt else (hist[-2] if len(hist) > 1 else None)

    alertas = detectar_alertas(M, term, prev)

    rk = sorted([r for r in M.values() if r["grupo"] not in ("MAC", "CL")],
                key=puntaje, reverse=True)
    rk = [r for r in rk if puntaje(r) > -900]
    for r in M.values():
        r["puntaje"] = puntaje(r)

    entrada = {"fecha": fmkt, "ts": int(time.time()), "termometro": term["score"],
               "pilares": {p["t"]: p["score"] for p in term["pilares"]},
               "inst": {s: {"px": r["px"], "v200": r["v200"], "v50": r["v50"],
                            "cross": r["cross"], "ytd": r["ytd"]} for s, r in M.items()}}
    hist = [h for h in hist if h.get("fecha") != fmkt] + [entrada]
    hist.sort(key=lambda h: h["fecha"])
    hist = hist[-400:]
    json.dump(hist, open(HIST, "w", encoding="utf-8"), ensure_ascii=False)

    cal = calendario(dt.date.today())
    serie = [{"f": h["fecha"], "s": h["termometro"]} for h in hist if h.get("termometro") is not None]

    datos = {
        "generado": dt.datetime.now().isoformat(timespec="seconds"),
        "fecha_mercado": fmkt,
        "termometro": term,
        "serie_termometro": serie[-120:],
        "instrumentos": list(M.values()),
        "ranking": [r["sym"] for r in rk],
        "alertas": alertas,
        "calendario": cal,
        "lv_sobreponderar": [s for s in LV_SOBREPONDERAR if s in M],
        "fallidos": fallidos,
    }
    json.dump(datos, open(DATOS, "w", encoding="utf-8"), ensure_ascii=False)
    log(f"  datos.json escrito · termómetro {term['score']} · {len(alertas)} alertas · histórico {len(hist)} días")

    # silencio por defecto: solo avisa si hay algo accionable
    if not args.no_telegram and (alertas or args.forzar_aviso):
        ok = avisar(armar_aviso(term, alertas, rk[0] if rk else None,
                                rk[-1] if rk else None, cal))
        log(f"  telegram: {'enviado' if ok else 'falló'}")
    elif not alertas:
        log("  sin alertas — no aviso (silencio por defecto)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
