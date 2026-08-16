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
    "IEI": ("Bonos EEUU 3-7 años", "RF"),   # duracion ~4,4: contrapartida de HYG
    "LQD": ("Deuda grado inversión", "RF"), "HYG": ("Deuda alto rendimiento", "RF"),
    "SPHB": ("S&P alta beta", "TEM"), "SPLV": ("S&P baja volatilidad", "TEM"),
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
            for k in range(len(pares) - 1, 0, -1):
                if pares[k][1] == pares[k - 1][1]:
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

def bajar_vix3m():
    """VIX3M desde el CDN de CBOE. Yahoo tiene ^VIX3M congelado, y el nivel del
    VIX solo no distingue regimen: la estructura de plazos si."""
    try:
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            filas = [l for l in r.read().decode("utf-8", "replace").strip().splitlines() if l]
        for linea in reversed(filas[1:]):
            partes = linea.split(",")
            if len(partes) >= 5:
                try:
                    return float(partes[4])
                except ValueError:
                    continue
    except Exception as e:
        log(f"  ! VIX3M: {type(e).__name__}")
    return None

# ─────────────────────────── termometro ───────────────────────────
def termometro(M: dict, vix3m: float | None = None) -> dict:
    g = M.get
    spy, vix, tnx = g("^GSPC") or g("SPY"), g("^VIX"), g("^TNX")
    hyg, iei, lqd = g("HYG"), g("IEI"), g("LQD")
    xly, xlp = g("XLY"), g("XLP")
    if not all([spy, vix, tnx, hyg, xly, xlp]):
        return {"score": None, "pilares": [], "nota": "faltan datos para el termómetro"}
    cl = lambda x: max(0.0, min(100.0, x))
    P = []

    # 1 TENDENCIA — precio vs media de 200 (Faber 2007). El pilar mejor fundado.
    P.append(("Tendencia", "S&P vs media 200", spy["v200"], cl(50 + spy["v200"] * 4.5)))

    # 2 AMPLITUD — participacion real: % de ETFs de acciones EEUU sobre su propia
    # media de 200. Reemplaza a "RSP menos SPY en el año", que estaba INVERTIDO:
    # daba 98,7/100 en 2022 (año bajista, SPY -18,6%) y 0/100 en 2023 y 2024
    # (dos años de +25%). El defecto es estructural: cuando las mega-caps lideran
    # la caida, el equiponderado gana, y el pilar lo leia como salud.
    univ = [r for r in M.values()
            if r["grupo"] in ("SEC", "IDX", "TEM") and r.get("v200") is not None]
    if len(univ) >= 10:
        pct = sum(1 for r in univ if r["v200"] > 0) / len(univ) * 100
        P.append(("Amplitud", f"{sum(1 for r in univ if r['v200']>0)} de {len(univ)} sobre su media 200",
                  round(pct, 1), cl(pct)))

    # 3 VOLATILIDAD — estructura de plazos VIX/VIX3M por sobre el nivel suelto.
    # Bajo 1 el mercado paga menos por proteccion a 30 dias que a 90 (calma);
    # sobre 1 hay miedo con fecha. Se autoajusta al regimen de volatilidad.
    if vix3m and vix3m > 0:
        ratio = vix["px"] / vix3m
        P.append(("Volatilidad", f"VIX/VIX3M {ratio:.2f}", round(ratio, 3),
                  cl(50 + (1.0 - ratio) * 200)))
    else:
        P.append(("Volatilidad", f"VIX {vix['px']:.2f}", vix["px"], cl(100 - (vix["px"] - 10) * 4)))

    # 4 CREDITO — HYG contra Treasuries de DURACION PARECIDA (IEI, ~4,4 años).
    # Antes se comparaba contra LQD, que dura ~8,0 contra los ~3,0 de HYG: esa
    # brecha de 5 años hacia que el pilar midiera movimientos de TASA disfrazados
    # de estres crediticio. Una baja de 50pb de tasas movia el pilar 32 puntos
    # sin que se moviera un solo punto base de spread.
    ref = iei or lqd
    cr = hyg["m3"] - ref["m3"]
    P.append(("Crédito", f"Alto rendimiento vs Treasury {'3-7a' if iei else '(referencia larga)'}",
              round(cr, 2), cl(50 + cr * 20)))

    # 5 TASAS LARGAS
    P.append(("Tasas largas", "Bono EEUU 10 años", tnx["px"], cl(100 - tnx["rng"])))

    # 6 APETITO — canasta de pares ofensivo/defensivo. Se saco XLE: energia es un
    # trade de inflacion, no del ciclo, y dejaba el pilar clavado en 100/100 en
    # 2021, 2022 y 2023 — incluido 2022, cuando el S&P cayo 18,6% con XLE +59%.
    pares, det = [], []
    for ofensivo, defensivo, etq in (("SPHB", "SPLV", "alta beta/baja vol"),
                                     ("XLY", "XLP", "discrecional/básico"),
                                     ("IWM", "SPY", "small/large"),
                                     ("SMH", "SPY", "semis/mercado")):
        a, b = g(ofensivo), g(defensivo)
        if a and b and a.get("m3") is not None and b.get("m3") is not None:
            pares.append(a["m3"] - b["m3"]); det.append(etq)
    if pares:
        cd = sum(pares) / len(pares)
        P.append(("Apetito", f"{len(pares)} pares ofensivo/defensivo", round(cd, 2), cl(50 + cd * 5)))

    score = round(sum(p[3] for p in P) / len(P))
    etq = ("RIESGO ENCENDIDO" if score >= 72 else
           "RIESGO ENCENDIDO · con reparos" if score >= 58 else
           "NEUTRAL" if score >= 42 else
           "A LA DEFENSIVA" if score >= 28 else "RIESGO APAGADO")
    return {"score": score, "etiqueta": etq,
            "pilares": [{"t": a, "sub": b, "val": round(c, 2), "score": round(d)} for a, b, c, d in P]}

