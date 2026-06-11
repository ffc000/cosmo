# Script para volcar el app.py actual del servidor
import base64

with open('/opt/sintia/app.py', 'rb') as f:
    data = f.read()

# Guardar en chunks para poder leerlo
print(f"TAMAÑO: {len(data)} bytes, {data.count(b'\n')} líneas")

# Escribir en /tmp para poder descargarlo
with open('/tmp/app_dump.py', 'wb') as f:
    f.write(data)
print("Guardado en /tmp/app_dump.py")

# Mostrar las primeras 50 líneas (config e imports)
lines = data.decode('utf-8').split('\n')
print("\n=== PRIMERAS 50 LÍNEAS ===")
for i, l in enumerate(lines[:50]):
    print(f"{i+1:4}: {l}")
