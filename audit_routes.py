import ast, re

# Rutas que el JS necesita
JS_ROUTES = {
    '/api/historial/completo': 'GET',
    '/api/vua/bpmn': 'POST',
    '/api/vua/config': 'GET',
    '/api/vua/config/<clave>': 'PUT',
    '/api/vua/config/<clave>/mejorar': 'POST',
    '/api/vua/consultas_frecuentes': 'GET',
    '/api/vua/correo': 'POST',
    '/api/vua/correos_rapidos': 'GET',
    '/api/vua/cronologia': 'GET',       # FALTA - cronologia lista
    '/api/vua/cronologia': 'POST',
    '/api/vua/cronologia/<id>': 'PUT',
    '/api/vua/cronologia/<id>': 'DELETE',
    '/api/vua/ejes': 'GET',             # FALTA
    '/api/vua/ejes/<id>': 'PUT',
    '/api/vua/ejes/<id>/mejorar': 'POST',
    '/api/vua/equipo': 'GET',           # FALTA?
    '/api/vua/equipo': 'POST',
    '/api/vua/equipo/<id>': 'PUT',
    '/api/vua/equipo/<id>': 'DELETE',
    '/api/vua/glosario': 'GET',         # FALTA?
    '/api/vua/glosario': 'POST',
    '/api/vua/glosario/<id>': 'PUT',
    '/api/vua/glosario/<id>': 'DELETE',
    '/api/vua/info/<clave>': 'GET',
    '/api/vua/minuta': 'POST',
    '/api/vua/minuta_ia': 'POST',
    '/api/vua/normativa': 'POST',
    '/api/vua/riesgos': 'GET',
    '/api/vua/riesgos/<id>': 'PUT',
}

# Leer app.py del servidor (simulado - usar el contenido real)
try:
    with open('/opt/sintia/app.py') as f:
        app_content = f.read()
    print("Leyendo /opt/sintia/app.py REAL")
except:
    print("No hay acceso al servidor - usando análisis local")
    exit(1)

# Extraer todas las rutas registradas
routes_in_app = re.findall(r"@app\.route\(['\"]([^'\"]+)['\"].*?methods=\[([^\]]+)\]", app_content)
routes_set = set()
for path, methods in routes_in_app:
    for m in re.findall(r"'([A-Z]+)'", methods):
        routes_set.add((path, m))

print(f"\nTotal rutas en app.py: {len(routes_set)}")
print(f"\nRutas VUA registradas:")
for path, method in sorted(r for r in routes_set if 'vua' in r[0]):
    print(f"  [{method}] {path}")

# Verificar qué falta
print(f"\nVERIFICACIÓN DE RUTAS CRÍTICAS:")
critical = [
    ('/api/vua/ejes', 'GET'),
    ('/api/vua/cronologia', 'GET'),
    ('/api/vua/equipo', 'GET'),
    ('/api/vua/glosario', 'GET'),
    ('/api/vua/config', 'GET'),
    ('/api/vua/riesgos', 'GET'),
    ('/api/vua/correos_rapidos', 'GET'),
    ('/api/vua/consultas_frecuentes', 'GET'),
    ('/api/vua/info/<clave>', 'GET'),
]
for path, method in critical:
    # Buscar variantes (con/sin tipos de parámetro)
    found = any(
        re.sub(r'<[^>]+>', '<x>', p) == re.sub(r'<[^>]+>', '<x>', path)
        and m == method
        for p, m in routes_set
    )
    print(f"  {'✓' if found else '✗ FALTA'} [{method}] {path}")

# Verificar sintaxis
try:
    ast.parse(app_content)
    print(f"\n✓ app.py sintaxis OK ({app_content.count(chr(10))} líneas)")
except SyntaxError as e:
    print(f"\n✗ SINTAXIS ERROR línea {e.lineno}: {e.msg}")
