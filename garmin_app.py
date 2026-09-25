#!/usr/bin/env python3
"""Garmin Progreso — app de escritorio (PySide6 + pyqtgraph) sobre garmin_core."""
import statistics
import sys
from datetime import date, datetime, timedelta

import pyqtgraph as pg
from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt,
                            QThread, QTimer, QUrl, Signal)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox,
                               QSplitter, QTableView, QTabWidget, QToolBar, QVBoxLayout,
                               QWidget)

import garmin_core as core
from garmin_coach_ui import CoachTab

pg.setConfigOptions(antialias=True, background="w", foreground="k")

PERIODOS = [("30 días", 30), ("90 días", 90), ("6 meses", 182), ("1 año", 365), ("Todo", 36500)]
PANEL = [("diario", "fc_reposo"), ("diario", "hrv_noche"), ("diario", "sueno_h"),
         ("diario", "sueno_score"), ("diario", "readiness"), ("diario", "bb_max"),
         ("diario", "estres_medio"), ("diario", "pasos"), ("diario", "vo2max"),
         ("peso", "peso_kg")]
MINIS = [("diario", "fc_reposo"), ("diario", "hrv_noche"), ("peso", "peso_kg")]
AZUL, AZUL_OSC, ROJO = (80, 120, 200, 150), (30, 70, 160), (200, 60, 60)


# ---------------------------------------------------------------- utilidades
def etiqueta(m):
    n, u = core.ETIQUETAS.get(m, (m, ""))
    return f"{n} ({u})" if u else n


def fmt(m, v):
    if v is None:
        return "—"
    if m == "ritmo_min_km":
        mm, ss = int(v), round((v - int(v)) * 60)
        if ss == 60:
            mm, ss = mm + 1, 0
        return f"{mm}:{ss:02d}"
    if m in core.ENTEROS:
        return f"{v:,.0f}".replace(",", ".")
    return f"{v:.1f}"


def ts(d: date) -> float:
    return datetime(d.year, d.month, d.day).timestamp()


def media_movil(ys, n):
    out, ventana, s = [], [], 0.0
    for y in ys:
        ventana.append(y)
        s += y
        if len(ventana) > n:
            s -= ventana.pop(0)
        out.append(s / len(ventana))
    return out


class EjeY(pg.AxisItem):
    """Eje vertical que puede mostrar min:seg (ritmo)."""
    def __init__(self):
        super().__init__("left")
        self.ritmo = False

    def modo_ritmo(self, activo: bool):
        if activo != self.ritmo:
            self.ritmo = activo
            self.picture = None
            self.update()

    def tickStrings(self, values, scale, spacing):
        if self.ritmo:
            return [fmt("ritmo_min_km", v) for v in values]
        return super().tickStrings(values, scale, spacing)


def grafico_serie(plot, fuente, m, dias, tipo=None, ventana=7, leyenda=True):
    """Dibuja puntos + media móvil + recta de tendencia. Devuelve la serie."""
    s = core.serie(fuente, m, date.today() - timedelta(days=dias), tipo)
    plot.clear()
    plot.getViewBox().invertY(m == "ritmo_min_km")
    if not s:
        return s
    xs = [ts(d) for d, _ in s]
    ys = [v for _, v in s]
    kw = lambda n: {"name": n} if leyenda else {}
    plot.plot(xs, ys, pen=None, symbol="o", symbolSize=5, symbolBrush=AZUL, symbolPen=None,
              **kw("valores"))
    if len(ys) >= 2 and ventana > 1:
        plot.plot(xs, media_movil(ys, ventana), pen=pg.mkPen(AZUL_OSC, width=2),
                  **kw(f"media móvil {ventana}"))
    if len(set(xs)) > 1:
        r = statistics.linear_regression(xs, ys)
        plot.plot([xs[0], xs[-1]], [r.intercept + r.slope * xs[0], r.intercept + r.slope * xs[-1]],
                  pen=pg.mkPen(ROJO, width=1.5, style=Qt.DashLine), **kw("tendencia"))
    plot.autoRange()
    return s


