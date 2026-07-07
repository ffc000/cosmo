/**
 * common.js — CosmoTools
 *
 * Interceptor de CSRF + red de seguridad para fetch sin .catch(). Antes este
 * bloque estaba copiado y pegado, idéntico, en las 8 plantillas HTML. Eso es
 * justamente lo que permitió que quedara desactualizado en algún archivo sin
 * que nadie lo notara — cada plantilla nueva partía de una copia manual en
 * vez de heredar un único comportamiento compartido.
 *
 * Nota: NO se incluyen aquí esc()/escHtml() — varían levemente entre
 * plantillas (p. ej. senasa.html tiene un esc() con un propósito distinto,
 * para escapar comillas dentro de atributos onclick, no para HTML). Unificar
 * eso a la fuerza podía introducir un bug sutil, así que cada plantilla sigue
 * definiendo su propia versión de escape de texto.
 *
 * Incluir con: <script src="/static/common.js"></script>
 * Requiere el meta csrf-token en el <head> (lo genera Jinja por request).
 */
(function () {
  "use strict";

  var meta = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.content : '';
  var originalFetch = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    var method = (init.method || 'GET').toUpperCase();
    if (method !== 'GET' && method !== 'HEAD') {
      init.headers = new Headers(init.headers || {});
      if (!init.headers.has('X-CSRFToken')) init.headers.set('X-CSRFToken', token);
    }
    return originalFetch(input, init);
  };

  // Red de seguridad: si un fetch en cualquier parte de la página falla y
  // nadie le puso .catch(), esto evita que la UI quede colgada en silencio
  // (ej. en "Cargando...") sin que el usuario se entere de que algo falló.
  window.addEventListener('unhandledrejection', function (e) {
    console.error('Error de red sin manejar:', e && e.reason);
    if (window.__avisoErrorRed) return;
    window.__avisoErrorRed = true;
    var b = document.createElement('div');
    b.textContent = 'Error de conexión con el servidor. Reintentá o recargá la página.';
    b.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);' +
      'background:#B91C1C;color:#fff;padding:.6rem 1.1rem;border-radius:8px;' +
      'font:600 .8rem sans-serif;z-index:99999;box-shadow:0 4px 16px rgba(0,0,0,.25)';
    document.body.appendChild(b);
    setTimeout(function () { b.remove(); window.__avisoErrorRed = false; }, 6000);
  });
})();
