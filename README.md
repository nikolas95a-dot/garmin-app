# Garmin Progreso (app de escritorio + servidor MCP)

```
garmin_core.py    núcleo: login, SQLite local, sincronización, análisis
garmin_app.py     app de escritorio (PySide6 + pyqtgraph)
garmin_mcp.py     servidor MCP para Claude Code / Claude Desktop
garmin_login.py   login por terminal (opcional; la app tiene su propio login)
```

La app y el servidor MCP comparten la misma base: `~/garmin_data/garmin.db`.

## Instalación (Ubuntu)

```bash
mkdir -p ~/garmin_mcp && cd ~/garmin_mcp      # copiar acá los 4 .py
python3 -m venv .venv
.venv/bin/pip install -U -r requirements.txt
.venv/bin/python garmin_app.py
```

Requiere Python 3.12+. Al abrir por primera vez pide email, contraseña y MFA;
guarda solo los tokens en `~/.garminconnect`.

Si Qt se queja de "xcb" en Ubuntu: `sudo apt install libxcb-cursor0`.

## La app

- **Panel**: tarjetas con el último valor y la variación de los últimos 7 días contra las
  4 semanas previas (verde = mejora, rojo = empeora, gris = neutro o cambio menor a 1 %).
  Debajo, FC en reposo, HRV y peso de los últimos 90 días. Clic en una tarjeta abre su tendencia.
- **Tendencias**: cualquier métrica diaria, de actividades (filtrable por tipo) o de peso;
  puntos, media móvil configurable, recta de regresión y veredicto
  (mejorando / empeorando / estable: el cambio en el período se compara con medio desvío).
- **Actividades**: tabla ordenable; doble clic muestra parciales y zonas de FC; descarga FIT/GPX.
- **Semanal**: volumen por semana (km, minutos, carga, cantidad) y ratio de la última semana
  completa contra el promedio de las 4 anteriores.

Primera sincronización: conviene hacerla por tramos (ej. 90 días, después 365 con los
días ya guardados salteados) para no disparar límites 429 de Garmin.

## Servidor MCP

Claude Code (Linux):
```bash
claude mcp add garmin --scope user -- ~/garmin_mcp/.venv/bin/python ~/garmin_mcp/garmin_mcp.py
```

Claude Desktop (Windows), en `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{ "mcpServers": { "garmin": {
    "command": "C:\\ruta\\garmin_mcp\\.venv\\Scripts\\python.exe",
    "args": ["C:\\ruta\\garmin_mcp\\garmin_mcp.py"] } } }
```

## Notas

- API no oficial: si Garmin cambia la autenticación, `pip install -U garminconnect`
  y volver a iniciar sesión.
- La base guarda también el JSON crudo de cada día/actividad (columna `raw`) para poder
  extraer campos nuevos sin volver a descargar.

## Coach (Claude por API)

Pestaña **Coach**: atajos (análisis semanal, plan, ¿entreno fuerte hoy?, tendencias) y chat libre.

- Requiere una API key de https://console.anthropic.com (servicio aparte de claude.ai, pago por uso).
  Se carga en ⚙ Configurar y se guarda en `~/.config/garmin_progreso/config.json` (permisos 600),
  o por variable `ANTHROPIC_API_KEY`.
- En el primer mensaje de cada conversación se envía un resumen compacto (~4-6 k caracteres):
  métricas diarias de N días, actividades, volumen semanal y tendencias de 90 días.
  Los mensajes siguientes reutilizan el historial.
- "Perfil y objetivos" se agrega a las instrucciones: cuanto más concreto, mejores recomendaciones.
- Las conversaciones se guardan en `~/garmin_data/informes/` como Markdown.
