"""
Coach con Claude (API de Anthropic): arma un resumen compacto de los datos locales
y conversa sobre ellos con streaming. Sin dependencias de Qt.

Clave de API: se configura desde la app (se guarda en ~/.config/garmin_progreso/config.json
con permisos 600) o por variable de entorno ANTHROPIC_API_KEY.
"""
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import garmin_core as core

CONFIG_PATH = Path("~/.config/garmin_progreso/config.json").expanduser()
INFORMES_DIR = core.DATA_DIR / "informes"
MODELOS = ["claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001"]
DEFAULTS = {"api_key": "", "modelo": MODELOS[0], "dias_contexto": 28, "perfil": "",
            "max_tokens": 2500}
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

SYSTEM = """Sos un entrenador y analista de datos deportivos. Trabajás con las métricas \
de Garmin del usuario, que vienen dentro de <datos_garmin>. Respondé en español rioplatense.

Criterios:
- Basate en los datos: citá valores y comparaciones concretas (ej. "HRV 7 d: 48 ms vs 53 ms \
las 4 semanas previas"). Si falta un dato, decilo; no inventes.
- Distinguí señal de ruido: una noche mala no es tendencia. Usá medias, pendientes y el \
contexto de carga.
- Recomendaciones accionables y priorizadas (qué hacer, cuánto, cuándo). Pocas y buenas.
- Considerá la relación carga ↔ recuperación: FC en reposo en alza, HRV en baja, sueño corto \
o readiness bajo sostenidos sugieren bajar la carga.
- Progresiones prudentes: evitá subir el volumen semanal mucho más de un 10-30 % de golpe.
- No diagnostiques enfermedades. Si ves señales que merecen atención médica (FC en reposo \
anormalmente alta varios días, caídas bruscas y sostenidas de HRV con síntomas, etc.), \
sugerí consultar a un profesional, sin alarmismo.
- Formato: Markdown breve. Títulos cortos, listas cuando ayuden, sin relleno.

Notas sobre los datos: ritmo en min/km decimal (5.5 = 5:30); sueño en horas; \
min_intensidad = moderados + 2 × vigorosos; carga = training load de Garmin por actividad; \
Body Battery 0-100; readiness 0-100; semanas de lunes a domingo (la actual está incompleta)."""


class SinClave(RuntimeError):
    pass


# ---------------------------------------------------------------- configuración
def cargar_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception:
            pass
    if not cfg["api_key"]:
        cfg["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


def guardar_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(CONFIG_PATH, 0o600)


# ---------------------------------------------------------------- contexto
def _v(x):
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.2f}".rstrip("0").rstrip(".")
    return str(x)


def _tabla(filas: list[dict], cols: list[str]) -> str:
    if not filas:
        return "(sin datos)"
    return "\n".join([";".join(cols)] + [";".join(_v(f.get(c)) for c in cols) for f in filas])


