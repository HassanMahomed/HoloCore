"""Local browser Console for inspecting and editing HoloCore knowledge."""
from __future__ import annotations

import html
import json
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .animus import Animus
from .activity import ActivityLedger
from .archive import Archive
from .commands import COMMANDS
from .config import Config
from .layout import world_paths
from .home import HomeManager
from .global_graph import build_global_graph
from .atlas_html import render_atlas_html
from .install import inspect_client_connections


def _record(value):
    return asdict(value) if hasattr(value, "__dataclass_fields__") else value


def build_console_payload(root: Path) -> dict:
    root = root.resolve()
    config = Config.load(root=root)
    paths = world_paths(root, config)
    world = config.world_id or root.name
    worlds = [{"name": item.get("name", item.get("id", "World")), "root": item.get("root", "")} for item in HomeManager(Path(paths["home"])).list_worlds().get("worlds", [])]
    animus = Animus(config.animus_path)
    try:
        status = animus.status(world=world)
        chats = [_record(item) for item in animus.timeline(world=world, limit=200)]
        shards = [_record(item) for item in animus.shards(world=world)[:200]]
        decks = [_record(item) for item in animus.decks(world=world)]
        chronicle = [_record(item) for item in animus.signal_chronicle(world=world, limit=200)]
    except (KeyError, OSError, ValueError) as exc:
        status = {"ready": False, "error": str(exc), "database": str(config.animus_path)}
        chats, shards, decks, chronicle = [], [], [], []
    try:
        activity_ledger = ActivityLedger(config.animus_path)
        activity_status = activity_ledger.status(project_id=world)
        runs = activity_ledger.runs(project_id=world, limit=100)
        run_actions = {
            run["id"]: activity_ledger.actions(run["id"], limit=200)
            for run in runs[:25]
        }
        run_handoffs = {
            run["id"]: activity_ledger.handoff(project_id=world, run_id=run["id"], action_limit=10, item_limit=25)
            for run in runs[:25]
        }
    except (OSError, ValueError) as exc:
        activity_status = {"ready": False, "error": str(exc), "database": str(config.animus_path)}
        runs, run_actions, run_handoffs = [], {}, {}
    connections = inspect_client_connections()
    archive = Archive(config.vault)
    wiki = []
    for note in archive._notes()[0]:  # bounded by Archive's safety limits
        rel = note.relative_to(archive.vault).as_posix()
        try:
            item = archive.read(rel)
            wiki.append({"path": rel, "title": note.stem, "content": item["content"], "modified": note.stat().st_mtime})
        except (OSError, ValueError):
            continue
    atlas = {"path": paths["atlas_json"], "html": paths["atlas_html"], "exists": Path(paths["atlas_json"]).exists()}
    if atlas["exists"]:
        try:
            graph = json.loads(Path(paths["atlas_json"]).read_text(encoding="utf-8"))
            atlas.update({"nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", [])), "constellations": len(graph.get("constellations", graph.get("communities", [])))})
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    global_path = Path(paths["home"]) / "Console" / "global-atlas.html"
    global_graph = build_global_graph(paths["home"])
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text(render_atlas_html(global_graph, title="All Projects Code Map"), encoding="utf-8")
    return {
        "world": world, "root": str(root), "worlds": worlds, "paths": paths, "status": status,
        "chats": chats, "shards": shards, "decks": decks, "chronicle": chronicle,
        "activity_status": activity_status, "runs": runs, "run_actions": run_actions, "run_handoffs": run_handoffs,
        "connections": connections,
        "wiki": wiki, "atlas": atlas, "global_atlas": {"path": str(global_path), "worlds": len(global_graph.get("graph", {}).get("worlds", [])), "nodes": len(global_graph.get("nodes", []))},
        "commands": [{**asdict(command), "effect": ("maintenance" if command.name in {"cleanup", "doctor", "status", "install-check", "update", "sync-all"} else "ingest/sync" if command.name in {"ingest", "inbox-sync", "animus-sync", "atlas-refresh"} else "write activity" if command.name == "handoffs" else "write knowledge" if command.write else "read/query")} for command in COMMANDS],
    }


