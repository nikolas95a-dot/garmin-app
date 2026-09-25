"""
Núcleo compartido (app de escritorio + servidor MCP):
autenticación, base SQLite local, sincronización con Garmin Connect y análisis.

Variables de entorno opcionales:
  GARMINTOKENS  ruta de tokens    (default ~/.garminconnect)
  GARMIN_DATA   carpeta de datos  (default ~/garmin_data)
"""
import io
import json
import os
import sqlite3
import statistics
import time
import zipfile
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import Garmin

TOKENSTORE = str(Path(os.environ.get("GARMINTOKENS", "~/.garminconnect")).expanduser())
DATA_DIR = Path(os.environ.get("GARMIN_DATA", "~/garmin_data")).expanduser()
DB_PATH = DATA_DIR / "garmin.db"
FILES_DIR = DATA_DIR / "files"
PAUSA = 0.6        # segundos entre llamadas a la API (evita 429)
DIAS_VIVOS = 3     # los últimos N días se re-sincronizan siempre

SCHEMA = """
CREATE TABLE IF NOT EXISTS diario(
  fecha TEXT PRIMARY KEY,
  pasos INTEGER, fc_reposo INTEGER, estres_medio INTEGER,
  bb_max INTEGER, bb_min INTEGER, kcal_activas INTEGER, min_intensidad INTEGER,
  sueno_h REAL, sueno_profundo_h REAL, sueno_rem_h REAL, sueno_score INTEGER,
  hrv_noche INTEGER, hrv_semanal INTEGER, hrv_estado TEXT,
  readiness INTEGER, vo2max REAL,
  raw TEXT, actualizado TEXT);
CREATE TABLE IF NOT EXISTS actividades(
  id INTEGER PRIMARY KEY, fecha TEXT, tipo TEXT, nombre TEXT,
  distancia_km REAL, duracion_min REAL, ritmo_min_km REAL, vel_kmh REAL,
  fc_media INTEGER, fc_max INTEGER, desnivel_m REAL, kcal INTEGER,
  te_aerobico REAL, te_anaerobico REAL, carga REAL, vo2max REAL, raw TEXT);
CREATE TABLE IF NOT EXISTS peso(
  fecha TEXT PRIMARY KEY, peso_kg REAL, grasa_pct REAL, imc REAL);
CREATE INDEX IF NOT EXISTS idx_act_fecha ON actividades(fecha);
"""

TABLAS = {
    "diario": ("diario", ["fc_reposo", "hrv_noche", "hrv_semanal", "sueno_h", "sueno_profundo_h",
                          "sueno_rem_h", "sueno_score", "bb_max", "bb_min", "estres_medio",
                          "pasos", "kcal_activas", "min_intensidad", "readiness", "vo2max"]),
    "actividades": ("actividades", ["distancia_km", "duracion_min", "ritmo_min_km", "vel_kmh",
                                    "fc_media", "fc_max", "desnivel_m", "kcal", "te_aerobico",
                                    "te_anaerobico", "carga", "vo2max"]),
    "peso": ("peso", ["peso_kg", "grasa_pct", "imc"]),
}

ETIQUETAS = {
    "pasos": ("Pasos", ""), "fc_reposo": ("FC en reposo", "ppm"),
    "estres_medio": ("Estrés medio", ""), "bb_max": ("Body Battery máx", ""),
    "bb_min": ("Body Battery mín", ""), "kcal_activas": ("Kcal activas", "kcal"),
    "min_intensidad": ("Minutos de intensidad", "min"), "sueno_h": ("Sueño", "h"),
    "sueno_profundo_h": ("Sueño profundo", "h"), "sueno_rem_h": ("Sueño REM", "h"),
    "sueno_score": ("Puntaje de sueño", ""), "hrv_noche": ("HRV noche", "ms"),
    "hrv_semanal": ("HRV semanal", "ms"), "readiness": ("Training readiness", ""),
    "vo2max": ("VO2max", "ml/kg/min"),
    "distancia_km": ("Distancia", "km"), "duracion_min": ("Duración", "min"),
    "ritmo_min_km": ("Ritmo", "min/km"), "vel_kmh": ("Velocidad", "km/h"),
    "fc_media": ("FC media", "ppm"), "fc_max": ("FC máx", "ppm"),
    "desnivel_m": ("Desnivel", "m"), "kcal": ("Kcal", "kcal"),
    "te_aerobico": ("TE aeróbico", ""), "te_anaerobico": ("TE anaeróbico", ""),
    "carga": ("Carga", ""), "peso_kg": ("Peso", "kg"),
    "grasa_pct": ("Grasa corporal", "%"), "imc": ("IMC", ""),
}

