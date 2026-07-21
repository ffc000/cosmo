#!/usr/bin/env bash
#
# deploy.sh — corre por cron cada 2 minutos (*/2 * * * *). Si hay un commit
# nuevo en origin/master, hace git pull + reinicia sintia, y notifica por
# Telegram.
#
# Mejoras 20/07/2026 (revisión de arquitectura) sobre la versión anterior:
#   1. git fetch fallido ahora se detecta y notifica -- antes, si fetch
#      fallaba (red, GitHub caído, problema de SSH key), el script seguía
#      comparando contra un origin/master VIEJO (el de la última vez que
#      fetch funcionó), pudiendo creer "no hay cambios" sin haberse
#      enterado nunca de que sí los había. Sin ningún aviso.
#   2. Después de reiniciar, se verifica que el sitio responda de verdad
#      (curl a localhost) antes de avisar "Deploy OK" -- antes,
#      `systemctl restart` devolviendo éxito solo confirma que el comando
#      fue aceptado, no que la app haya levantado bien. Un deploy con un
#      error que se coló hasta acá podía reportarse como exitoso con el
#      sitio caído.
#   3. Lock file -- si un deploy tarda más de 2 minutos (poco común, pero
#      posible con un git pull lento), la corrida siguiente del cron ya no
#      se pisa con la anterior.
#
# Lo que NO cambia de la versión anterior: la lógica de detectar el commit
# nuevo, el pull, el restart, y las notificaciones de éxito/fallo del pull
# y del restart -- ver la nota de "TOKEN/CHAT_ID en texto plano" al final,
# eso quedó afuera de este cambio a propósito (es una decisión de
# infraestructura aparte, no un bug de robustez).

set -uo pipefail

TOKEN="8112521479:AAGotgBwBfR0LfSGnmujCftJH8Ozsb81AHU"
CHAT_ID="327905399"
LOCK="/tmp/deploy-sintia.lock"
HEALTHCHECK_URL="http://127.0.0.1:5000/"
HEALTHCHECK_TIMEOUT=15   # segundos -- gunicorn recién reiniciado puede tardar unos segundos en aceptar conexiones

cd /opt/sintia

notificar() {
  curl -sX POST "https://api.telegram.org/bot$TOKEN/sendMessage" \
    -d chat_id="$CHAT_ID" --data-urlencode "text=$1" > /dev/null
}

# ── Lock: si ya hay un deploy corriendo, salir sin hacer nada ──────────────
if [ -e "$LOCK" ]; then
  # Lock de más de 10 minutos se asume una corrida colgada (mismo criterio
  # que ya usamos en procesar_imports_pendientes.py) -- se pisa y se avisa,
  # en vez de bloquear deploys para siempre por una corrida que nunca
  # terminó de liberar el lock.
  EDAD=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
  if [ "$EDAD" -lt 600 ]; then
    exit 0
  fi
  notificar "⚠ deploy.sh: lock de $((EDAD/60)) min encontrado al arrancar -- se asume una corrida colgada, se pisa."
fi
trap 'rm -f "$LOCK"' EXIT
echo $$ > "$LOCK"

# ── Fetch, con manejo explícito de fallo ───────────────────────────────────
FETCH_OUT=$(git fetch origin master 2>&1)
if [ $? -ne 0 ]; then
  notificar "✗ Deploy: git fetch falló en sintia -- no se pudo chequear si hay commits nuevos.
Error:
$FETCH_OUT"
  exit 1
fi

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/master)

if [ "$LOCAL" != "$REMOTE" ]; then
  PULL_OUT=$(git pull origin master 2>&1)
  if [ $? -eq 0 ]; then
    RESTART_OUT=$(systemctl restart sintia 2>&1)
    if [ $? -eq 0 ]; then
      # Healthcheck real: reintenta un rato corto, porque gunicorn recién
      # reiniciado puede tardar unos segundos en aceptar conexiones -- no
      # alcanza con "el comando de restart no dio error".
      OK=0
      for i in $(seq 1 "$HEALTHCHECK_TIMEOUT"); do
        CODIGO=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 "$HEALTHCHECK_URL" 2>/dev/null)
        if [ "$CODIGO" = "200" ] || [ "$CODIGO" = "302" ]; then
          OK=1
          break
        fi
        sleep 1
      done

      if [ "$OK" = "1" ]; then
        HASH=$(git ls-files -z | xargs -0 md5sum | sort | md5sum | cut -d' ' -f1)
        notificar "✓ Deploy OK en sintia ($REMOTE)
MD5 archivos: $HASH"
      else
        LOG=$(journalctl -u sintia -n 25 --no-pager)
        notificar "✗ Deploy FALLÓ en sintia: el servicio reinició pero NO responde en $HEALTHCHECK_URL
después de esperar ${HEALTHCHECK_TIMEOUT}s (último código HTTP: ${CODIGO:-sin respuesta}).
Commit: $REMOTE
Log:
$LOG"
      fi
    else
      LOG=$(journalctl -u sintia -n 15 --no-pager)
      notificar "✗ Deploy FALLÓ en sintia: systemctl restart falló.
Commit: $REMOTE
Error: $RESTART_OUT
Log:
$LOG"
    fi
  else
    notificar "✗ Deploy FALLÓ en sintia: git pull falló.
Intentando commit: $REMOTE
Error:
$PULL_OUT"
  fi
fi
