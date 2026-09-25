"""Pestaña Coach para garmin_app.py."""
from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

import garmin_coach as gc

ATAJOS = [
    ("📊 Análisis de la semana",
     "Analizá mi última semana: recuperación (HRV, FC en reposo, sueño, readiness), carga de "
     "entrenamiento y cómo vengo respecto de las semanas anteriores. Cerrá con 3 a 5 "
     "recomendaciones concretas."),
    ("🗓️ Plan próxima semana",
     "Con base en mis datos, proponé un plan para los próximos 7 días (sesiones, duración, "
     "intensidad y descansos), ajustado a mi estado de recuperación actual."),
    ("⚡ ¿Entreno fuerte hoy?",
     "Según mi readiness, HRV, sueño de anoche y la carga de los últimos días, ¿hoy conviene "
     "una sesión intensa, una suave o descanso? Justificá en pocas líneas."),
    ("📈 Tendencias de fondo",
     "Analizá las tendencias de los últimos 3 meses: qué mejoró, qué empeoró, qué está estable "
     "y qué debería vigilar."),
]


class ConfigCoachDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurar coach")
        self.setMinimumWidth(520)
        cfg = gc.cargar_config()
        self.key = QLineEdit(cfg["api_key"])
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("sk-ant-...")
        ver = QCheckBox("Mostrar")
        ver.toggled.connect(lambda v: self.key.setEchoMode(
            QLineEdit.Normal if v else QLineEdit.Password))
        fila_key = QHBoxLayout()
        fila_key.addWidget(self.key)
        fila_key.addWidget(ver)

        self.modelo = QComboBox()
        self.modelo.setEditable(True)
        self.modelo.addItems(gc.MODELOS)
        self.modelo.setCurrentText(cfg["modelo"])
        self.dias = QSpinBox()
        self.dias.setRange(7, 90)
        self.dias.setValue(int(cfg["dias_contexto"]))
        self.dias.setSuffix(" días")
        self.perfil = QPlainTextEdit(cfg["perfil"])
        self.perfil.setPlaceholderText(
            "Ej: 38 años, corro 4 veces por semana y hago bici el finde. Objetivo: media "
            "maratón sub 1:50 en marzo. Tengo poco tiempo los martes.")

        link = QLabel('<a href="https://console.anthropic.com/">Obtener una API key '
                      '(console.anthropic.com)</a>')
        link.setOpenExternalLinks(True)
        nota = QLabel(f"Se guarda en {gc.CONFIG_PATH} con permisos 600.")
        nota.setStyleSheet("color: gray;")

        f = QFormLayout(self)
        f.addRow("API key:", fila_key)
        f.addRow("", link)
        f.addRow("Modelo:", self.modelo)
        f.addRow("Datos detallados:", self.dias)
        f.addRow("Perfil y objetivos:", self.perfil)
        f.addRow(nota)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.guardar)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def guardar(self):
        cfg = gc.cargar_config()
        cfg.update(api_key=self.key.text().strip(), modelo=self.modelo.currentText().strip(),
                   dias_contexto=self.dias.value(), perfil=self.perfil.toPlainText().strip())
        gc.guardar_config(cfg)
        self.accept()


class CoachWorker(QThread):
    fragmento = Signal(str)
    terminado = Signal()
    error = Signal(str, bool)

    def __init__(self, coach, texto):
        super().__init__()
        self.coach, self.texto, self._cancelar = coach, texto, False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        gen = None
        try:
            gen = self.coach.preguntar(self.texto)
            for t in gen:
                if self._cancelar:
                    break
                self.fragmento.emit(t)
        except gc.SinClave as e:
            self.error.emit(str(e), True)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}", False)
        finally:
            if gen is not None:
                gen.close()
            self.terminado.emit()