def render_console_html(payload: dict) -> str:
    state = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return """<!doctype html><html><head><meta charset="utf-8"><title>HoloCore Console</title>
<style>body{margin:0;background:#0b1020;color:#dce7ff;font:14px system-ui;overflow-x:hidden}header{padding:20px 28px;background:#121a31;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}h1{margin:0;color:#8ee7ff}nav{padding:12px 28px;background:#0f172b;display:flex;gap:8px;flex-wrap:wrap}button{background:#1b2b4d;color:#dce7ff;border:1px solid #35527f;border-radius:6px;padding:8px 12px;cursor:pointer}button.active{background:#2389a8}main{padding:20px 28px;max-width:1400px;margin:auto;min-width:0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.card{background:#121a31;border:1px solid #263b61;border-radius:8px;padding:14px;margin:8px 0;min-width:0;overflow:hidden}.muted{color:#91a4c7}pre,textarea{width:100%;max-width:100%;box-sizing:border-box;background:#080d19;color:#dce7ff;border:1px solid #30486f;border-radius:6px;padding:10px;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}textarea{min-height:280px}input,select{background:#080d19;color:#dce7ff;border:1px solid #30486f;padding:8px;border-radius:5px;max-width:100%}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}details{background:#121a31;border:1px solid #263b61;border-radius:8px;padding:10px;margin:8px 0}summary{cursor:pointer;color:#8ee7ff;font-weight:600}a{color:#8ee7ff}</style></head><body>
<header><div><h1>HoloCore Console</h1><div class="muted" id="world"></div></div><div class="row"><label for="worldSelect">Project</label><select id="worldSelect" onchange="switchWorld()"></select><span class="muted">Local-first · private by default</span></div></header><nav id="nav"></nav><main id="app"></main><script id="state" type="application/json">"""+state+"""</script>
<script>const S=JSON.parse(document.getElementById('state').textContent);const tabs=['Overview','Chat History','Memories','Knowledge','Code Map','Agent Activity','Locations','Commands','Setup & Updates'];let current='Overview';
function esc(x){return String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}function card(title,body){return `<div class="card"><h3>${esc(title)}</h3>${body}</div>`}const pathNames={world:'Project',world_id:'Project ID',home:'HoloCore Home',shared_brain:'Shared knowledge',worlds_registry:'Projects list',start_here:'Start here',archive:'Knowledge storage',world_archive:'Project knowledge',ingest_inbox:'Import folder',raw_sources:'Original files',archive_entries:'Knowledge notes',archive_index:'Knowledge index',atlas_json:'Code Map data',atlas_html:'Code Map view',atlas_runtime:'Code Map runtime',memory_shards:'Memory storage',raw_chats:'Chat archive',runtime:'Runtime',claude_mcp:'Claude connection',claude_commands:'Claude commands',claude_hooks:'Claude activity hook',codex_mcp:'Codex connection',codex_hooks:'Codex activity hook',codex_skills:'Codex commands'};function pathName(k){return pathNames[k]||k.replaceAll('_',' ')}function runCards(q=''){const query=q.trim().toLowerCase();const rows=S.runs.filter(r=>{const actions=S.run_actions[r.id]||[];return !query||JSON.stringify([r.agent,r.agent_type,r.model,r.status,actions.map(x=>[x.event,x.tool,x.status,x.items])]).toLowerCase().includes(query)});return rows.map(r=>{const h=S.run_handoffs[r.id]||{};return card(`${r.agent} · ${r.agent_type}`,`<div class="muted">${esc(r.status)} · ${esc(r.started_at)} · ${esc(r.model||'model not reported')}</div><div class="muted">${h.summary?`${h.summary.actions} actions · ${h.summary.failures} failures · ${h.summary.affected_items} affected items`:''}</div>`+(S.run_actions[r.id]||[]).map(x=>`<details><summary>${esc(x.event)}${x.tool?' · '+esc(x.tool):''}</summary><div class="muted">${esc(x.occurred_at)} · ${esc(x.status)}</div>${(x.items||[]).map(i=>`<div>${esc(i)}</div>`).join('')}</details>`).join(''))}).join('')||'<p class="muted">No matching agent activity.</p>'}function filterRuns(){document.getElementById('runCards').innerHTML=runCards(document.getElementById('runFilter').value)}
function render(){document.getElementById('world').textContent=S.world+' · '+S.root;const ws=document.getElementById('worldSelect');ws.innerHTML='';S.worlds.forEach(x=>{const o=document.createElement('option');o.value=x.root;o.textContent=x.name;o.selected=x.root===S.root;ws.appendChild(o)});document.getElementById('nav').innerHTML=tabs.map(t=>`<button class="${t==current?'active':''}" onclick="current='${t}';render()">${t}</button>`).join('');let a='';
if(current==='Overview'){a='<div class="grid">'+card('Knowledge',`<b>${S.wiki.length}</b> notes`)+card('Memories',`<b>${S.status.memory_shards??0}</b> memories · <b>${S.chats.length}</b> conversations`)+card('Code Map',`${S.atlas.exists?'Ready':'Missing'} · ${S.atlas.nodes||0} items`)+card('Agent Activity',`<b>${S.activity_status.runs||0}</b> runs · <b>${S.activity_status.actions||0}</b> actions · <b>${S.activity_status.items||0}</b> affected items`)+'</div>'}
if(current==='Chat History')a=S.chats.length?S.chats.map(x=>card(x.title||x.kind,`<div class="muted">${esc(x.occurred_at)} · ${esc(x.source_ref||'')}</div><pre>${esc(x.content)}</pre>`)).join(''):'<p class="muted">No captured conversations in this project.</p>';
if(current==='Memories')a='<div class="grid">'+card('Memories',S.shards.length+' stored')+card('Memory groups',S.decks.length+' focused areas')+card('Timeline',S.chronicle.length+' tracked changes')+'</div>'+(S.shards.map(x=>card(x.sector_id||'general',`<pre>${esc(x.content)}</pre>`)).join('')||'<p class="muted">No memories in this project.</p>');
if(current==='Knowledge')a='<div class="row"><select id="wikiSelect" onchange="loadWiki()">'+S.wiki.map(x=>`<option value="${esc(x.path)}">${esc(x.path)}</option>`).join('')+'</select><button onclick="newWiki()">New note</button><button onclick="saveWiki()">Save</button></div><textarea id="wikiText"></textarea><p class="muted">Editable Markdown knowledge. Managed notes include provenance; user notes remain user-owned.</p>';
if(current==='Code Map')a=card('Project Code Map',`${S.atlas.exists?'Ready':'Missing'} · ${S.atlas.nodes||0} items · ${S.atlas.edges||0} relationships · ${S.atlas.constellations||0} clusters<br><span class="muted">${esc(S.atlas.path)}</span>`)+(S.atlas.exists?'<iframe title="Project Code Map" src="/atlas" style="width:100%;height:620px;border:1px solid #35527f;border-radius:8px;background:#080d19"></iframe>':'<p class="muted">Refresh the code map to create the embedded view.</p>')+card('All Projects Code Map',`${S.global_atlas.worlds} projects · ${S.global_atlas.nodes} items<br><iframe title="All Projects Map" src="/global-atlas" style="width:100%;height:620px;border:1px solid #35527f;border-radius:8px;background:#080d19"></iframe>`);
if(current==='Agent Activity')a=S.runs.length?'<div class="row"><input id="runFilter" aria-label="Search agent activity" placeholder="Search agents, tools, status, or affected items" oninput="filterRuns()"></div><div id="runCards">'+runCards()+'</div>':'<p class="muted">No agent activity recorded yet. Reconnect Codex or Claude to install the activity hooks.</p>';
if(current==='Locations')a=card('Exact storage locations',Object.entries(S.paths).map(([k,v])=>`<div><b>${esc(pathName(k))}</b>: ${esc(v)}</div>`).join(''));
if(current==='Commands'){const groups={"read/query":[],"write knowledge":[],"write activity":[],"ingest/sync":[],maintenance:[]},groupLabels={"read/query":"Browse and search","write knowledge":"Save knowledge","write activity":"Record handoffs","ingest/sync":"Add or refresh project data",maintenance:"Setup and updates"};S.commands.forEach(x=>(groups[x.effect]||groups['read/query']).push(x));a=Object.entries(groups).map(([g,xs])=>'<details open><summary>'+groupLabels[g]+' ('+xs.length+')</summary><div class="grid">'+xs.map(x=>card(x.name,'<div class="muted">'+esc(x.description)+'</div><code>'+esc(x.invocation)+'</code>')).join('')+'</div></details>').join('')};
if(current==='Setup & Updates')a=card('Current status',`Memories: ${esc(S.status.ready?'ready':'needs attention')}<br>Agent activity: ${esc(S.activity_status.ready?'ready':'needs attention')}<br>Use sync, diagnostics, cleanup, and update from the command panel or AI client.`)+card('AI connections',Object.entries(S.connections).map(([name,x])=>`<div><b>${esc(name)}</b>: ${esc(x.state)} · ${esc(x.detail)}</div>`).join(''));document.getElementById('app').innerHTML=a;if(current==='Knowledge'&&S.wiki.length)loadWiki()}
function switchWorld(){window.location='?root='+encodeURIComponent(document.getElementById('worldSelect').value)}function loadWiki(){let p=document.getElementById('wikiSelect').value;fetch('/api/wiki?path='+encodeURIComponent(p)).then(r=>r.json()).then(x=>document.getElementById('wikiText').value=x.content||'')};function newWiki(){document.getElementById('wikiSelect').innerHTML='<option value="wiki/new-note.md">wiki/new-note.md</option>';document.getElementById('wikiText').value='Write the note here.'};function saveWiki(){let p=document.getElementById('wikiSelect').value;fetch('/api/wiki',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p,content:document.getElementById('wikiText').value})}).then(r=>r.json()).then(x=>{alert(x.error||'Saved');if(!x.error)location.reload()})};render();</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    service = None
    def _send(self, value, status=200, content_type="application/json"):
        data = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status); self.send_header("Content-Type", content_type+"; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parsed = urlparse(self.path)
        requested_root = parse_qs(parsed.query).get("root", [""])[0]
        if requested_root:
            self.service = ConsoleService(Path(requested_root))
        if parsed.path in {"/", "/console"}: return self._send(render_console_html(self.service.payload), content_type="text/html")
        if parsed.path == "/atlas":
            atlas_path = Path(self.service.payload["atlas"]["html"])
            if not atlas_path.is_file(): return self._send("Atlas has not been generated", 404, "text/plain")
            return self._send(atlas_path.read_text(encoding="utf-8", errors="replace"), content_type="text/html")
        if parsed.path == "/global-atlas":
            global_path = Path(self.service.payload["global_atlas"]["path"])
            return self._send(global_path.read_text(encoding="utf-8", errors="replace"), content_type="text/html")
        if parsed.path == "/api/state": return self._send(self.service.payload)
        if parsed.path == "/api/wiki":
            path = parse_qs(parsed.query).get("path", [""])[0]
            try: return self._send(self.service.archive.read(path))
            except Exception as exc: return self._send({"error": str(exc)}, 400)
        return self._send({"error":"not found"}, 404)
    def do_POST(self):
        if self.path != "/api/wiki": return self._send({"error":"not found"},404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            path, content = str(body["path"]), str(body["content"])
            try: result = self.service.archive.update(path, body=content)
            except FileNotFoundError: result = self.service.archive.create(path, content)
            self.service.payload = build_console_payload(self.service.root)
            return self._send(result)
        except Exception as exc: return self._send({"error": str(exc)}, 400)
    def log_message(self, *_): pass


class ConsoleService:
    def __init__(self, root: Path):
        self.root = root.resolve(); self.payload = build_console_payload(self.root); self.archive = Archive(Config.load(root=self.root).vault)


def serve_console(root: Path, host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> int:
    service = ConsoleService(root); _Handler.service = service
    # Keep one discoverable snapshot in Home; the live server remains the
    # editable surface and is still opened by the CLI.
    home = Path(service.payload["paths"]["home"])
    export_path = home / "Console" / "console.html"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(render_console_html(service.payload), encoding="utf-8")
    server = ThreadingHTTPServer((host, port), _Handler); url = f"http://{host}:{server.server_port}/"
    print(json.dumps({"url": url, "html": str(export_path), "world": service.payload["world"], "close": "Ctrl+C"}, indent=2))
    if open_browser: threading.Timer(0.2, lambda: webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


def export_console(root: Path) -> Path:
    service = ConsoleService(root)
    path = Path(service.payload["paths"]["home"]) / "Console" / "console.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_console_html(service.payload), encoding="utf-8")
    return path


__all__ = ["build_console_payload", "export_console", "render_console_html", "serve_console"]
