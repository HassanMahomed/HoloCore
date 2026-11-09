"""Local, review-first browser wizard for first-run HoloCore setup."""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .engine import HoloCoreEngine
from .auto_update import configure as configure_auto_update
from .folder_layout import HomeLayout
from .home import HomeManager
from .install import detect_installed_clients
from .local_models import LocalModelManager
from .obsidian_plugin import install_plugin


CLIENT_NAMES = ("claude", "codex", "cursor", "gemini", "opencode")


def _resolved(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve()


def build_setup_state(*, home: str | Path | None = None, project: str | Path | None = None) -> dict[str, Any]:
    """Return read-only setup discovery for the browser wizard."""
    selected_home = HomeManager(home).home
    clients = detect_installed_clients()
    models = LocalModelManager(selected_home).discover()
    return {
        "home": str(selected_home),
        "project": str(_resolved(project)) if project else "",
        "clients": clients,
        "models": models,
        "defaults": {
            "clients": [name for name in CLIENT_NAMES if clients.get(name, {}).get("installed")],
            "build_code_map": True,
            "auto_update": False,
        },
    }


def build_setup_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a proposed setup and expose every important resolved path."""
    home_text = str(payload.get("home") or "").strip()
    if not home_text:
        raise ValueError("Choose a HoloCore Home folder.")
    home = _resolved(home_text)
    project_text = str(payload.get("project") or "").strip()
    project = _resolved(project_text) if project_text else None
    if home.exists() and not home.is_dir():
        raise ValueError("The HoloCore Home path is an existing file.")
    if project and project.exists() and not project.is_dir():
        raise ValueError("The project path is an existing file.")
    if project and (home == project or project in home.parents):
        raise ValueError("HoloCore Home cannot be inside the project folder.")
    clients = list(dict.fromkeys(str(item).casefold() for item in payload.get("clients", [])))
    unknown = [item for item in clients if item not in CLIENT_NAMES]
    if unknown:
        raise ValueError(f"Unsupported AI client: {unknown[0]}")
    if clients and project is None:
        raise ValueError("Choose a project folder before connecting AI tools.")
    model = str(payload.get("model") or "").strip() or None
    vaults = list(dict.fromkeys(str(_resolved(item)) for item in payload.get("obsidian_vaults", []) if str(item).strip()))
    for vault in vaults:
        if not (Path(vault) / ".obsidian").is_dir():
            raise ValueError(f"Not an Obsidian vault (no .obsidian folder): {vault}")
    layout = HomeLayout(home)
    paths: dict[str, str] = {
        "HoloCore Home": str(home),
        "Knowledge vault": str(layout.knowledge),
        "Projects": str(home / "Projects"),
        "Memories": str(layout.memories),
        "Imported sources": str(home / "Sources"),
        "AI connections": str(home / "Connections"),
        "Local model settings": str(home / "Runtime" / "local-model.json"),
    }
    if project:
        world_id = project.name
        paths.update({
            "Project folder": str(project),
            "Project knowledge": str(layout.project_knowledge(world_id)),
            "Project storage": str(home / "Projects" / world_id),
            "Code Map": str(home / "Projects" / world_id / "CodeMap" / "graph.json"),
        })
    actions = ["Create or select the HoloCore Home and its local folders"]
    if project:
        actions.append(f"Register project: {project.name}")
    if clients:
        actions.append("Connect detected AI tools: " + ", ".join(name.title() for name in clients))
    if project and bool(payload.get("build_code_map", True)):
        actions.append("Build the first project Code Map")
    if model:
        actions.append(f"Use local Ollama model: {model}")
    else:
        actions.append("Use deterministic extraction (no local model selected)")
    if bool(payload.get("auto_update", False)):
        actions.append("Enable one lightweight update check per active day")
    for vault in vaults:
        actions.append(f"Add the HoloCore Board plugin to Obsidian vault: {Path(vault).name}")
    return {
        "home": str(home), "project": str(project) if project else "", "clients": clients,
        "build_code_map": bool(payload.get("build_code_map", True)), "model": model,
        "auto_update": bool(payload.get("auto_update", False)), "obsidian_vaults": vaults,
        "paths": paths, "actions": actions,
    }


def _write_setup_journal(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _setup_operation_id(plan: dict[str, Any]) -> str:
    contract = {key: plan[key] for key in ("home", "project", "clients", "build_code_map", "model", "auto_update")}
    if plan.get("obsidian_vaults"):  # Only when chosen, so earlier setup journals still resume.
        contract["obsidian_vaults"] = plan["obsidian_vaults"]
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def apply_setup(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply or safely resume a reviewable setup plan using idempotent steps."""
    plan = build_setup_plan(payload)
    home = Path(plan["home"])
    journal_path = home / "Runtime" / "setup-operation.json"
    operation_id = _setup_operation_id(plan)
    existing: dict[str, Any] = {}
    if journal_path.is_file():
        try:
            existing = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Setup recovery journal is unreadable: {journal_path}: {exc}") from exc
    active = existing.get("state") in {"applying", "failed"}
    if active and existing.get("operation_id") != operation_id:
        raise RuntimeError(
            f"A different setup operation is incomplete. Retry its original choices first. Journal: {journal_path}"
        )
    resumed = bool(active)
    journal = existing if resumed else {
        "schema_version": 1,
        "operation_id": operation_id,
        "state": "planned",
        "plan": plan,
        "completed_steps": [],
        "results": {},
    }
    journal.update({"state": "applying", "current_step": None, "error": None})
    _write_setup_journal(journal_path, journal)

    def run_step(name: str, action):
        if name in journal["completed_steps"]:
            return journal["results"].get(name)
        journal.update({"current_step": name, "state": "applying", "error": None})
        _write_setup_journal(journal_path, journal)
        try:
            result = action()
        except Exception as exc:
            journal.update({"state": "failed", "error": str(exc), "current_step": name})
            _write_setup_journal(journal_path, journal)
            raise RuntimeError(
                f"Setup stopped during '{name}' and can be resumed with the same choices. Journal: {journal_path}"
            ) from exc
        journal["results"][name] = result
        journal["completed_steps"].append(name)
        _write_setup_journal(journal_path, journal)
        return result

    home_result = run_step("home", lambda: HomeManager(home).select_home(home, initialize=True))

    def configure_project():
        if not plan["project"]:
            return None
        engine = HoloCoreEngine(Path(plan["project"]))
        if plan["build_code_map"]:
            return engine.setup(git=False, platforms=plan["clients"], home=home)
        return engine.initialize(git=False, platforms=plan["clients"], home=home)

    project_result = run_step("project", configure_project)
    model_result = run_step(
        "local_model",
        lambda: LocalModelManager(home).configure("ollama", plan["model"]) if plan["model"] else None,
    )
    update_result = run_step(
        "auto_update", lambda: configure_auto_update(home, enabled=plan["auto_update"])
    )
    plugin_result = run_step("obsidian_plugin", lambda: [install_plugin(vault) for vault in plan["obsidian_vaults"]]) \
        if plan["obsidian_vaults"] else []
    journal.update({"state": "complete", "current_step": None, "error": None})
    _write_setup_journal(journal_path, journal)
    return {
        "applied": True, "plan": plan, "home": home_result,
        "project": project_result, "local_model": model_result, "auto_update": update_result,
        "obsidian_plugin": plugin_result,
        "resumed": resumed, "journal": str(journal_path),
        "next": "Open the selected AI tool in your project, then run `holocore status`.",
    }


def pick_folder(title: str) -> str | None:
    """Open the native Windows folder picker; typed paths remain the fallback."""
    if os.name != "nt":
        return None
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description={json.dumps(title)};"
        "$d.ShowNewFolderButton=$true;"
        "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){$d.SelectedPath}"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-STA", "-Command", script],
        capture_output=True, text=True, check=False,
    )
    value = completed.stdout.strip()
    return value or None