# Dirección "buena" de cada métrica: +1 subir es mejor, -1 bajar es mejor, 0 neutra
MEJOR = {"fc_reposo": -1, "estres_medio": -1, "ritmo_min_km": -1, "grasa_pct": -1,
         "peso_kg": 0, "imc": 0, "fc_media": 0, "fc_max": 0, "duracion_min": 0,
         "distancia_km": 0, "desnivel_m": 0, "kcal": 0, "carga": 0,
         "te_aerobico": 0, "te_anaerobico": 0}

ENTEROS = {"pasos", "fc_reposo", "estres_medio", "bb_max", "bb_min", "kcal_activas",
           "min_intensidad", "sueno_score", "hrv_noche", "hrv_semanal", "readiness",
           "fc_media", "fc_max", "kcal", "carga", "desnivel_m"}

# Valores 0 que en realidad significan "sin dato" (reloj no usado, etc.)
SIN_CERO = {"pasos", "fc_reposo", "bb_max", "sueno_h", "sueno_score", "hrv_noche",
            "hrv_semanal", "vo2max", "peso_kg", "readiness"}


class SinSesion(RuntimeError):
    pass


# ---------------------------------------------------------------- infraestructura
def db(readonly: bool = False) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if readonly:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(DB_PATH)
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


_cliente = None


def hay_tokens() -> bool:
    return Path(TOKENSTORE).exists()


def cliente() -> Garmin:
    global _cliente
    if _cliente is None:
        if not hay_tokens():
            raise SinSesion("No hay sesión guardada. Iniciá sesión en Garmin.")
        g = Garmin()
        try:
            g.login(TOKENSTORE)
        except Exception as e:
            raise SinSesion(f"La sesión guardada no es válida ({e}). Iniciá sesión de nuevo.") from e
        _cliente = g
    return _cliente


def login(email: str, password: str, prompt_mfa) -> Garmin:
    """Login con credenciales; guarda tokens. prompt_mfa() debe devolver el código."""
    global _cliente
    g = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    g.login(TOKENSTORE)
    if not hay_tokens() and hasattr(g, "garth"):   # versiones viejas
        g.garth.dump(TOKENSTORE)
    _cliente = g
    return g


def _llamar(fn, *args):
    try:
        r = fn(*args)
    except Exception:
        r = None
    time.sleep(PAUSA)
    return r


def _dig(d, *claves):
    for k in claves:
        if isinstance(d, list):
            d = d[0] if d else None
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _primero(x):
    if isinstance(x, list):
        return x[0] if x else {}
    return x if isinstance(x, dict) else {}


def _horas(seg):
    return round(seg / 3600, 2) if seg else None


def _upsert(con, tabla, fila):
    cols = ",".join(fila)
    con.execute(f"INSERT OR REPLACE INTO {tabla}({cols}) VALUES({','.join('?' * len(fila))})",
                list(fila.values()))


def _sin_raw(filas):
    return [{k: r[k] for k in r.keys() if k != "raw"} for r in filas]


def _iso(d):
    return d.isoformat() if isinstance(d, date) else d