# ─────────────────────────── ranking ───────────────────────────
def puntaje(r: dict):
    """Momentum multi-horizonte 13612W (Keller & Keuning) con gate de tendencia
    absoluta. Reemplaza a la version anterior, que castigaba la distancia sobre
    la media de 50 y el retorno del año: backtest sobre este mismo universo dio
    9,41% anual contra 15,09% de esta, y por debajo de solo comprar SPY (14,89%).
    El castigo era ademas la señal de Avramov-Kaplanski-Subrahmanyam (RFE 2021)
    con el signo invertido, y se volvia dominante — a 20% sobre la media de 50
    restaba 85 puntos cuando el techo positivo del modelo entero era 74."""
    if any(r.get(k) is None for k in ("v200", "m1", "m3", "m6", "y1")):
        return None
    if r["v200"] <= 0 or r["y1"] <= 0:      # gate binario: sin tendencia, no compite
        return None
    s = (12 * r["m1"] + 4 * r["m3"] + 2 * r["m6"] + 1 * r["y1"]) / 4
    s += ((r["rng"] or 50) - 50) * 0.10     # cercania al maximo de 52s (George & Hwang 2004)
    return round(s, 1)

def peso_sugerido(rs: list) -> dict:
    """La volatilidad ya no entra al puntaje: define el tamaño. w proporcional a 1/vol."""
    inv = {r["sym"]: 1.0 / r["vol"] for r in rs if r.get("vol")}
    tot = sum(inv.values()) or 1.0
    return {s: round(v / tot * 100, 1) for s, v in inv.items()}

# ─────────────────────────── alertas ───────────────────────────
# Umbral de movimiento diario en SIGMAS del propio instrumento, no en % fijo.
# Un 4% fijo disparaba 218 alertas al año y significaba 0,4 sigmas en un ETF y
# 10,9 en otro: la misma alerta para un no-evento y para una catastrofe.
# A 4 sigmas quedan 38 al año, y sirve igual para XLU que para SQM-B.
UMBRALES = {"sigmas_dia": 4.0, "vix_nivel": 25.0,
            "banda_ma200": 2.0,                 # histeresis: sin banda hay 8 cruces/año por ETF
            "term_entra": 70, "term_sale": 60,  # histeresis del termometro
            "term_cae": 40, "term_vuelve": 50}

def _estado_ma(prev_est, v200, banda):
    """Estado con histeresis. Solo cambia cuando el precio pasa la banda entera
    del lado contrario, no cada vez que roza la linea."""
    est = prev_est or ("arriba" if v200 > 0 else "abajo")
    if est == "arriba" and v200 < -banda:
        return "abajo", True
    if est == "abajo" and v200 > banda:
        return "arriba", True
    return est, False