def llenar_tipos(combo: QComboBox):
    actual = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("Todos", None)
    for t in core.tipos_actividad():
        combo.addItem(t, t)
    i = combo.findData(actual)
    combo.setCurrentIndex(i if i >= 0 else 0)
    combo.blockSignals(False)


def combo_periodo(indice=1) -> QComboBox:
    cb = QComboBox()
    for n, d in PERIODOS:
        cb.addItem(n, d)
    cb.setCurrentIndex(indice)
    return cb


def fila_controles(*pares) -> QHBoxLayout:
    h = QHBoxLayout()
    for texto, w in pares:
        if texto:
            h.addWidget(QLabel(texto))
        h.addWidget(w)
    h.addStretch()
    return h


class TablaModelo(QAbstractTableModel):
    def __init__(self, columnas):
        super().__init__()
        self.cols = [c for c, _ in columnas]
        self.enc = [e for _, e in columnas]
        self.filas = []

    def set_filas(self, filas):
        self.beginResetModel()
        self.filas = filas
        self.endResetModel()

    def rowCount(self, p=QModelIndex()):
        return 0 if p.isValid() else len(self.filas)

    def columnCount(self, p=QModelIndex()):
        return 0 if p.isValid() else len(self.cols)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        c = self.cols[idx.column()]
        v = self.filas[idx.row()].get(c)
        if role == Qt.DisplayRole:
            if isinstance(v, float):
                return fmt(c, v)
            return "" if v is None else str(v)
        if role == Qt.UserRole:
            return v
        if role == Qt.TextAlignmentRole and isinstance(v, (int, float)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def headerData(self, s, o, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and o == Qt.Horizontal:
            return self.enc[s]
        return None


def nueva_tabla(modelo) -> tuple[QTableView, QSortFilterProxyModel]:
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(modelo)
    proxy.setSortRole(Qt.UserRole)
    t = QTableView()
    t.setModel(proxy)
    t.setSortingEnabled(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setAlternatingRowColors(True)
    t.verticalHeader().hide()
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(False)
    return t, proxy


# ---------------------------------------------------------------- hilos y diálogos
class SyncWorker(QThread):
    progreso = Signal(int, int, str)
    terminado = Signal(dict)
    error = Signal(str, bool)

    def __init__(self, dias, forzar):
        super().__init__()
        self.dias, self.forzar, self._cancelar = dias, forzar, False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            r = core.sincronizar(self.dias, self.forzar,
                                 progreso=lambda i, t, m: self.progreso.emit(i, t, m),
                                 cancelar=lambda: self._cancelar)
            self.terminado.emit(r)
        except core.SinSesion as e:
            self.error.emit(str(e), True)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}", False)


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Iniciar sesión en Garmin Connect")
        self.nombre = ""
        self.email = QLineEdit()
        self.pwd = QLineEdit()
        self.pwd.setEchoMode(QLineEdit.Password)
        form = QFormLayout(self)
        form.addRow("Email:", self.email)
        form.addRow("Contraseña:", self.pwd)
        nota = QLabel(f"La contraseña no se guarda. Los tokens quedan en\n{core.TOKENSTORE}")
        nota.setStyleSheet("color: gray;")
        form.addRow(nota)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.intentar)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _mfa(self):
        QApplication.restoreOverrideCursor()
        codigo, _ = QInputDialog.getText(self, "Verificación", "Código MFA:")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        return codigo.strip()

    def intentar(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            g = core.login(self.email.text().strip(), self.pwd.text(), self._mfa)
            try:
                self.nombre = g.get_full_name() or ""
            except Exception:
                pass
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Login", f"No se pudo iniciar sesión:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self.accept()


class DetalleDialog(QDialog):
    COLS = [("n", "#"), ("distancia_km", "km"), ("duracion_min", "min"),
            ("ritmo_min_km", "Ritmo"), ("fc_media", "FC media"), ("fc_max", "FC máx"),
            ("desnivel_m", "Desnivel")]

    def __init__(self, act: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{act['fecha']} — {act.get('nombre') or act.get('tipo')}")
        self.resize(820, 560)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            f"<b>{act.get('tipo')}</b> · {fmt('distancia_km', act['distancia_km'])} km · "
            f"{fmt('duracion_min', act['duracion_min'])} min · ritmo "
            f"{fmt('ritmo_min_km', act['ritmo_min_km'])} · FC {fmt('fc_media', act['fc_media'])}"
            f" / {fmt('fc_max', act['fc_max'])} · carga {fmt('carga', act['carga'])}"))

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            d = core.detalle_actividad(act["id"])
        except Exception as e:
            d = {}
            v.addWidget(QLabel(f"<span style='color:red'>No se pudo obtener el detalle: {e}</span>"))
        finally:
            QApplication.restoreOverrideCursor()

        split = QSplitter(Qt.Vertical)
        laps = (d.get("splits") or {}).get("lapDTOs") or [] if isinstance(d.get("splits"), dict) else []
        filas = []
        for i, l in enumerate(laps, 1):
            km = (l.get("distance") or 0) / 1000
            mins = (l.get("duration") or 0) / 60
            filas.append({"n": i, "distancia_km": km, "duracion_min": mins,
                          "ritmo_min_km": mins / km if km > 0.05 else None,
                          "fc_media": l.get("averageHR"), "fc_max": l.get("maxHR"),
                          "desnivel_m": l.get("elevationGain")})
        modelo = TablaModelo(self.COLS)
        modelo.set_filas(filas)
        tabla, _ = nueva_tabla(modelo)
        split.addWidget(tabla)

        zonas = d.get("zonas_fc") or []
        if isinstance(zonas, list) and zonas:
            pw = pg.PlotWidget(title="Tiempo en zonas de FC (min)")
            xs = [z.get("zoneNumber", i + 1) for i, z in enumerate(zonas)]
            hs = [(z.get("secsInZone") or 0) / 60 for z in zonas]
            colores = [(120, 170, 220), (90, 180, 120), (230, 190, 60), (230, 130, 50), (210, 60, 60)]
            pw.addItem(pg.BarGraphItem(x=xs, height=hs, width=0.7,
                                       brushes=[colores[(x - 1) % 5] for x in xs]))
            pw.getAxis("bottom").setTicks([[
                (x, f"Z{x}\n≥{z.get('zoneLowBoundary', '')}") for x, z in zip(xs, zonas)]])
            split.addWidget(pw)
        v.addWidget(split)


# ---------------------------------------------------------------- pestañas
class Tarjeta(QFrame):
    clic = Signal(str, str)

    def __init__(self, fuente, m):
        super().__init__()
        self.fuente, self.m = fuente, m
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(190)
        nombre, self.unidad = core.ETIQUETAS[m]
        lay = QVBoxLayout(self)
        titulo = QLabel(nombre)
        titulo.setStyleSheet("color: gray;")
        self.valor = QLabel("—")
        self.valor.setStyleSheet("font-size: 26px; font-weight: bold;")
        self.delta = QLabel("")
        self.delta.setTextFormat(Qt.RichText)
        for w in (titulo, self.valor, self.delta):
            lay.addWidget(w)

    def mousePressEvent(self, e):
        self.clic.emit(self.fuente, self.m)

    def actualizar(self, e):
        if not e:
            self.valor.setText("—")
            self.delta.setText("<span style='color:gray'>sin datos</span>")
            return
        self.valor.setText(f"{fmt(self.m, e['ultimo'])} {self.unidad}")
        txt = f"<span style='color:gray'>último: {e['fecha']}</span>"
        d, prev = e["delta"], e["media_prev"]
        if d is not None:
            direc = core.MEJOR.get(self.m, 1)
            if direc == 0 or (prev and abs(d) < 0.01 * abs(prev)):
                col = "gray"
            else:
                col = "#2e8b57" if d * direc > 0 else "#c0392b"
            flecha = "▲" if d > 0 else ("▼" if d < 0 else "■")
            if self.m == "ritmo_min_km":
                val = fmt("ritmo_min_km", abs(d))
            elif self.m in core.ENTEROS and abs(d) >= 10:
                val = fmt(self.m, abs(d))
            else:
                val = f"{abs(d):.1f}"
            txt += f"<br><span style='color:{col}'>{flecha} {val} (7 d vs 4 sem. previas)</span>"
        self.delta.setText(txt)


class PanelTab(QWidget):
    ir_a = Signal(str, str)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        grid = QGridLayout()
        self.tarjetas = []
        for i, (f, m) in enumerate(PANEL):
            t = Tarjeta(f, m)
            t.clic.connect(self.ir_a)
            grid.addWidget(t, i // 5, i % 5)
            self.tarjetas.append(t)
        v.addLayout(grid)
        ayuda = QLabel("Clic en una tarjeta para ver su tendencia. Verde = mejora, rojo = empeora.")
        ayuda.setStyleSheet("color: gray;")
        v.addWidget(ayuda)
        self.info = QLabel()
        v.addWidget(self.info)
        mini = QHBoxLayout()
        self.minis = []
        for f, m in MINIS:
            pw = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()},
                               title=f"{etiqueta(m)} — 90 días")
            pw.showGrid(y=True, alpha=0.3)
            mini.addWidget(pw)
            self.minis.append((pw, f, m))
        v.addLayout(mini, 1)

    def refrescar(self):
        for pw, f, m in self.minis:
            grafico_serie(pw, f, m, 90, leyenda=False)
        est = core.estado_actual(PANEL)
        for t in self.tarjetas:
            t.actualizar(est.get(f"{t.fuente}.{t.m}"))
        n = core.consulta_sql("SELECT COUNT(*) AS d, MIN(fecha) AS a, MAX(fecha) AS b FROM diario")
        a = core.consulta_sql("SELECT COUNT(*) AS n FROM actividades")
        if n and n[0]["d"]:
            self.info.setText(f"Base local: {n[0]['d']} días ({n[0]['a']} → {n[0]['b']}), "
                              f"{a[0]['n']} actividades · {core.DB_PATH}")
        else:
            self.info.setText("Base vacía: sincronizá para empezar.")