# ---------------------------------------------------------------- sincronización
def _sync_dia(g: Garmin, f: str) -> dict:
    s = _llamar(g.get_user_summary, f) or {}
    sl = _llamar(g.get_sleep_data, f) or {}
    hrv = _llamar(g.get_hrv_data, f) or {}
    tr = _primero(_llamar(g.get_training_readiness, f))
    mm = _primero(_llamar(g.get_max_metrics, f))
    dto = (sl.get("dailySleepDTO") or {}) if isinstance(sl, dict) else {}
    hs = (hrv.get("hrvSummary") or {}) if isinstance(hrv, dict) else {}
    return dict(
        fecha=f,
        pasos=s.get("totalSteps"),
        fc_reposo=s.get("restingHeartRate"),
        estres_medio=s.get("averageStressLevel"),
        bb_max=s.get("bodyBatteryHighestValue"),
        bb_min=s.get("bodyBatteryLowestValue"),
        kcal_activas=s.get("activeKilocalories"),
        min_intensidad=(s.get("moderateIntensityMinutes") or 0)
        + 2 * (s.get("vigorousIntensityMinutes") or 0),
        sueno_h=_horas(dto.get("sleepTimeSeconds")),
        sueno_profundo_h=_horas(dto.get("deepSleepSeconds")),
        sueno_rem_h=_horas(dto.get("remSleepSeconds")),
        sueno_score=_dig(dto, "sleepScores", "overall", "value"),
        hrv_noche=hs.get("lastNightAvg"),
        hrv_semanal=hs.get("weeklyAvg"),
        hrv_estado=hs.get("status"),
        readiness=tr.get("score"),
        vo2max=_dig(mm, "generic", "vo2MaxPreciseValue"),
        raw=json.dumps({"summary": s, "sleep": dto, "hrv": hs, "readiness": tr, "max": mm},
                       default=str),
        actualizado=datetime.now().isoformat(timespec="seconds"),
    )


def _fila_actividad(a: dict) -> dict:
    dist = (a.get("distance") or 0) / 1000
    dur = (a.get("duration") or 0) / 60
    return dict(
        id=a["activityId"],
        fecha=(a.get("startTimeLocal") or "")[:10],
        tipo=_dig(a, "activityType", "typeKey"),
        nombre=a.get("activityName"),
        distancia_km=round(dist, 3),
        duracion_min=round(dur, 1),
        ritmo_min_km=round(dur / dist, 2) if dist > 0.1 else None,
        vel_kmh=round(dist / (dur / 60), 2) if dur > 0 else None,
        fc_media=a.get("averageHR"),
        fc_max=a.get("maxHR"),
        desnivel_m=a.get("elevationGain"),
        kcal=a.get("calories"),
        te_aerobico=a.get("aerobicTrainingEffect"),
        te_anaerobico=a.get("anaerobicTrainingEffect"),
        carga=a.get("activityTrainingLoad"),
        vo2max=a.get("vO2MaxValue"),
        raw=json.dumps(a, default=str),
    )


def sincronizar(dias: int = 30, forzar: bool = False, progreso=None, cancelar=None) -> dict:
    """progreso(i, total, mensaje) y cancelar() -> bool son callbacks opcionales."""
    g = cliente()
    hoy = date.today()
    desde = hoy - timedelta(days=dias - 1)
    limite_vivo = hoy - timedelta(days=DIAS_VIVOS)
    with closing(db()) as con:
        existentes = {r[0] for r in con.execute("SELECT fecha FROM diario WHERE fecha >= ?",
                                                (desde.isoformat(),))}
        pendientes = []
        d = desde
        while d <= hoy:
            if forzar or d.isoformat() not in existentes or d >= limite_vivo:
                pendientes.append(d)
            d += timedelta(days=1)

        total = len(pendientes) + 2
        n_dias = 0
        cancelado = False
        for i, d in enumerate(pendientes):
            if cancelar and cancelar():
                cancelado = True
                break
            if progreso:
                progreso(i, total, f"Día {d.isoformat()}")
            _upsert(con, "diario", _sync_dia(g, d.isoformat()))
            con.commit()
            n_dias += 1

        n_act = n_peso = 0
        if not cancelado:
            if progreso:
                progreso(total - 2, total, "Actividades")
            acts = _llamar(g.get_activities_by_date, desde.isoformat(), hoy.isoformat()) or []
            for a in acts:
                _upsert(con, "actividades", _fila_actividad(a))
            n_act = len(acts)

            if progreso:
                progreso(total - 1, total, "Peso")
            comp = _llamar(g.get_body_composition, desde.isoformat(), hoy.isoformat()) or {}
            for w in comp.get("dateWeightList") or []:
                if w.get("weight") is None:
                    continue
                _upsert(con, "peso", dict(fecha=w.get("calendarDate"),
                                          peso_kg=round(w["weight"] / 1000, 2),
                                          grasa_pct=w.get("bodyFat"), imc=w.get("bmi")))
                n_peso += 1
            con.commit()
        if progreso:
            progreso(total, total, "Cancelado" if cancelado else "Listo")
    return {"dias_actualizados": n_dias, "actividades": n_act, "registros_peso": n_peso,
            "cancelado": cancelado, "rango": [desde.isoformat(), hoy.isoformat()]}


