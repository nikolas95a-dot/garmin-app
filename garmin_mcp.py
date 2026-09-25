#!/usr/bin/env python3
"""Servidor MCP local para Garmin Connect. Usa la misma base que garmin_app.py."""
from mcp.server.fastmcp import FastMCP

import garmin_core as core

mcp = FastMCP("garmin")


@mcp.tool()
def sincronizar(dias: int = 30, forzar: bool = False) -> dict:
    """Descarga de Garmin Connect los últimos `dias` (métricas diarias, actividades y peso)
    a la base local. Llamar antes de analizar si los datos pueden estar desactualizados."""
    return core.sincronizar(dias, forzar)


@mcp.tool()
def metricas_diarias(desde: str, hasta: str) -> list[dict]:
    """Métricas diarias entre dos fechas (YYYY-MM-DD): pasos, FC reposo, estrés,
    Body Battery, sueño, HRV, readiness, VO2max."""
    return core.metricas_diarias(desde, hasta)


@mcp.tool()
def actividades(desde: str, hasta: str, tipo: str | None = None) -> list[dict]:
    """Actividades entre dos fechas. `tipo` = typeKey de Garmin (running, cycling, ...)."""
    return core.actividades(desde, hasta, tipo)


@mcp.tool()
def tipos_actividad() -> list[str]:
    """Tipos de actividad presentes en la base, del más frecuente al menos."""
    return core.tipos_actividad()


@mcp.tool()
def detalle_actividad(activity_id: int) -> dict:
    """Parciales (splits) y tiempo en zonas de FC de una actividad (en vivo)."""
    return core.detalle_actividad(activity_id)


@mcp.tool()
def descargar_actividad(activity_id: int, formato: str = "fit") -> str:
    """Descarga el archivo de una actividad (fit, gpx, tcx, kml, csv). Devuelve la ruta."""
    return core.descargar_actividad(activity_id, formato)


@mcp.tool()
def tendencia(metrica: str, dias: int = 90, fuente: str = "diario",
              tipo_actividad: str | None = None, ventana: int = 7) -> dict:
    """Evolución de una métrica: media, desvío, pendiente semanal, veredicto
    (mejorando/empeorando/estable) y últimos `ventana` días vs los previos.
    fuente: diario | actividades | peso."""
    return core.tendencia(fuente, metrica, dias, tipo_actividad, ventana)


@mcp.tool()
def resumen_semanal(semanas: int = 8, tipo: str | None = None, por_tipo: bool = True) -> list[dict]:
    """Volumen de entrenamiento por semana: cantidad, km, minutos, carga, FC media ponderada."""
    return core.resumen_semanal(semanas, tipo, por_tipo)


@mcp.tool()
def consulta_sql(sql: str) -> list[dict]:
    """SELECT de solo lectura sobre la base (tablas: diario, actividades, peso). Máx 500 filas."""
    try:
        return core.consulta_sql(sql)
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    mcp.run()