class TendenciasTab(QWidget):
    def __init__(self):
        super().__init__()
        self.cb_fuente = QComboBox()
        for k in core.TABLAS:
            self.cb_fuente.addItem(k.capitalize(), k)
        self.cb_met = QComboBox()
        self.cb_tipo = QComboBox()
        self.cb_per = combo_periodo(1)
        self.sp_ven = QSpinBox()
        self.sp_ven.setRange(1, 60)
        self.sp_ven.setValue(7)
        self.sp_ven.setSuffix(" pts")

        self.eje_y = EjeY()
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(), "left": self.eje_y})
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.addLegend(offset=(10, 10))
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.RichText)
        self.stats.setAlignment(Qt.AlignTop)
        self.stats.setMinimumWidth(250)
        self.stats.setWordWrap(True)

        split = QSplitter()
        split.addWidget(self.plot)
        split.addWidget(self.stats)
        split.setStretchFactor(0, 4)
        v = QVBoxLayout(self)
        v.addLayout(fila_controles(("Fuente:", self.cb_fuente), ("Métrica:", self.cb_met),
                                   ("Tipo:", self.cb_tipo), ("Período:", self.cb_per),
                                   ("Media móvil:", self.sp_ven)))
        v.addWidget(split)

        self.cb_fuente.currentIndexChanged.connect(self._poblar_metricas)
        for w in (self.cb_met, self.cb_tipo, self.cb_per):
            w.currentIndexChanged.connect(self.dibujar)
        self.sp_ven.valueChanged.connect(self.dibujar)
        self._poblar_metricas()

    def _poblar_metricas(self):
        f = self.cb_fuente.currentData()
        self.cb_met.blockSignals(True)
        self.cb_met.clear()
        for m in core.TABLAS[f][1]:
            self.cb_met.addItem(etiqueta(m), m)
        self.cb_met.blockSignals(False)
        self.cb_tipo.setEnabled(f == "actividades")
        self.dibujar()

    def mostrar(self, fuente, m):
        self.cb_fuente.setCurrentIndex(self.cb_fuente.findData(fuente))
        self.cb_met.setCurrentIndex(self.cb_met.findData(m))

    def dibujar(self):
        f, m = self.cb_fuente.currentData(), self.cb_met.currentData()
        if not m:
            return
        dias = self.cb_per.currentData()
        tipo = self.cb_tipo.currentData() if f == "actividades" else None
        self.plot.setLabel("left", etiqueta(m))
        self.eje_y.modo_ritmo(m == "ritmo_min_km")
        if not grafico_serie(self.plot, f, m, dias, tipo, self.sp_ven.value()):
            self.stats.setText("Sin datos para este filtro.")
            return
        self._stats(core.tendencia(f, m, dias, tipo, ventana=7))

    def _stats(self, t):
        if "error" in t:
            self.stats.setText(t["error"])
            return
        m = t["metrica"]
        col = {"mejorando": "#2e8b57", "empeorando": "#c0392b"}.get(t["veredicto"], "gray")
        f = lambda v: fmt(m, v)
        pend = t["pendiente_por_semana"]
        pend_txt = (("+" if pend >= 0 else "-") + fmt("ritmo_min_km", abs(pend))
                    if m == "ritmo_min_km" else f"{pend:+.2f}")
        filas = [("Registros", t["n"]), ("Desde", t["desde"]), ("Hasta", t["hasta"]),
                 ("Media", f(t["media"])), ("Mediana", f(t["mediana"])),
                 ("Desvío", f"{t['desvio']:.2f}"), ("Mín / Máx", f"{f(t['min'])} / {f(t['max'])}"),
                 ("Pendiente / semana", pend_txt),
                 ("Últimos 7 d", f(t["media_ultimos"])), ("7 d previos", f(t["media_previos"]))]
        html = (f"<h3>{etiqueta(m)}</h3><p style='font-size:16px;color:{col}'>"
                f"<b>{t['veredicto'].capitalize()}</b></p><table cellspacing=4>")
        html += "".join(f"<tr><td style='color:gray'>{k}</td><td><b>{v}</b></td></tr>"
                        for k, v in filas)
        self.stats.setText(html + "</table>")