# ---------------------------------------------------------------- consultas
def metricas_diarias(desde, hasta) -> list[dict]:
    with closing(db()) as con:
        return _sin_raw(con.execute(
            "SELECT * FROM diario WHERE fecha BETWEEN ? AND ? ORDER BY fecha",
            (_iso(desde), _iso(hasta))).fetchall())


def actividades(desde, hasta, tipo=None) -> list[dict]:
    q, p = "SELECT * FROM actividades WHERE fecha BETWEEN ? AND ?", [_iso(desde), _iso(hasta)]
    if tipo:
        q += " AND tipo = ?"
        p.append(tipo)
    with closing(db()) as con:
        return _sin_raw(con.execute(q + " ORDER BY fecha", p).fetchall())


def tipos_actividad() -> list[str]:
    with closing(db()) as con:
        return [r[0] for r in con.execute(
            "SELECT tipo FROM actividades WHERE tipo IS NOT NULL "
            "GROUP BY tipo ORDER BY COUNT(*) DESC")]


def serie(fuente: str, metrica: str, desde=None, tipo=None) -> list[tuple[date, float]]:
    tabla, cols = TABLAS[fuente]
    if metrica not in cols:
        raise ValueError(f"Métrica inválida: {metrica}")
    q, p = f"SELECT fecha, {metrica} FROM {tabla} WHERE {metrica} IS NOT NULL", []
    if metrica in SIN_CERO:
        q += f" AND {metrica} > 0"
    if desde:
        q += " AND fecha >= ?"
        p.append(_iso(desde))
    if tipo and fuente == "actividades":
        q += " AND tipo = ?"
        p.append(tipo)
    with closing(db()) as con:
        filas = con.execute(q + " ORDER BY fecha", p).fetchall()
    return [(date.fromisoformat(f), float(v)) for f, v in filas if f]


def tendencia(fuente: str, metrica: str, dias: int = 90, tipo=None, ventana: int = 7) -> dict:
    if fuente not in TABLAS:
        return {"error": f"fuente inválida; opciones: {list(TABLAS)}"}
    if metrica not in TABLAS[fuente][1]:
        return {"error": "métrica inválida", "disponibles": TABLAS[fuente][1]}
    hoy = date.today()
    s = serie(fuente, metrica, hoy - timedelta(days=dias), tipo)
    if len(s) < 3:
        return {"error": "Pocos datos para analizar.", "n": len(s)}
    fechas = [f for f, _ in s]
    ys = [v for _, v in s]
    xs = [(f - fechas[0]).days for f in fechas]
    pend = statistics.linear_regression(xs, ys).slope * 7 if len(set(xs)) > 1 else 0.0
    c1, c2 = hoy - timedelta(days=ventana), hoy - timedelta(days=2 * ventana)
    ult = [y for f, y in zip(fechas, ys) if f > c1]
    prev = [y for f, y in zip(fechas, ys) if c2 < f <= c1]
    m_ult = statistics.fmean(ult) if ult else None
    m_prev = statistics.fmean(prev) if prev else None
    desvio = statistics.stdev(ys)
    cambio_total = pend * (xs[-1] / 7) if xs[-1] else 0.0
    direccion = MEJOR.get(metrica, 1)
    if abs(cambio_total) < 0.5 * desvio or direccion == 0:
        veredicto = "estable" if abs(cambio_total) < 0.5 * desvio else (
            "en aumento" if cambio_total > 0 else "en descenso")
    else:
        veredicto = "mejorando" if cambio_total * direccion > 0 else "empeorando"
    return {
        "metrica": metrica, "fuente": fuente, "tipo_actividad": tipo, "n": len(ys),
        "desde": fechas[0].isoformat(), "hasta": fechas[-1].isoformat(),
        "media": round(statistics.fmean(ys), 3), "mediana": round(statistics.median(ys), 3),
        "desvio": round(desvio, 3), "min": min(ys), "max": max(ys),
        "pendiente_por_semana": round(pend, 4), "cambio_en_periodo": round(cambio_total, 3),
        "veredicto": veredicto, "ventana": ventana,
        "media_ultimos": round(m_ult, 3) if m_ult is not None else None,
        "media_previos": round(m_prev, 3) if m_prev is not None else None,
        "delta": round(m_ult - m_prev, 3) if m_ult is not None and m_prev is not None else None,
    }


