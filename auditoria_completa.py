import ast, re, json, sqlite3, sys

print("=" * 60)
print("AUDITORÍA COMPLETA DEL SERVIDOR")
print("=" * 60)

# ── 1. Leer app.py ────────────────────────────────────────────────────────────
try:
    with open('/opt/sintia/app.py') as f:
        content = f.read()
    lines = content.split('\n')
    print(f"\n1. app.py: {len(lines)} líneas")
except Exception as e:
    print(f"ERROR leyendo app.py: {e}"); sys.exit(1)

# ── 2. Sintaxis ───────────────────────────────────────────────────────────────
try:
    ast.parse(content)
    print("   Sintaxis: OK")
except SyntaxError as e:
    print(f"   SINTAXIS ERROR línea {e.lineno}: {e.msg}")
    sys.exit(1)

# ── 3. Rutas VUA registradas ──────────────────────────────────────────────────
print("\n2. RUTAS VUA en app.py:")
# Extraer pares (ruta, metodos, funcion)
route_blocks = re.findall(
    r'@app\.route\(["\']([^"\']+)["\'][^)]*\)\s*\n'
    r'(?:@\w+(?:\([^)]*\))?\s*\n)*'
    r'def (\w+)',
    content
)
vua_routes = [(r, f) for r, f in route_blocks if 'vua' in r]
for route, fn in sorted(vua_routes):
    # Buscar el método
    idx = content.find(f'@app.route("{route}")')
    if idx == -1:
        idx = content.find(f"@app.route('{route}')")
    methods = re.search(r"methods=\[([^\]]+)\]", content[idx:idx+100])
    m = methods.group(1) if methods else "?"
    print(f"   {m:25} {route:45} -> {fn}")

# Rutas críticas GET
print("\n3. VERIFICACIÓN RUTAS GET CRÍTICAS:")
critical_gets = [
    '/api/vua/ejes',
    '/api/vua/cronologia',
    '/api/vua/equipo',
    '/api/vua/glosario',
    '/api/vua/config',
    '/api/vua/riesgos',
    '/api/vua/correos_rapidos',
    '/api/vua/consultas_frecuentes',
    '/api/vua/info/<clave>',
]
for route in critical_gets:
    # Buscar la ruta con GET
    pattern = re.compile(
        r"@app\.route\(['\"]" + re.escape(route) + r"['\"].*?methods=\[([^\]]+)\]",
        re.DOTALL
    )
    matches = pattern.findall(content)
    has_get = any('GET' in m for m in matches)
    print(f"   {'✓' if has_get else '✗ FALTA GET'} {route}")

# ── 4. BD - tablas y filas ─────────────────────────────────────────────────────
print("\n4. BASE DE DATOS /data/historial.db:")
try:
    con = sqlite3.connect('/data/historial.db')
    tablas_vua = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'vua_%'"
    ).fetchall()]
    for t in sorted(tablas_vua):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})").fetchall()]
        count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"   {t}: {count} filas, cols={cols}")
    con.close()
except Exception as e:
    print(f"   ERROR: {e}")

# ── 5. IDs de vua_ejes ─────────────────────────────────────────────────────────
print("\n5. IDs en vua_ejes:")
try:
    con = sqlite3.connect('/data/historial.db')
    rows = con.execute("SELECT id, nombre, LENGTH(descripcion) as dlen FROM vua_ejes ORDER BY orden").fetchall()
    for r in rows:
        print(f"   id={repr(r[0])} type={type(r[0]).__name__} nombre={r[1][:40]} desc={r[2]} chars")
    con.close()
except Exception as e:
    print(f"   ERROR: {e}")

# ── 6. Verificar función vua_ejes_list completa ───────────────────────────────
print("\n6. FUNCIÓN vua_ejes_list:")
idx = content.find('def vua_ejes_list()')
if idx == -1:
    print("   ✗ FUNCIÓN NO ENCONTRADA")
else:
    # Buscar decorador
    pre = content[max(0, idx-200):idx]
    route_match = re.search(r"@app\.route\(['\"][^'\"]+['\"][^)]*\)", pre)
    login_match = '@login_required' in pre
    print(f"   Encontrada en pos {idx}")
    print(f"   @app.route: {route_match.group() if route_match else 'NO ENCONTRADO'}")
    print(f"   @login_required: {login_match}")
    # Ver el body
    body = content[idx:idx+300]
    print(f"   Body: {body[:200]}")

# ── 7. Verificar función vua_config_list completa ────────────────────────────
print("\n7. FUNCIÓN vua_config_list:")
idx = content.find('def vua_config_list()')
if idx == -1:
    print("   ✗ FUNCIÓN NO ENCONTRADA")
else:
    pre = content[max(0, idx-200):idx]
    route_match = re.search(r"@app\.route\(['\"][^'\"]+['\"][^)]*\)", pre)
    print(f"   @app.route: {route_match.group() if route_match else 'NO ENCONTRADO'}")
    print(f"   @login_required: {'@login_required' in pre}")

# ── 8. Buscar errores de endpoint duplicado ───────────────────────────────────
print("\n8. ENDPOINTS DUPLICADOS:")
all_routes = re.findall(r"@app\.route\(['\"]([^'\"]+)['\"].*?methods=\[([^\]]+)\]", content)
seen = {}
for path, methods in all_routes:
    for m in re.findall(r"'([A-Z]+)'|\"([A-Z]+)\"", methods):
        method = m[0] or m[1]
        key = f"{method}:{path}"
        if key in seen:
            print(f"   ✗ DUPLICADO: {key}")
        seen[key] = True
if all(True for k in seen): 
    dups = [k for k in seen if list(seen.keys()).count(k) > 1]
    if not dups:
        print("   ✓ Sin duplicados")

print("\n" + "=" * 60)
print("FIN AUDITORÍA")
print("=" * 60)