class ActividadesTab(QWidget):
    COLS = [("fecha", "Fecha"), ("tipo", "Tipo"), ("nombre", "Nombre"),
            ("distancia_km", "km"), ("duracion_min", "min"), ("ritmo_min_km", "Ritmo"),
            ("fc_media", "FC media"), ("fc_max", "FC máx"), ("desnivel_m", "Desnivel"),
            ("carga", "Carga"), ("te_aerobico", "TE aer."), ("vo2max", "VO2max")]
    mensaje = Signal(str)

    def __init__(self):
        super().__init__()
        self.cb_per = combo_periodo(1)
        self.cb_tipo = QComboBox()
        self.modelo = TablaModelo(self.COLS)
        self.tabla, self.proxy = nueva_tabla(self.modelo)
        botones = []
        for texto, fn in [("Detalle", self.detalle), ("Descargar FIT", lambda: self.descargar("fit")),
                          ("Descargar GPX", lambda: self.descargar("gpx")),
                          ("Abrir carpeta", self.abrir_carpeta)]:
            b = QPushButton(texto)
            b.clicked.connect(fn)
            botones.append((None, b))
        v = QVBoxLayout(self)
        v.addLayout(fila_controles(("Período:", self.cb_per), ("Tipo:", self.cb_tipo), *botones))
        v.addWidget(self.tabla)
        self.cb_per.currentIndexChanged.connect(self.refrescar)
        self.cb_tipo.currentIndexChanged.connect(self.refrescar)
        self.tabla.doubleClicked.connect(self.detalle)

    def refrescar(self):
        desde = date.today() - timedelta(days=self.cb_per.currentData())
        self.modelo.set_filas(core.actividades(desde, date.today(), self.cb_tipo.currentData()))
        self.tabla.sortByColumn(0, Qt.DescendingOrder)

    def _seleccion(self):
        filas = self.tabla.selectionModel().selectedRows()
        if not filas:
            QMessageBox.information(self, "Actividades", "Seleccioná una actividad.")
            return None
        return self.modelo.filas[self.proxy.mapToSource(filas[0]).row()]

    def detalle(self, *_):
        a = self._seleccion()
        if a:
            DetalleDialog(a, self).exec()

    def descargar(self, formato):
        a = self._seleccion()
        if not a:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ruta = core.descargar_actividad(a["id"], formato)
            self.mensaje.emit(f"Guardado: {ruta}")
        except Exception as e:
            QMessageBox.warning(self, "Descarga", f"No se pudo descargar:\n{e}")
        finally:
            QApplication.restoreOverrideCursor()

    def abrir_carpeta(self):
        core.FILES_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(core.FILES_DIR)))