def detectar_alertas(M: dict, term: dict, prev: dict | None) -> tuple[list[dict], dict]:
    A = []
    p_term = (prev or {}).get("termometro")
    p_inst = (prev or {}).get("inst", {})
    p_est = (prev or {}).get("estado", {})
    p_reg = (prev or {}).get("regimen_alto")
    sc = term.get("score")
    estados, reg_alto = {}, p_reg

    if sc is not None:
        if p_reg is not True and sc >= UMBRALES["term_entra"]:
            reg_alto = True
            A.append({"n": 1, "t": f"El termómetro entró en riesgo encendido pleno ({sc})",
                      "d": f"cruzó {UMBRALES['term_entra']}" + (f" desde {p_term}" if p_term else "") + "."})
        elif p_reg is True and sc < UMBRALES["term_sale"]:
            reg_alto = False
            A.append({"n": 2, "t": f"El termómetro salió del régimen alto ({sc})",
                      "d": f"cayó bajo {UMBRALES['term_sale']}" + (f" desde {p_term}" if p_term else "") + "."})
        if p_term is not None and p_term >= UMBRALES["term_vuelve"] and sc < UMBRALES["term_cae"]:
            A.append({"n": 3, "t": f"El termómetro se desplomó a {sc}",
                      "d": f"venía en {p_term}. El mercado se puso defensivo de golpe."})

    vix = M.get("^VIX")
    if vix and vix["px"] >= UMBRALES["vix_nivel"] > p_inst.get("^VIX", {}).get("px", 0):
        # el salto porcentual de un dia se saco: los spikes del VIX revierten y
        # generaban alertas que no sobrevivian a la sesion siguiente.
        A.append({"n": 2, "t": f"El VIX cruzó {UMBRALES['vix_nivel']:.0f} ({vix['px']:.2f})",
                  "d": "volatilidad en zona de estrés."})

    for sym, r in M.items():
        if r["v200"] is None:
            continue
        nom = r["nombre"]
        est, cambio = _estado_ma(p_est.get(sym), r["v200"], UMBRALES["banda_ma200"])
        estados[sym] = est
        if r["grupo"] == "MAC":
            continue
        if cambio and p_est.get(sym):
            arriba = est == "arriba"
            A.append({"n": 1 if arriba else 2,
                      "t": f"{sym} {'recuperó' if arriba else 'perdió'} su media de 200",
                      "d": f"{nom} — {r['v200']:+.1f}%, cruzando la banda de "
                           f"±{UMBRALES['banda_ma200']:.0f}% que filtra el ruido."})
        ant = p_inst.get(sym)
        if ant and ant.get("cross") and r["cross"] and ant["cross"] != r["cross"]:
            alcista = r["cross"] == "golden"
            A.append({"n": 1 if alcista else 2,
                      "t": f"{sym}: cruce {'alcista' if alcista else 'bajista'} confirmado",
                      "d": f"{nom} — la media de 50 cruzó {'sobre' if alcista else 'bajo'} la de 200."})
        # movimiento del dia medido contra la volatilidad del propio instrumento
        if r["d1"] is not None and not r.get("rezago") and r.get("vol"):
            sigma = r["vol"] / math.sqrt(252)
            if sigma > 0 and abs(r["d1"]) / sigma >= UMBRALES["sigmas_dia"]:
                A.append({"n": 2, "t": f"{sym} se movió {r['d1']:+.1f}% en el día",
                          "d": f"{nom} — {abs(r['d1'])/sigma:.1f} sigmas contra su propia "
                               f"volatilidad. Fuera de lo normal para este instrumento."})
    A.sort(key=lambda x: x["n"])
    return A[:12], {"estado": estados, "regimen_alto": reg_alto}

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

    v3m = bajar_vix3m()
    if v3m: log(f"  VIX3M {v3m:.2f} (CBOE)")
    term = termometro(M, v3m)

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

    alertas, estado = detectar_alertas(M, term, prev)

    for r in M.values():
        r["puntaje"] = puntaje(r)
    rk = sorted([r for r in M.values()
                 if r["grupo"] not in ("MAC", "CL") and r["puntaje"] is not None],
                key=lambda r: r["puntaje"], reverse=True)
    pesos = peso_sugerido(rk[:8])
    for r in rk:
        r["peso"] = pesos.get(r["sym"])

    entrada = {"fecha": fmkt, "ts": int(time.time()), "termometro": term["score"],
               "pilares": {p["t"]: p["score"] for p in term["pilares"]},
               # los CRUDOS son los que permiten recalcular si cambia una formula;
               # el score ya normalizado es irrecuperable
               "crudos": {p["t"]: p["val"] for p in term["pilares"]},
               "estado": estado["estado"], "regimen_alto": estado["regimen_alto"],
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
        "pesos": pesos,
        "alertas": alertas,
        "calendario": cal,
        "lv_sobreponderar": [s for s in LV_SOBREPONDERAR if s in M],
        "vix3m": v3m,
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
