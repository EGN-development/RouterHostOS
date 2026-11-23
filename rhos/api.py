"""HTTP API and minimal management page."""

from __future__ import annotations

import os
import hmac
import html
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

from . import __version__
from .config import ConfigError, Conflict, Store
from .auth import AuthError, AuthStore
from . import linux


class ConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]


class CommitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    apply: bool = False


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int
    expected_revision: int


class BootstrapBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str


def hmac_compare(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def create_app(state_dir: str | Path | None = None) -> FastAPI:
    store = Store(state_dir or os.getenv("RHOS_STATE_DIR", "/var/lib/rhos"))
    auth = AuthStore(state_dir or os.getenv("RHOS_STATE_DIR", "/var/lib/rhos"))
    app = FastAPI(title="RouterHost OS API", version=__version__)

    def current_admin(request: Request, csrf: bool = False):
        session = auth.read_session(request.cookies.get("rhos_session"))
        if session is None:
            raise HTTPException(status_code=401, detail="administrator login required")
        if request.url.scheme != "https" and request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(status_code=400, detail="authenticated management requires HTTPS")
        if csrf and not hmac_compare(request.headers.get("X-CSRF-Token", ""), session["csrf"]):
            raise HTTPException(status_code=403, detail="invalid CSRF token")
        return session

    def require_local(request: Request):
        address = request.client.host if request.client else ""
        if address not in ("127.0.0.1", "::1", "testclient"):
            raise HTTPException(status_code=403, detail="initial setup is available only from localhost")

    def call(action):
        try:
            return action()
        except Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        if not auth.initialized():
            return HTMLResponse("""<!doctype html><meta charset="utf-8"><title>RHOS setup required</title><h1>RouterHost OS first setup</h1><p>Create the administrator from a local terminal with <code>python -m rhos.admin create</code>, then sign in.</p>""", status_code=503)
        raise HTTPException(status_code=401, detail="administrator login required. Use /login.")

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        if not auth.initialized():
            return HTMLResponse("""<!doctype html><meta charset="utf-8"><title>RHOS setup required</title><h1>RouterHost OS first setup</h1><p>Create the administrator from a local terminal with <code>python -m rhos.admin create</code>.</p>""", status_code=503)
        return HTMLResponse("""<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>RHOS login</title><style>body{background:#0b1020;color:#e7edf8;font:16px system-ui;display:grid;place-items:center;height:100vh}form{background:#121c30;padding:28px;border-radius:12px;min-width:280px}input,button{display:block;width:100%;box-sizing:border-box;padding:12px;margin:10px 0}button{background:#5c8cff;color:white;border:0;border-radius:6px}</style><form id="login"><h1>RouterHost OS</h1><label>Administrator<input name="username" autocomplete="username" required></label><label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button><p id="error" role="alert"></p></form><script>document.querySelector('form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const r=await fetch('/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:f.get('username'),password:f.get('password')})});if(!r.ok){document.querySelector('#error').textContent='Sign in failed';return}sessionStorage.setItem('rhos_csrf',(await r.json()).csrf_token);location.href='/dashboard'}</script></html>""")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(request: Request):
        current_admin(request)
        snapshot = store.snapshot()
        hostname = html.escape(snapshot["config"]["system"]["hostname"])
        return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>RouterHost OS</title><style>body{{margin:0;background:#0b1020;color:#e7edf8;font:15px system-ui}}nav{{background:#111a2d;padding:18px 6vw;color:#91b4ff;font-size:20px}}main{{max-width:1150px;margin:36px auto;padding:0 20px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}}section{{background:#121c30;border:1px solid #26334b;border-radius:12px;padding:20px}}h1{{font-size:30px}}h2{{font-size:17px;color:#91b4ff}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;color:#b8c8e3}}small{{color:#8c9ab4}}</style></head><body><nav>RHOS <small>RouterHost OS · {__version__}</small></nav><main><h1>Router overview</h1><p>{hostname} · configuration revision {snapshot['revision']}</p><div class="grid"><section><h2>Interfaces</h2><pre id="interfaces">Loading…</pre></section><section><h2>Routes</h2><pre id="routes">Loading…</pre></section><section><h2>Configuration</h2><pre id="configuration">Loading…</pre></section></div></main><script>const h={{'X-CSRF-Token':sessionStorage.getItem('rhos_csrf')||''}};Promise.all(['/api/v1/interfaces','/api/v1/ip/routes','/api/v1/configuration'].map(async p=>[p,await fetch(p).then(r=>r.json())])).then(rows=>rows.forEach(([p,d])=>document.getElementById(p.endsWith('interfaces')?'interfaces':p.endsWith('routes')?'routes':'configuration').textContent=JSON.stringify(d,null,2))).catch(()=>location.href='/login');</script></body></html>"""

    @app.get("/api/v1/system")
    def system():
        import platform
        return {"name": "RouterHost OS", "version": __version__, "hostname": store.snapshot()["config"]["system"]["hostname"], "kernel": platform.release()}

    @app.get("/api/v1/auth/status")
    def auth_status():
        return {"initialized": auth.initialized()}

    @app.post("/api/v1/auth/bootstrap")
    def bootstrap(body: BootstrapBody, request: Request):
        require_local(request)
        try:
            auth.bootstrap(body.username, body.password)
        except AuthError as exc:
            raise HTTPException(status_code=409 if auth.initialized() else 422, detail=str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="administrator bootstrap failed") from exc
        return {"initialized": True}

    @app.post("/api/v1/auth/login")
    def login(body: LoginBody, response: Response):
        if not auth.initialized():
            raise HTTPException(status_code=503, detail="administrator setup is required")
        if not auth.verify(body.username, body.password):
            raise HTTPException(status_code=401, detail="invalid username or password")
        token, csrf = auth.create_session(body.username)
        response.set_cookie("rhos_session", token, httponly=True, secure=True, samesite="strict", max_age=3600, path="/")
        return {"csrf_token": csrf, "expires_in": 3600}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request, response: Response):
        current_admin(request, csrf=True)
        response.delete_cookie("rhos_session", path="/")
        return {"logged_out": True}

    @app.get("/api/v1/interfaces")
    def get_interfaces(request: Request):
        current_admin(request)
        try:
            return linux.interfaces()
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/interfaces/ethernet")
    def get_ethernet_interfaces(request: Request):
        current_admin(request)
        try:
            return [item for item in linux.interfaces() if item["kind"] == "ether"]
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/ip/addresses")
    def get_addresses(request: Request):
        current_admin(request)
        try:
            return linux.addresses()
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/ip/routes")
    def get_routes(request: Request):
        current_admin(request)
        try:
            return linux.routes()
        except (RuntimeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/v1/configuration")
    def get_configuration(request: Request):
        current_admin(request)
        return store.snapshot()

    @app.get("/api/v1/configuration/diff")
    def configuration_diff(request: Request):
        current_admin(request)
        return store.diff()

    @app.put("/api/v1/configuration")
    def put_configuration(body: ConfigBody, request: Request, if_match: str | None = Header(default=None)):
        current_admin(request, csrf=True)
        if if_match is None:
            raise HTTPException(status_code=428, detail="If-Match revision header is required")
        try:
            revision = int(if_match.strip('"'))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="If-Match must be an integer revision") from exc
        return call(lambda: store.replace_candidate(body.config, revision))

    @app.post("/api/v1/configuration/commit")
    def commit(body: CommitBody, request: Request):
        current_admin(request, csrf=True)
        return call(lambda: store.commit(body.revision, body.apply))

    @app.post("/api/v1/configuration/rollback")
    def rollback(body: RollbackBody, request: Request):
        current_admin(request, csrf=True)
        return call(lambda: store.rollback(body.revision, body.expected_revision))

    app.state.rhos_store = store
    return app