def render_setup_html(state: dict[str, Any], token: str) -> str:
    safe_state = json.dumps(state, ensure_ascii=False).replace("<", "\\u003c")
    safe_token = json.dumps(token)
    return r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Set up HoloCore</title><style>
:root{color-scheme:light;--ink:#171713;--muted:#69675f;--line:#d9d7cd;--paper:#faf9f5;--panel:#fff;--gold:#9b6b12;--soft:#f2eee3;--ok:#1e7145}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}main{width:min(780px,calc(100% - 32px));margin:48px auto 80px}header{margin-bottom:34px}.eyebrow{color:var(--gold);font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}h1{font-size:clamp(36px,7vw,64px);line-height:1;margin:10px 0 16px;letter-spacing:-.045em}h2{font-size:22px;margin:0 0 8px}p{margin:0;color:var(--muted)}section{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:24px;margin:16px 0;box-shadow:0 10px 30px #2c28180b}.field{margin-top:18px}label,.label{display:block;font-weight:700;margin-bottom:7px}input[type=text]{width:100%;height:46px;border:1px solid #aaa79d;border-radius:9px;padding:0 12px;font:inherit;background:#fff}.pathrow{display:grid;grid-template-columns:1fr auto;gap:8px}.btn,button{min-height:44px;border-radius:9px;border:1px solid var(--ink);padding:0 16px;background:#fff;color:var(--ink);font:700 14px system-ui;cursor:pointer}.primary{background:var(--ink);color:#fff}.btn:hover,button:hover{transform:translateY(-1px)}button:focus-visible,input:focus-visible{outline:3px solid #d9a63b;outline-offset:2px}.choices{display:grid;gap:8px;margin-top:16px}.choice{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line);border-radius:10px;padding:13px}.choice input{margin-top:5px}.choice small{display:block;color:var(--muted)}.badge{display:inline-block;border-radius:99px;background:var(--soft);padding:2px 8px;color:var(--muted);font-size:12px}.found{color:var(--ok)}select{width:100%;height:46px;border:1px solid #aaa79d;border-radius:9px;padding:0 10px;background:#fff;font:inherit}.review{display:grid;grid-template-columns:minmax(130px,.45fr) 1fr;gap:8px 18px;margin-top:16px}.review dt{font-weight:700}.review dd{margin:0;overflow-wrap:anywhere;color:var(--muted)}ul{color:var(--muted)}.actions{display:flex;align-items:center;gap:12px;justify-content:flex-end;margin-top:20px}.status{margin-right:auto;color:var(--muted)}.done{border-color:#81b89a;background:#f6fff9}.error{color:#a12820}@media(max-width:560px){main{margin-top:28px}.pathrow{grid-template-columns:1fr}.review{grid-template-columns:1fr}.review dd{margin-bottom:8px}.actions{align-items:stretch;flex-direction:column}.status{margin:0}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}.btn:hover,button:hover{transform:none}}
</style></head><body><main><header><div class="eyebrow">HoloCore · private and local</div><h1>Choose what HoloCore connects.</h1><p>Your files stay on this computer. Nothing changes until you review and apply the plan.</p></header>
<section><h2>1. Your Second Brain</h2><p>Choose the HoloCore Home folder. Its Knowledge subfolder can be opened directly in Obsidian.</p><div class="field"><label for="home">HoloCore Home</label><div class="pathrow"><input id="home" type="text"><button type="button" onclick="browse('home')">Browse</button></div></div><div class="field"><label for="project">First project <span class="badge">optional</span></label><div class="pathrow"><input id="project" type="text" placeholder="Choose a code or document project"><button type="button" onclick="browse('project')">Browse</button></div></div></section>
<section><h2>2. AI tools and features</h2><p>Detected tools are selected. Clear anything you do not want HoloCore to configure.</p><div id="clients" class="choices"></div><div class="choices"><label class="choice"><input id="map" type="checkbox" checked><span><b>Build the first Code Map</b><small>Indexes project structure locally. You can run this later.</small></span></label><label class="choice"><input id="updates" type="checkbox"><span><b>Daily update checks</b><small>Checks at most once per active day. HoloCore does not run a background service.</small></span></label></div></section>
<section><h2>3. Local AI model <span class="badge">optional</span></h2><p id="runtime">Checking Ollama on this computer…</p><div class="field"><label for="model">Model used for local extraction</label><select id="model"><option value="">Deterministic mode (no model)</option></select></div></section>
<section><h2>4. Review</h2><p>These paths and actions are calculated before HoloCore writes anything.</p><ul id="planActions"></ul><dl id="paths" class="review"></dl><div class="actions"><span id="status" class="status">Ready to review.</span><button class="primary" id="apply" type="button" onclick="applySetup()">Apply setup</button></div></section>
<section id="complete" class="done" hidden><h2>HoloCore is ready.</h2><p id="completeText"></p></section>
<script>const S=''' + safe_state + r''';const TOKEN=''' + safe_token + r''';const $=id=>document.getElementById(id);$('home').value=S.home;$('project').value=S.project;const names={claude:'Claude',codex:'Codex',cursor:'Cursor',gemini:'Gemini',opencode:'OpenCode'};Object.entries(S.clients).forEach(([name,x])=>{$('clients').insertAdjacentHTML('beforeend',`<label class="choice"><input type="checkbox" data-client="${name}" ${S.defaults.clients.includes(name)?'checked':''}><span><b>${names[name]||name}</b> <span class="badge ${x.installed?'found':''}">${x.installed?'Detected':'Not detected'}</span><small>${x.evidence.length?esc(x.evidence.join(' · ')):'No executable or settings folder found'}</small></span></label>`)});const candidate=S.models.candidates[0];$('runtime').textContent=candidate.reachable?`Ollama detected · ${candidate.models.length} installed model${candidate.models.length===1?'':'s'}`:'Ollama was not detected. HoloCore will use deterministic extraction.';candidate.models.forEach(x=>$('model').insertAdjacentHTML('beforeend',`<option value="${esc(x.name)}">${esc(x.name)}</option>`));function esc(x){return String(x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}function data(){return{home:$('home').value,project:$('project').value,clients:[...document.querySelectorAll('[data-client]:checked')].map(x=>x.dataset.client),build_code_map:$('map').checked,auto_update:$('updates').checked,model:$('model').value}}async function post(path,body){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-HoloCore-Token':TOKEN},body:JSON.stringify(body)});const x=await r.json();if(!r.ok)throw Error(x.error||'Request failed');return x}async function review(){try{const x=await post('/api/plan',data());$('planActions').innerHTML=x.actions.map(v=>`<li>${esc(v)}</li>`).join('');$('paths').innerHTML=Object.entries(x.paths).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');$('status').textContent='Plan is ready.';$('status').className='status'}catch(e){$('paths').innerHTML='';$('planActions').innerHTML='';$('status').textContent=e.message;$('status').className='status error'}}let timer;document.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(review,180)});async function browse(kind){try{const x=await post('/api/pick-folder',{kind});if(x.path){$(kind).value=x.path;review()}}catch(e){$('status').textContent=e.message}}async function applySetup(){$('apply').disabled=true;$('status').textContent='Applying setup…';try{const x=await post('/api/apply',data());$('complete').hidden=false;$('completeText').textContent=x.next;$('status').textContent='Setup complete. You can close this tab.';$('complete').scrollIntoView({behavior:'smooth'})}catch(e){$('status').textContent=e.message;$('status').className='status error';$('apply').disabled=false}}review();</script></main></body></html>'''


class SetupWizardService:
    def __init__(self, *, home: str | Path | None = None, project: str | Path | None = None):
        self.state = build_setup_state(home=home, project=project)
        self.token = secrets.token_urlsafe(24)
        self.server: ThreadingHTTPServer | None = None


class _SetupHandler(BaseHTTPRequestHandler):
    service: SetupWizardService

    def _send(self, value: Any, status: int = 200, content_type: str = "application/json") -> None:
        data = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise ValueError("Request is too large.")
        value = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(value, dict):
            raise ValueError("Request must be an object.")
        return value

    def do_GET(self) -> None:
        if urlparse(self.path).path in {"/", "/setup"}:
            return self._send(render_setup_html(self.service.state, self.service.token), content_type="text/html")
        return self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.headers.get("X-HoloCore-Token") != self.service.token:
            return self._send({"error": "invalid local setup token"}, 403)
        try:
            body = self._body()
            path = urlparse(self.path).path
            if path == "/api/plan":
                return self._send(build_setup_plan(body))
            if path == "/api/pick-folder":
                kind = str(body.get("kind") or "folder")
                selected = pick_folder("Choose HoloCore Home" if kind == "home" else "Choose your first project")
                return self._send({"path": selected})
            if path == "/api/apply":
                result = apply_setup(body)
                if self.service.server:
                    threading.Timer(2.0, self.service.server.shutdown).start()
                return self._send(result)
            return self._send({"error": "not found"}, 404)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return self._send({"error": str(exc)}, 400)

    def log_message(self, *_: Any) -> None:
        pass


def serve_setup_wizard(*, home: str | Path | None = None, project: str | Path | None = None,
                       host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> int:
    service = SetupWizardService(home=home, project=project)
    handler = type("BoundSetupHandler", (_SetupHandler,), {"service": service})
    server = ThreadingHTTPServer((host, port), handler)
    service.server = server
    url = f"http://{host}:{server.server_port}/"
    print(json.dumps({"url": url, "local_only": True, "close": "Ctrl+C"}, indent=2))
    if open_browser:
        threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


__all__ = ["apply_setup", "build_setup_plan", "build_setup_state", "pick_folder", "render_setup_html", "serve_setup_wizard"]