def estado_actual(metricas: list[tuple[str, str]]) -> dict:
    """Último valor y media 7 días vs las 4 semanas previas, por métrica."""
    hoy = date.today()
    res = {}
    for fuente, m in metricas:
        s = serie(fuente, m, hoy - timedelta(days=60))
        if not s:
            res[f"{fuente}.{m}"] = None
            continue
        ult = [v for f, v in s if f > hoy - timedelta(days=7)]
        prev = [v for f, v in s if hoy - timedelta(days=35) < f <= hoy - timedelta(days=7)]
        m7 = statistics.fmean(ult) if ult else None
        mp = statistics.fmean(prev) if prev else None
        res[f"{fuente}.{m}"] = {
            "ultimo": s[-1][1], "fecha": s[-1][0].isoformat(), "media7": m7, "media_prev": mp,
            "delta": (m7 - mp) if m7 is not None and mp is not None else None,
        }
    return res


def resumen_semanal(semanas: int = 8, tipo=None, por_tipo: bool = False) -> list[dict]:
    """Volumen por semana (lunes a domingo). Sin por_tipo incluye semanas vacías."""
    hoy = date.today()
    inicio = hoy - timedelta(days=hoy.weekday()) - timedelta(weeks=semanas - 1)
    grupos = {}
    if not por_tipo:
        for i in range(semanas):
            grupos[((inicio + timedelta(weeks=i)).isoformat(), None)] = []
    for a in actividades(inicio, hoy, tipo):
        d = date.fromisoformat(a["fecha"])
        lunes = (d - timedelta(days=d.weekday())).isoformat()
        grupos.setdefault((lunes, a["tipo"] if por_tipo else None), []).append(a)
    res = []
    for (lunes, t), acts in sorted(grupos.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        con_fc = [a for a in acts if a["fc_media"] and a["duracion_min"]]
        dur_fc = sum(a["duracion_min"] for a in con_fc)
        fila = {
            "semana": lunes, "n": len(acts),
            "km": round(sum(a["distancia_km"] or 0 for a in acts), 1),
            "minutos": round(sum(a["duracion_min"] or 0 for a in acts)),
            "carga": round(sum(a["carga"] or 0 for a in acts)),
            "fc_media": round(sum(a["fc_media"] * a["duracion_min"] for a in con_fc) / dur_fc)
            if dur_fc else None,
        }
        if por_tipo:
            fila["tipo"] = t
        res.append(fila)
    return res


def detalle_actividad(activity_id: int) -> dict:
    g = cliente()
    return {"splits": _llamar(g.get_activity_splits, activity_id),
            "zonas_fc": _llamar(g.get_activity_hr_in_timezones, activity_id)}


def descargar_actividad(activity_id: int, formato: str = "fit") -> str:
    g = cliente()
    fmts = {"fit": Garmin.ActivityDownloadFormat.ORIGINAL,
            "gpx": Garmin.ActivityDownloadFormat.GPX,
            "tcx": Garmin.ActivityDownloadFormat.TCX,
            "kml": Garmin.ActivityDownloadFormat.KML,
            "csv": Garmin.ActivityDownloadFormat.CSV}
    formato = formato.lower()
    if formato not in fmts:
        raise ValueError(f"Formato no válido. Opciones: {', '.join(fmts)}")
    datos = g.download_activity(activity_id, dl_fmt=fmts[formato])
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    destino = FILES_DIR / f"{activity_id}.{formato}"
    if formato == "fit":   # ORIGINAL llega como zip con el .fit adentro
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            destino.write_bytes(z.read(z.namelist()[0]))
    else:
        destino.write_bytes(datos)
    return str(destino)


def consulta_sql(sql: str, limite: int = 500) -> list[dict]:
    if not sql.lstrip().lower().startswith(("select", "with")):
        raise ValueError("Solo se permiten consultas SELECT/WITH")
    if not DB_PATH.exists():
        raise ValueError("La base no existe todavía; sincronizá primero")
    with closing(db(readonly=True)) as con:
        return _sin_raw(con.execute(sql).fetchmany(limite))
