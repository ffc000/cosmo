"""
_rfixwis_datos.py — Tablas de referencia (Aduana↔DIRA, feriados, códigos de
plan de barrido) para el reporte de stock. Se cargan de la base UNA SOLA VEZ,
al importarse este módulo (ARR_DRA, ARR_ADU, FERIADOS, ADU_IDX quedan fijos
para toda la corrida) — igual que en el _RFIXWIS.py original: si ref_dira o
ref_aduanas no existen o están vacías, esto lanza RefAduanasNoDisponibleError
apenas se importa, no recién cuando se llama a procesar().
Extraído de _RFIXWIS.py (Fase 3 de profesionalización, igual que generar.py).
"""
import sqlite3 as _sq3_ref

class RefAduanasNoDisponibleError(Exception):
    """Las tablas ref_dira / ref_aduanas no existen o están vacías en la BD.
    No hay fallback hardcodeado: se administran desde el panel de administración
    (Ref. Aduanas / DIRA)."""
    pass

def _cargar_ref_dira_desde_bd():
    """Carga ARR_DRA (dict indice -> nombre) desde la tabla ref_dira en HIST_DB."""
    try:
        con = _sq3_ref.connect("/data/historial.db")
        con.row_factory = _sq3_ref.Row
        existe = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ref_dira'"
        ).fetchone()
        if not existe:
            con.close()
            raise RefAduanasNoDisponibleError(
                "La tabla ref_dira no existe en la base de datos. "
                "Verificá que init_historial() se haya ejecutado en app.py."
            )
        rows = con.execute("SELECT indice, nombre FROM ref_dira ORDER BY orden, indice").fetchall()
        con.close()
        if not rows:
            raise RefAduanasNoDisponibleError(
                "La tabla ref_dira está vacía. Cargá las direcciones regionales desde el "
                "panel de administración (Ref. Aduanas / DIRA) antes de generar el reporte."
            )
        return {r["indice"]: r["nombre"] for r in rows}
    except RefAduanasNoDisponibleError:
        raise
    except Exception as _e:
        raise RefAduanasNoDisponibleError(f"No se pudo leer ref_dira desde la base de datos: {_e}")

def _cargar_ref_aduanas_desde_bd():
    """Carga ARR_ADU desde la tabla ref_aduanas en HIST_DB (única fuente de verdad)."""
    try:
        con = _sq3_ref.connect("/data/historial.db")
        con.row_factory = _sq3_ref.Row
        existe = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ref_aduanas'"
        ).fetchone()
        if not existe:
            con.close()
            raise RefAduanasNoDisponibleError(
                "La tabla ref_aduanas no existe en la base de datos. "
                "Verificá que init_historial() se haya ejecutado en app.py."
            )
        rows = con.execute("SELECT cod, nombre, indice_dira FROM ref_aduanas").fetchall()
        con.close()
        if not rows:
            raise RefAduanasNoDisponibleError(
                "La tabla ref_aduanas está vacía. Cargá los datos desde el panel "
                "de administración (Ref. Aduanas / DIRA) antes de generar el reporte."
            )
        return [[r["cod"], r["nombre"], r["indice_dira"]] for r in rows]
    except RefAduanasNoDisponibleError:
        raise
    except Exception as _e:
        raise RefAduanasNoDisponibleError(f"No se pudo leer ref_aduanas desde la base de datos: {_e}")

ARR_DRA = _cargar_ref_dira_desde_bd()

ARR_ADU = _cargar_ref_aduanas_desde_bd()

def _cargar_feriados_desde_bd():
    """Carga el set de feriados (YYMMDD como int) desde la tabla `feriados` en
    HIST_DB. A diferencia de ref_dira/ref_aduanas, si la tabla está vacía NO se
    aborta la generación del reporte: se sigue generando, simplemente sin marcar
    ningún día como feriado (solo sábados/domingos, que se calculan aparte).
    Sábados y domingos NO se guardan acá — se derivan de la fecha en es_no_habil()."""
    try:
        con = _sq3_ref.connect("/data/historial.db")
        existe = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feriados'"
        ).fetchone()
        if not existe:
            con.close()
            return set()
        rows = con.execute("SELECT fecha FROM feriados").fetchall()
        con.close()
        # fecha viene como 'YYYY-MM-DD' -> convertir a int YYMMDD para matchear
        # el formato que usa el resto del módulo (yymmdd_to_date, arr15, etc.)
        out = set()
        for (fecha,) in rows:
            try:
                y, m, d = fecha.split('-')
                out.add(int(y[2:4] + m + d))
            except Exception:
                continue
        return out
    except Exception:
        return set()

FERIADOS = _cargar_feriados_desde_bd()

ARR_BARRIDO = {'0011100Q','0011104E','00111028','0011109B','0011205D'}

def _adu_index():
    return {a[0]: a for a in ARR_ADU}

ADU_IDX = _adu_index()

def nombre_adu_y_dira(cod3: str):
    a = ADU_IDX.get(cod3)
    if not a:
        return ('N/E', 'N/E')
    return (a[1], ARR_DRA.get(a[2], 'N/E'))

def adu_vs_dira(cod3: str, dira_idx: str) -> bool:
    if dira_idx == '0':
        return True
    a = ADU_IDX.get(cod3)
    return bool(a and a[2] == dira_idx)