class CoachTab(QWidget):
    mensaje = Signal(str)

    def __init__(self):
        super().__init__()
        self.coach = gc.Coach()
        self.worker = None
        self.en_curso = None

        atajos = QHBoxLayout()
        self.botones = []
        for texto, prompt in ATAJOS:
            b = QPushButton(texto)
            b.clicked.connect(lambda _=False, p=prompt: self.enviar(p))
            atajos.addWidget(b)
            self.botones.append(b)
        atajos.addStretch()
        for texto, fn in [("Nueva conversación", self.nueva), ("Guardar", self.guardar),
                          ("Informes", self.abrir_informes), ("⚙ Configurar", self.configurar)]:
            b = QPushButton(texto)
            b.clicked.connect(fn)
            atajos.addWidget(b)

        self.vista = QTextBrowser()
        self.vista.setOpenExternalLinks(True)
        self.vista.setStyleSheet("QTextBrowser { font-size: 14px; padding: 8px; }")

        self.entrada = QLineEdit()
        self.entrada.setPlaceholderText(
            "Preguntá lo que quieras sobre tus datos… (Enter para enviar)")
        self.entrada.returnPressed.connect(lambda: self.enviar(self.entrada.text()))
        self.btn_enviar = QPushButton("Enviar")
        self.btn_enviar.clicked.connect(self._enviar_o_detener)
        abajo = QHBoxLayout()
        abajo.addWidget(self.entrada)
        abajo.addWidget(self.btn_enviar)

        self.estado = QLabel()
        self.estado.setStyleSheet("color: gray;")

        v = QVBoxLayout(self)
        v.addLayout(atajos)
        v.addWidget(self.vista, 1)
        v.addLayout(abajo)
        v.addWidget(self.estado)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        self._bienvenida()

    # ------------------------------------------------------------ vista
    def _bienvenida(self):
        cfg = self.coach.cfg
        if not cfg.get("api_key"):
            self.vista.setMarkdown(
                "### Coach\n\nPara empezar, tocá **⚙ Configurar** y cargá tu API key de "
                "Anthropic. También podés escribir tu perfil y objetivos: mejora mucho las "
                "recomendaciones.")
        else:
            self.vista.setMarkdown(
                "### Coach\n\nElegí un atajo o escribí una pregunta. En el primer mensaje se "
                f"envían tus datos de los últimos {cfg['dias_contexto']} días, el volumen "
                "semanal y las tendencias de 90 días. **Sincronizá antes** si hace rato que "
                "no lo hacés.")
        self.estado.setText(f"Modelo: {cfg['modelo']}")

    def _render(self):
        self.vista.setMarkdown(self.coach.markdown(self.en_curso))
        sb = self.vista.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------ acciones
    def _ocupado(self, si: bool):
        for b in self.botones:
            b.setEnabled(not si)
        self.entrada.setEnabled(not si)
        self.btn_enviar.setText("Detener" if si else "Enviar")

    def _enviar_o_detener(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancelar()
        else:
            self.enviar(self.entrada.text())

    def enviar(self, texto: str):
        texto = texto.strip()
        if not texto or (self.worker and self.worker.isRunning()):
            return
        self.entrada.clear()
        self.en_curso = ""
        # muestra la pregunta de inmediato
        self.vista.setMarkdown(self.coach.markdown() + ("\n\n---\n\n" if self.coach.transcripcion
                                                        else "") + f"**🧑 Vos:** {texto}\n\n…")
        self.estado.setText("Preparando datos y consultando a Claude…")
        self._ocupado(True)
        self.worker = CoachWorker(self.coach, texto)
        self.worker.fragmento.connect(self._fragmento)
        self.worker.error.connect(self._error)
        self.worker.terminado.connect(self._fin)
        self.worker.start()

    def _fragmento(self, t):
        self.en_curso += t
        if not self._timer.isActive():
            self._timer.start()

    def _fin(self):
        self._timer.stop()
        self.en_curso = None
        self._ocupado(False)
        self._render()
        self.estado.setText(f"Modelo: {self.coach.cfg['modelo']} · "
                            f"{len(self.coach.transcripcion) // 2} intercambios")
        self.entrada.setFocus()

    def _error(self, msg, falta_clave):
        if falta_clave:
            self.configurar()
        else:
            QMessageBox.warning(self, "Coach", f"No se pudo consultar a Claude:\n{msg}")

    def nueva(self):
        if self.worker and self.worker.isRunning():
            return
        self.coach.reiniciar()
        self._bienvenida()

    def guardar(self):
        ruta = self.coach.guardar()
        self.mensaje.emit(f"Guardado: {ruta}" if ruta else "No hay conversación para guardar.")

    def abrir_informes(self):
        gc.INFORMES_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(gc.INFORMES_DIR)))

    def configurar(self):
        if ConfigCoachDialog(self).exec():
            self.coach.recargar_config()
            if not self.coach.transcripcion:
                self._bienvenida()
            self.estado.setText(f"Modelo: {self.coach.cfg['modelo']}")
