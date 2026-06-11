import re, ast

with open('/opt/sintia/app.py') as f:
    content = f.read()

print(f"Líneas originales: {content.count(chr(10))}")

# Encontrar todas las definiciones de funciones con sus decoradores
# y quedarnos solo con la ÚLTIMA definición de cada endpoint+método

# Parsear bloques de función con sus decoradores
blocks = []
# Encontrar cada @app.route
pattern = re.compile(r'(@app\.route\([^\n]+\)\n(?:@[^\n]+\n)*def \w+\([^)]*\):.*?)(?=\n@app\.route|\n# ──|\Z)', re.DOTALL)

# Mejor approach: dividir en líneas y procesar
lines = content.split('\n')
func_starts = []  # (linea, nombre_funcion, ruta, metodo)

for i, line in enumerate(lines):
    if line.startswith('@app.route('):
        # Buscar el nombre de la función
        for j in range(i+1, min(i+5, len(lines))):
            m = re.match(r'def (\w+)', lines[j])
            if m:
                func_name = m.group(1)
                # Extraer ruta y método
                route_m = re.search(r"['\"]([^'\"]+)['\"]", line)
                method_m = re.search(r"methods=\[([^\]]+)\]", line)
                route = route_m.group(1) if route_m else ''
                methods = re.findall(r"'([A-Z]+)'|\"([A-Z]+)\"", method_m.group(1)) if method_m else []
                methods = [m[0] or m[1] for m in methods]
                func_starts.append((i, func_name, route, methods))
                break

# Encontrar duplicados de funciones (mismo nombre)
func_names = [f[1] for f in func_starts]
seen_names = {}
duplicates = []
for i, (lineno, name, route, methods) in enumerate(func_starts):
    if name in seen_names:
        duplicates.append((lineno, name, seen_names[name]))
        print(f"  DUPLICADO: def {name}() en líneas {seen_names[name]+1} y {lineno+1}")
    else:
        seen_names[name] = lineno

print(f"\nTotal duplicados: {len(duplicates)}")

# Estrategia: para cada función duplicada, eliminar la PRIMERA ocurrencia
# manteniendo la más reciente (que tiene las mejoras)
if duplicates:
    # Marcar líneas a eliminar
    lines_to_remove = set()
    
    for dup_lineno, dup_name, first_lineno in duplicates:
        # Encontrar el bloque completo de la PRIMERA definición
        # (desde los decoradores hasta el inicio de la siguiente función o bloque)
        block_start = first_lineno
        # Retroceder para incluir decoradores
        while block_start > 0 and (lines[block_start-1].startswith('@') or lines[block_start-1].strip() == ''):
            block_start -= 1
        
        # Avanzar hasta encontrar el final del bloque
        block_end = first_lineno + 1
        while block_end < len(lines):
            line = lines[block_end]
            # El bloque termina cuando encontramos otro decorador @app.route o def al nivel 0
            if line.startswith('@app.route') or (line.startswith('def ') and block_end > first_lineno + 2):
                break
            if line.startswith('# ──') and block_end > first_lineno + 3:
                break
            block_end += 1
        
        print(f"  Eliminando {dup_name}: líneas {block_start+1}-{block_end}")
        for l in range(block_start, block_end):
            lines_to_remove.add(l)
    
    # Reconstruir sin las líneas marcadas
    new_lines = [l for i, l in enumerate(lines) if i not in lines_to_remove]
    new_content = '\n'.join(new_lines)
    
    # Verificar sintaxis
    try:
        ast.parse(new_content)
        print(f"\nOK - sintaxis válida ({new_content.count(chr(10))} líneas)")
        with open('/opt/sintia/app.py', 'w') as f:
            f.write(new_content)
        print("app.py actualizado")
    except SyntaxError as e:
        print(f"\nERROR sintaxis en línea {e.lineno}: {e.msg}")
        print("No se guardaron cambios")
else:
    print("No hay duplicados que eliminar")
