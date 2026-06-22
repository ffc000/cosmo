# ══════════════════════════════════════════════════════════════════════════════
# MÓDULOS TRAINING — agregar en app.py
# Ubicación: después de los imports existentes, antes de los blueprints actuales
# ══════════════════════════════════════════════════════════════════════════════

# 1. Imports (agregar junto a los demás imports al tope del archivo)
from garmin_routes import garmin_bp
from training_routes import training_bp

# 2. Registro de blueprints (agregar luego de crear la instancia `app`)
app.register_blueprint(garmin_bp)
app.register_blueprint(training_bp)

# 3. Decorar las rutas con @login_required
# Las rutas /garmin y /training ya usan session internamente,
# pero para forzar login_required en el blueprint hay dos opciones:

# OPCIÓN A — Más simple: agregar before_request al blueprint
# (ya incluido en garmin_routes.py y training_routes.py si usás esta versión)

# OPCIÓN B — En app.py, agregar estas rutas wrapper:
@app.route("/garmin")
@login_required
def garmin_redirect():
    from flask import redirect, url_for
    return redirect(url_for('garmin.garmin_index'))

@app.route("/training")
@login_required
def training_redirect():
    from flask import redirect, url_for
    return redirect(url_for('training.training_index'))

# NOTA: Si usás la Opción B, eliminar las rutas /garmin y /training
# de los blueprints (comentar las líneas @garmin_bp.route("/garmin")
# y @training_bp.route("/training") en los archivos respectivos).

# ──────────────────────────────────────────────────────────────────────────────
# 4. Variables de entorno necesarias en el VPS (.env o systemd service):
#
#    GARMIN_USER=tu_email@garmin.com
#    GARMIN_PASS=tu_password_garmin
#
# Agregar al mismo lugar donde está ANTHROPIC_API_KEY.
# ──────────────────────────────────────────────────────────────────────────────

# 5. Agregar en el nav de dashboard.html y otros templates:
#
#    <div class="nav-item" onclick="location.href='/garmin'">
#      <span class="nav-icon">⌚</span><span class="nav-label">Garmin</span>
#    </div>
#    <div class="nav-item" onclick="location.href='/training'">
#      <span class="nav-icon">📅</span><span class="nav-label">Training</span>
#    </div>
#
# ──────────────────────────────────────────────────────────────────────────────

# 6. Instalar dependencia:
#    pip install garminconnect
#    (fitparse es opcional, para parsear archivos FIT locales en el futuro)