def construir_contexto(dias: int = 28) -> str:
    hoy = date.today()
    desde = hoy - timedelta(days=dias - 1)
    p = [f"Hoy: {DIAS_SEMANA[hoy.weekday()]} {hoy.isoformat()}"]

    p.append(f"## Métricas diarias (últimos {dias} días)\n" + _tabla(
        core.metricas_diarias(desde, hoy),
        ["fecha", "fc_reposo", "hrv_noche", "hrv_semanal", "hrv_estado", "sueno_h",
         "sueno_profundo_h", "sueno_score", "readiness", "bb_max", "bb_min", "estres_medio",
         "pasos", "min_intensidad"]))

    p.append(f"## Actividades (últimos {dias} días)\n" + _tabla(
        core.actividades(desde, hoy),
        ["fecha", "tipo", "nombre", "distancia_km", "duracion_min", "ritmo_min_km",
         "fc_media", "fc_max", "desnivel_m", "carga", "te_aerobico", "te_anaerobico"]))

    p.append("## Volumen semanal (12 semanas, por tipo)\n" + _tabla(
        core.resumen_semanal(12, por_tipo=True),
        ["semana", "tipo", "n", "km", "minutos", "carga", "fc_media"]))

    tend = []
    claves = [("diario", m, None) for m in
              ("fc_reposo", "hrv_noche", "sueno_h", "readiness", "estres_medio", "vo2max")]
    claves.append(("peso", "peso_kg", None))
    for t in core.tipos_actividad()[:3]:
        a_pie = "run" in t or t in ("walking", "hiking")
        claves += [("actividades", "ritmo_min_km" if a_pie else "vel_kmh", t),
                   ("actividades", "fc_media", t)]
    for fuente, m, tipo in claves:
        r = core.tendencia(fuente, m, 90, tipo)
        if "error" in r:
            continue
        tend.append({"metrica": m + (f" [{tipo}]" if tipo else ""), "n": r["n"],
                     "media": r["media"], "desvio": r["desvio"],
                     "pend_semana": r["pendiente_por_semana"],
                     "ult7": r["media_ultimos"], "prev7": r["media_previos"],
                     "veredicto": r["veredicto"]})
    p.append("## Tendencias 90 días (regresión lineal)\n" + _tabla(
        tend, ["metrica", "n", "media", "desvio", "pend_semana", "ult7", "prev7", "veredicto"]))

    peso = core.consulta_sql(
        "SELECT fecha, peso_kg, grasa_pct FROM peso ORDER BY fecha DESC LIMIT 12")
    p.append("## Últimos pesajes\n" + _tabla(list(reversed(peso)),
                                              ["fecha", "peso_kg", "grasa_pct"]))
    return "\n\n".join(p)


# ---------------------------------------------------------------- conversación
class Coach:
    def __init__(self):
        self.cfg = cargar_config()
        self.historial: list[dict] = []
        self.transcripcion: list[tuple[str, str]] = []   # (rol, texto visible)

    def reiniciar(self):
        self.historial.clear()
        self.transcripcion.clear()

    def recargar_config(self):
        self.cfg = cargar_config()

    def _system(self) -> str:
        perfil = self.cfg.get("perfil", "").strip()
        return SYSTEM + (f"\n\nPerfil y objetivos del usuario:\n{perfil}" if perfil else "")

    def preguntar(self, texto: str):
        """Generador que va devolviendo fragmentos de la respuesta."""
        if not self.cfg.get("api_key"):
            raise SinClave("Falta configurar la API key de Anthropic.")
        import anthropic

        if not self.historial:   # primer mensaje: adjunta los datos
            contenido = (f"<datos_garmin>\n{construir_contexto(self.cfg['dias_contexto'])}"
                         f"\n</datos_garmin>\n\n{texto}")
        else:
            contenido = texto
        self.historial.append({"role": "user", "content": contenido})
        self.transcripcion.append(("user", texto))

        cliente = anthropic.Anthropic(api_key=self.cfg["api_key"])
        partes, completo = [], False
        try:
            with cliente.messages.stream(model=self.cfg["modelo"],
                                         max_tokens=int(self.cfg["max_tokens"]),
                                         system=self._system(),
                                         messages=self.historial) as st:
                for t in st.text_stream:
                    partes.append(t)
                    yield t
            completo = True
        finally:
            respuesta = "".join(partes)
            if completo or respuesta:
                if not completo:
                    respuesta += "\n\n*(respuesta interrumpida)*"
                self.historial.append({"role": "assistant", "content": respuesta})
                self.transcripcion.append(("assistant", respuesta))
            else:   # falló sin respuesta: deshacer el turno para no romper el historial
                self.historial.pop()
                self.transcripcion.pop()

    def markdown(self, en_curso: str | None = None) -> str:
        bloques = []
        for rol, txt in self.transcripcion:
            bloques.append(f"**🧑 Vos:** {txt}" if rol == "user" else txt)
        if en_curso is not None:
            bloques.append(en_curso + " ▍")
        return "\n\n---\n\n".join(bloques)

    def guardar(self) -> Path | None:
        if not self.transcripcion:
            return None
        INFORMES_DIR.mkdir(parents=True, exist_ok=True)
        ruta = INFORMES_DIR / f"{datetime.now():%Y-%m-%d_%H%M}_coach.md"
        ruta.write_text(f"# Coach — {datetime.now():%Y-%m-%d %H:%M}\n\n"
                        f"Modelo: {self.cfg['modelo']}\n\n---\n\n" + self.markdown(),
                        encoding="utf-8")
        return ruta
