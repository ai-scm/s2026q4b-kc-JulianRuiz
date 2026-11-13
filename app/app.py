"""
App dummy "Hola Mundo" protegida con Keycloak.
Protocolo: OpenID Connect, flujo Authorization Code con PKCE.
"""
import logging
import os
import sys
from urllib.parse import urlencode

from authlib.integrations.flask_client import OAuth
from dotenv import find_dotenv, load_dotenv
from flask import Flask, redirect, render_template_string, request, session, url_for

# 1) CONFIGURACIÓN -----------------------------------------------------------
# En local se carga el .env de la raíz. En producción no existe .env:
# la plataforma define las variables de entorno (Twelve-Factor III).
load_dotenv(find_dotenv())


def env(name, default=None):
    """Lee una variable de entorno. Si es obligatoria y falta, la app no arranca."""
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"Falta la variable de entorno obligatoria: {name}")
    return value


KEYCLOAK_URL = env("KEYCLOAK_URL").rstrip("/")
KEYCLOAK_REALM = env("KEYCLOAK_REALM")
CLIENT_ID = env("OIDC_CLIENT_ID")
CLIENT_SECRET = env("OIDC_CLIENT_SECRET")
APP_BASE_URL = env("APP_BASE_URL").rstrip("/")
APP_HOST = env("APP_HOST", "127.0.0.1")
APP_PORT = int(env("APP_PORT", "5000"))
ISSUER = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"

# 2) LOGS A STDOUT (Twelve-Factor XI) ----------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("hola-mundo")

# 3) FLASK Y SESIÓN ----------------------------------------------------------
app = Flask(__name__)
app.secret_key = env("APP_SECRET_KEY")  # firma la cookie de sesión
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,   # JavaScript no puede leer la cookie
    SESSION_COOKIE_SAMESITE="Lax",  # protege contra CSRF y permite el retorno del login
)

# 4) CLIENTE OPENID CONNECT --------------------------------------------------
oauth = OAuth(app)
oauth.register(
    name="keycloak",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    # Descubrimiento: de esta URL Authlib obtiene los endpoints y las llaves públicas
    server_metadata_url=f"{ISSUER}/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid profile email",
        "code_challenge_method": "S256",  # PKCE
    },
)

PAGE = """
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Hola Mundo - Keycloak</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 4rem auto; padding: 0 1rem; line-height: 1.5; }
  a.btn { display: inline-block; padding: .6rem 1rem; border: 1px solid #333; border-radius: 6px;
          text-decoration: none; color: #111; margin: 0 .5rem .5rem 0; }
  pre { background: #f4f4f4; padding: 1rem; border-radius: 6px; overflow: auto; font-size: .85rem; }
</style>
</head>
<body>
{% if user %}
  <h1>Hola Mundo, {{ name }}</h1>
  <p>Iniciaste sesión a través de Keycloak (realm <b>{{ realm }}</b>).</p>
  {% if roles %}<p>Roles: {{ roles | join(", ") }}</p>{% endif %}
  <details>
    <summary>Ver los claims del ID token</summary>
    <pre>{{ user | tojson(indent=2) }}</pre>
  </details>
  <p><a class="btn" href="{{ url_for('logout') }}">Cerrar sesión</a></p>
{% else %}
  <h1>Hola Mundo</h1>
  <p>No has iniciado sesión.</p>
  <a class="btn" href="{{ url_for('login') }}">Iniciar sesión con Keycloak</a>
  <a class="btn" href="{{ url_for('login', idp='google') }}">Entrar directo con Google</a>
{% endif %}
</body>
</html>
"""


# 5) RUTAS -------------------------------------------------------------------
@app.route("/")
def index():
    user = session.get("user")
    name, roles = None, []
    if user:
        name = user.get("name") or user.get("preferred_username") or user.get("email")
        roles = [
            r for r in user.get("realm_access", {}).get("roles", [])
            if not r.startswith("default-roles") and r not in ("offline_access", "uma_authorization")
        ]
    return render_template_string(PAGE, user=user, name=name, roles=roles, realm=KEYCLOAK_REALM)


@app.route("/login")
def login():
    """Paso 1 del flujo: enviar el navegador a Keycloak."""
    redirect_uri = f"{APP_BASE_URL}/callback"
    extra = {}
    if request.args.get("idp") == "google":
        # kc_idp_hint le dice a Keycloak que salte su pantalla y vaya directo a Google
        extra["kc_idp_hint"] = "google"
    return oauth.keycloak.authorize_redirect(redirect_uri, **extra)


@app.route("/callback")
def callback():
    """Paso 2: Keycloak vuelve con ?code=... y la app lo cambia por tokens."""
    # Authlib comprueba el state, envía el code + client_secret + code_verifier a Keycloak
    # y valida el ID token: firma, issuer, audiencia, expiración y nonce.
    token = oauth.keycloak.authorize_access_token()
    user = token.get("userinfo") or {}
    session["user"] = dict(user)
    session["id_token"] = token.get("id_token")  # se necesita para cerrar sesión en Keycloak
    log.info("Login correcto: %s", user.get("preferred_username"))
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    """Cierra la sesión de la app y también la de Keycloak."""
    id_token = session.get("id_token")
    session.clear()
    metadata = oauth.keycloak.load_server_metadata()
    params = {"post_logout_redirect_uri": f"{APP_BASE_URL}/", "client_id": CLIENT_ID}
    if id_token:
        params["id_token_hint"] = id_token
    return redirect(f"{metadata['end_session_endpoint']}?{urlencode(params)}")


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


# 6) ARRANQUE (Twelve-Factor VII: la app expone su propio puerto) -------------
if __name__ == "__main__":
    log.info("App en %s  |  issuer: %s", APP_BASE_URL, ISSUER)
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