class SemanalTab(QWidget):
    COLS = [("semana", "Semana (lunes)"), ("tipo", "Tipo"), ("n", "Act."), ("km", "km"),
            ("minutos", "Minutos"), ("carga", "Carga"), ("fc_media", "FC media")]
    METRICAS = [("km", "Kilómetros"), ("minutos", "Minutos"), ("carga", "Carga"),
                ("n", "Actividades")]

    def __init__(self):
        super().__init__()
        self.sp_sem = QSpinBox()
        self.sp_sem.setRange(4, 104)
        self.sp_sem.setValue(12)
        self.cb_tipo = QComboBox()
        self.cb_met = QComboBox()
        for k, n in self.METRICAS:
            self.cb_met.addItem(n, k)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(y=True, alpha=0.3)
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.modelo = TablaModelo(self.COLS)
        self.tabla, _ = nueva_tabla(self.modelo)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.plot)
        split.addWidget(self.tabla)
        split.setStretchFactor(0, 3)
        v = QVBoxLayout(self)
        v.addLayout(fila_controles(("Semanas:", self.sp_sem), ("Tipo:", self.cb_tipo),
                                   ("Métrica:", self.cb_met)))
        v.addWidget(self.info)
        v.addWidget(split)
        self.sp_sem.valueChanged.connect(self.refrescar)
        self.cb_tipo.currentIndexChanged.connect(self.refrescar)
        self.cb_met.currentIndexChanged.connect(self.refrescar)

    def refrescar(self):
        sem, tipo, m = self.sp_sem.value(), self.cb_tipo.currentData(), self.cb_met.currentData()
        datos = core.resumen_semanal(sem, tipo, por_tipo=False)
        self.modelo.set_filas(list(reversed(core.resumen_semanal(sem, tipo, por_tipo=True))))
        self.plot.clear()
        hs = [r[m] or 0 for r in datos]
        xs = list(range(len(hs)))
        brushes = [AZUL] * (len(hs) - 1) + [(80, 120, 200, 60)]   # semana actual (parcial) atenuada
        self.plot.addItem(pg.BarGraphItem(x=xs, height=hs, width=0.7, brushes=brushes))
        paso = max(1, len(xs) // 12)
        self.plot.getAxis("bottom").setTicks([[(i, datos[i]["semana"][5:]) for i in xs[::paso]]])
        self.plot.setLabel("left", self.cb_met.currentText())
        # última semana completa vs promedio de las 4 anteriores
        if len(hs) >= 6:
            ult, prev = hs[-2], statistics.fmean(hs[-6:-2])
            if prev:
                r = ult / prev
                col = "#2e8b57" if 0.8 <= r <= 1.3 else "#c0392b"
                self.info.setText(
                    f"Última semana completa: <b>{ult:g}</b> · media 4 previas: <b>{prev:.1f}</b> · "
                    f"ratio <b style='color:{col}'>{r:.2f}</b> "
                    f"<span style='color:gray'>(0.8–1.3 zona de progresión prudente; "
                    f"la barra clara es la semana en curso)</span>")
                return
        self.info.setText("<span style='color:gray'>La barra clara es la semana en curso.</span>")


# ---------------------------------------------------------------- ventana principal
class Ventana(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Garmin Progreso")
        self.resize(1250, 800)
        self.worker = None

        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)
        self.sp_dias = QSpinBox()
        self.sp_dias.setRange(1, 3650)
        self.sp_dias.setValue(30)
        self.sp_dias.setSuffix(" días")
        self.ck_forzar = QCheckBox("Forzar")
        self.ck_forzar.setToolTip("Volver a descargar días ya guardados")
        self.btn_sync = QPushButton("Sincronizar")
        self.btn_sync.clicked.connect(self.alternar_sync)
        btn_login = QPushButton("Iniciar sesión")
        btn_login.clicked.connect(self.login)
        for w in (QLabel(" Últimos "), self.sp_dias, self.ck_forzar, self.btn_sync):
            tb.addWidget(w)
        tb.addSeparator()
        tb.addWidget(btn_login)

        self.panel = PanelTab()
        self.tend = TendenciasTab()
        self.acts = ActividadesTab()
        self.sem = SemanalTab()
        self.coach = CoachTab()
        tabs = QTabWidget()
        for w, n in [(self.panel, "Panel"), (self.tend, "Tendencias"),
                     (self.acts, "Actividades"), (self.sem, "Semanal"), (self.coach, "Coach")]:
            tabs.addTab(w, n)
        self.tabs = tabs
        self.setCentralWidget(tabs)

        self.barra = QProgressBar()
        self.barra.setMaximumWidth(260)
        self.barra.hide()
        self.statusBar().addPermanentWidget(self.barra)

        self.panel.ir_a.connect(self.ir_tendencia)
        self.acts.mensaje.connect(lambda t: self.statusBar().showMessage(t, 8000))
        self.coach.mensaje.connect(lambda t: self.statusBar().showMessage(t, 8000))
        self.refrescar_todo()
        QTimer.singleShot(0, self.verificar_sesion)

    def verificar_sesion(self):
        if not core.hay_tokens():
            self.login()

    def login(self):
        dlg = LoginDialog(self)
        if dlg.exec():
            self.statusBar().showMessage(f"Sesión iniciada {dlg.nombre}".strip(), 6000)

    def ir_tendencia(self, fuente, m):
        self.tabs.setCurrentWidget(self.tend)
        self.tend.mostrar(fuente, m)

    def alternar_sync(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancelar()
            self.btn_sync.setEnabled(False)
            self.btn_sync.setText("Cancelando…")
            return
        self.worker = SyncWorker(self.sp_dias.value(), self.ck_forzar.isChecked())
        self.worker.progreso.connect(self._progreso)
        self.worker.terminado.connect(self._fin)
        self.worker.error.connect(self._error)
        self.btn_sync.setText("Cancelar")
        self.barra.setValue(0)
        self.barra.show()
        self.worker.start()

    def _restaurar(self):
        self.btn_sync.setText("Sincronizar")
        self.btn_sync.setEnabled(True)
        self.barra.hide()

    def _progreso(self, i, total, msg):
        self.barra.setMaximum(total)
        self.barra.setValue(i)
        self.statusBar().showMessage(msg)

    def _fin(self, r):
        self._restaurar()
        estado = "Cancelado" if r["cancelado"] else "Sincronizado"
        self.statusBar().showMessage(
            f"{estado}: {r['dias_actualizados']} días, {r['actividades']} actividades, "
            f"{r['registros_peso']} pesajes", 10000)
        self.refrescar_todo()

    def _error(self, msg, es_sesion):
        self._restaurar()
        if es_sesion:
            if QMessageBox.question(self, "Sesión", f"{msg}\n\n¿Iniciar sesión ahora?") \
                    == QMessageBox.Yes:
                self.login()
        else:
            QMessageBox.critical(self, "Error de sincronización", msg)
        self.refrescar_todo()

    def refrescar_todo(self):
        for cb in (self.tend.cb_tipo, self.acts.cb_tipo, self.sem.cb_tipo):
            llenar_tipos(cb)
        self.panel.refrescar()
        self.tend.dibujar()
        self.acts.refrescar()
        self.sem.refrescar()

    def closeEvent(self, e):
        for w in (self.worker, self.coach.worker):
            if w and w.isRunning():
                w.cancelar()
                w.wait(5000)
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Garmin Progreso")
    w = Ventana()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
