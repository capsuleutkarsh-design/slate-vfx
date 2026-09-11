import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List
import uvicorn
import threading
import asyncio
import psutil
import json
import psycopg2
import os

from slate.api.routers import users, attendance, shots

# Initialize FastAPI App
app = FastAPI(title="Slate Waiter API", description="High-performance API Gateway for Slate")

# Allow all origins for local network testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket Manager for Real-Time Updates ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# --- Routes ---

@app.get("/")
def read_root():
    return {"status": "online", "message": "The Waiter is taking orders."}

app.include_router(users.router)
app.include_router(attendance.router)
app.include_router(shots.router)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # We don't expect much from the client yet, just keep connection alive
            data = await websocket.receive_text()
            # If client says something, just echo it back or broadcast it
            await manager.broadcast(f"Broadcast: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

async def telemetry_loop():
    while True:
        try:
            if manager.active_connections:
                try:
                    appdata_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), "Slate_Central")
                    config_path = os.path.join(appdata_dir, "slate_server_config.json")
                    port = 5440
                    if os.path.exists(config_path):
                        with open(config_path, "r") as f:
                            cfg = json.load(f)
                            port = cfg.get("port", port)

                    from slate_server.core.db_credentials import connect_kwargs
                    conn = psycopg2.connect(**connect_kwargs(port, connect_timeout=1))
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_size_pretty(pg_database_size('slate'))")
                        res = cur.fetchone()
                        db_size = res[0] if res else "Unknown"
                        
                        cur.execute("SELECT count(*) FROM tracking_projects")
                        total_projects = cur.fetchone()[0]
                        
                        cur.execute("SELECT count(*) FROM stock_library")
                        total_assets = cur.fetchone()[0]
                        
                        cur.execute("SELECT client_addr FROM pg_stat_activity WHERE client_addr IS NOT NULL")
                        clients = [row[0] for row in cur.fetchall()]
                    conn.close()
                except Exception:
                    db_size = "Offline"
                    total_projects = 0
                    total_assets = 0
                    clients = []

                cpu = psutil.cpu_percent(interval=None)
                ram = psutil.virtual_memory().percent
                
                payload = {
                    "type": "stats",
                    "connections": len(manager.active_connections),
                    "cpu": f"{cpu}%",
                    "ram": f"{ram}%",
                    "db_size": db_size,
                    "projects": total_projects,
                    "assets": total_assets,
                    "clients": clients
                }
                await manager.broadcast(json.dumps(payload))
        except Exception:
            pass
        await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(telemetry_loop())

# --- Admin GUI ---

@app.get("/admin", response_class=HTMLResponse)
def get_admin_dashboard():
    """
    Provides a beautiful, live-updating HTML dashboard to monitor the server.
    """
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Slate_Central API Gateway</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg: #0A0A0F;
                --surface: rgba(255, 255, 255, 0.03);
                --surface-hover: rgba(255, 255, 255, 0.06);
                --border: rgba(255, 255, 255, 0.08);
                --text-primary: #FFFFFF;
                --text-secondary: #9CA3AF;
                --accent-1: #00F0FF;
                --accent-2: #8A2BE2;
                --accent-gradient: linear-gradient(135deg, var(--accent-1) 0%, var(--accent-2) 100%);
            }
            body {
                margin: 0;
                padding: 0;
                background-color: var(--bg);
                color: var(--text-primary);
                font-family: 'Outfit', sans-serif;
                min-height: 100vh;
                display: flex;
                flex-direction: column;
                background-image: 
                    radial-gradient(circle at 15% 50%, rgba(62, 168, 191, 0.08), transparent 25%),
                    radial-gradient(circle at 85% 30%, rgba(138, 43, 226, 0.08), transparent 25%);
            }
            .navbar {
                padding: 1.5rem 2rem;
                display: flex;
                justify-content: space-between;
                align-items: center;
                background: rgba(10, 10, 15, 0.7);
                backdrop-filter: blur(12px);
                border-bottom: 1px solid var(--border);
                position: sticky;
                top: 0;
                z-index: 100;
            }
            .logo {
                font-size: 1.5rem;
                font-weight: 800;
                background: var(--accent-gradient);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                letter-spacing: -0.5px;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }
            .logo::before {
                content: '';
                display: block;
                width: 24px;
                height: 24px;
                background: var(--accent-gradient);
                border-radius: 6px;
                box-shadow: 0 0 15px rgba(62, 168, 191, 0.4);
            }
            .status-badge {
                padding: 0.4rem 1rem;
                border-radius: 50px;
                font-size: 0.85rem;
                font-weight: 600;
                background: rgba(95, 191, 143, 0.1);
                color: #10B981;
                border: 1px solid rgba(95, 191, 143, 0.2);
                display: flex;
                align-items: center;
                gap: 0.4rem;
                box-shadow: 0 0 15px rgba(95, 191, 143, 0.1);
            }
            .status-badge::before {
                content: '';
                display: block;
                width: 8px;
                height: 8px;
                background: #10B981;
                border-radius: 50%;
                box-shadow: 0 0 8px #10B981;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0% { opacity: 0.6; transform: scale(0.9); }
                50% { opacity: 1; transform: scale(1.1); }
                100% { opacity: 0.6; transform: scale(0.9); }
            }
            .container {
                max-width: 1400px;
                margin: 0 auto;
                padding: 2rem;
                width: 100%;
                box-sizing: border-box;
                display: grid;
                grid-template-columns: 350px 1fr;
                gap: 2rem;
                flex: 1;
            }
            .glass-panel {
                background: var(--surface);
                backdrop-filter: blur(16px);
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 1.5rem;
                transition: transform 0.3s ease, box-shadow 0.3s ease, border-color 0.3s ease;
            }
            .glass-panel:hover {
                transform: translateY(-2px);
                box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                border-color: rgba(255, 255, 255, 0.15);
            }
            h2 {
                margin: 0 0 1.5rem 0;
                font-size: 1.25rem;
                font-weight: 600;
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }
            .metric {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 1rem;
                background: rgba(0,0,0,0.2);
                border-radius: 12px;
                margin-bottom: 0.75rem;
            }
            .metric-label {
                color: var(--text-secondary);
                font-size: 0.9rem;
            }
            .metric-value {
                font-size: 1.2rem;
                font-weight: 700;
                font-family: 'JetBrains Mono', monospace;
                color: var(--accent-1);
            }
            .terminal-container {
                background: #050508;
                border-radius: 16px;
                border: 1px solid var(--border);
                height: 600px;
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }
            .terminal-header {
                padding: 0.75rem 1rem;
                background: rgba(255,255,255,0.02);
                border-bottom: 1px solid var(--border);
                display: flex;
                gap: 0.5rem;
            }
            .mac-btn {
                width: 12px;
                height: 12px;
                border-radius: 50%;
            }
            .mac-close { background: #FF5F56; }
            .mac-min { background: #FFBD2E; }
            .mac-max { background: #27C93F; }
            
            .log-window {
                flex: 1;
                padding: 1rem;
                overflow-y: auto;
                font-family: 'JetBrains Mono', monospace;
                font-size: 0.85rem;
                line-height: 1.6;
                scroll-behavior: smooth;
            }
            .log-window::-webkit-scrollbar { width: 8px; }
            .log-window::-webkit-scrollbar-track { background: transparent; }
            .log-window::-webkit-scrollbar-thumb { background: var(--surface-hover); border-radius: 4px; }
            
            .log-entry { margin-bottom: 0.25rem; word-break: break-all; opacity: 0; animation: fadeIn 0.3s forwards; }
            @keyframes fadeIn { to { opacity: 1; } }
            
            .log-time { color: #6B7280; margin-right: 0.5rem; }
            .log-info { color: #60A5FA; }
            .log-warn { color: #FBBF24; }
            .log-error { color: #F87171; }
            .log-success { color: #34D399; }
            
            .client-list {
                list-style: none;
                padding: 0;
                margin: 0;
            }
            .client-item {
                padding: 0.75rem;
                background: rgba(0,0,0,0.2);
                border-radius: 8px;
                margin-bottom: 0.5rem;
                font-size: 0.9rem;
                display: flex;
                align-items: center;
                gap: 0.5rem;
            }
            .client-item::before {
                content: '⚡';
                font-size: 0.8rem;
            }
            
            @media (max-width: 900px) {
                .container { grid-template-columns: 1fr; }
            }
        </style>
    </head>
    <body>
        <div class="navbar">
            <div class="logo">Slate_Central Gateway</div>
            <div class="status-badge" id="conn-status">Gateway Online</div>
        </div>
        
        <div class="container">
            <div class="sidebar">
                <div class="glass-panel" style="margin-bottom: 1.5rem;">
                    <h2>
                        <svg width="20" height="20" fill="none" stroke="var(--accent-1)" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path></svg>
                        Gateway Metrics
                    </h2>
                    <div class="metric">
                        <span class="metric-label">Uptime</span>
                        <span class="metric-value" id="uptime">0h 0m</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Active Connections</span>
                        <span class="metric-value" id="active-connections">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Messages Sec</span>
                        <span class="metric-value" id="msg-rate">0</span>
                    </div>
                </div>
                
                <div class="glass-panel" style="margin-bottom: 1.5rem;">
                    <h2>
                        <svg width="20" height="20" fill="none" stroke="var(--accent-1)" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4 7v10c0 2.21 3.58 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.58 4 8 4s8-1.79 8-4M4 7c0-2.21 3.58-4 8-4s8 1.79 8 4m0 5c0 2.21-3.58 4-8 4s-8-1.79-8-4"></path></svg>
                        System Resources
                    </h2>
                    <div class="metric">
                        <span class="metric-label">CPU Usage</span>
                        <span class="metric-value" id="cpu-usage">0%</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">RAM Usage</span>
                        <span class="metric-value" id="ram-usage">0%</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Database Size</span>
                        <span class="metric-value" id="db-size">Unknown</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Total Projects</span>
                        <span class="metric-value" id="total-projects">0</span>
                    </div>
                    <div class="metric">
                        <span class="metric-label">Stock Assets</span>
                        <span class="metric-value" id="total-assets">0</span>
                    </div>
                </div>
                
                <div class="glass-panel">
                    <h2>
                        <svg width="20" height="20" fill="none" stroke="var(--accent-2)" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path></svg>
                        Live Clients
                    </h2>
                    <ul class="client-list" id="client-list">
                        <li style="color: var(--text-secondary); text-align: center; font-size: 0.9rem; padding: 1rem 0;">Waiting for connections...</li>
                    </ul>
                </div>
            </div>
            
            <div class="main-content">
                <div class="terminal-container">
                    <div class="terminal-header">
                        <div class="mac-btn mac-close"></div>
                        <div class="mac-btn mac-min"></div>
                        <div class="mac-btn mac-max"></div>
                    </div>
                    <div class="log-window" id="logs">
                        <div class="log-entry">
                            <span class="log-time">System</span>
                            <span class="log-info">Connecting to Slate_Central Gateway Telemetry...</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const logsDiv = document.getElementById('logs');
            let ws;
            let reconnectInterval = 2000;
            let start_time = Date.now();
            
            function connect() {
                ws = new WebSocket(`ws://${window.location.host}/ws`);
                
                ws.onopen = () => {
                    document.getElementById('conn-status').innerText = "Gateway Online";
                    document.getElementById('conn-status').style.color = "#10B981";
                    document.getElementById('conn-status').style.background = "rgba(95, 191, 143, 0.1)";
                    addLog("Secure WebSocket telemetry established.", "success");
                    ws.send(JSON.stringify({ type: 'admin_identify' }));
                };
                
                ws.onmessage = function(event) {
                    if(event.data.startsWith("{")) {
                        try {
                            const data = JSON.parse(event.data);
                            if(data.type === "stats") {
                                document.getElementById('active-connections').innerText = data.connections;
                                document.getElementById('cpu-usage').innerText = data.cpu;
                                document.getElementById('ram-usage').innerText = data.ram;
                                document.getElementById('db-size').innerText = data.db_size;
                                document.getElementById('total-projects').innerText = data.projects;
                                document.getElementById('total-assets').innerText = data.assets;
                                
                                const list = document.getElementById('client-list');
                                if(data.clients && data.clients.length > 0) {
                                    list.innerHTML = '';
                                    data.clients.forEach(c => {
                                        const li = document.createElement('li');
                                        li.className = 'client-item';
                                        li.innerText = c;
                                        list.appendChild(li);
                                    });
                                } else {
                                    list.innerHTML = '<li style="color: var(--text-secondary); text-align: center; font-size: 0.9rem; padding: 1rem 0;">No active clients</li>';
                                }
                            }
                        } catch(e) {}
                    } else {
                        addLog(event.data);
                    }
                };
                
                ws.onclose = () => {
                    document.getElementById('conn-status').innerText = "Gateway Offline";
                    document.getElementById('conn-status').style.color = "#EF4444";
                    document.getElementById('conn-status').style.background = "rgba(239, 68, 68, 0.1)";
                    addLog("Lost connection to Gateway. Reconnecting in 2s...", "error");
                    setTimeout(connect, reconnectInterval);
                };
            }
            
            function addLog(msg, type='info') {
                const now = new Date();
                const timeStr = now.toLocaleTimeString('en-US', { hour12: false, hour: "numeric", minute: "numeric", second: "numeric" });
                const div = document.createElement('div');
                div.className = 'log-entry';
                
                let colorClass = 'log-info';
                if(msg.includes('ERROR') || msg.includes('fail')) colorClass = 'log-error';
                else if(msg.includes('WARN')) colorClass = 'log-warn';
                else if(msg.includes('SUCCESS') || msg.includes('connected')) colorClass = 'log-success';
                
                div.innerHTML = `<span class="log-time">[${timeStr}]</span> <span class="${colorClass}">▶ ${msg}</span>`;
                logsDiv.appendChild(div);
                if (logsDiv.children.length > 200) {
                    logsDiv.removeChild(logsDiv.firstChild);
                }
                logsDiv.scrollTop = logsDiv.scrollHeight;
            }

            setInterval(() => {
                const diff = Date.now() - start_time;
                const hrs = Math.floor(diff / 3600000);
                const mins = Math.floor((diff % 3600000) / 60000);
                document.getElementById('uptime').innerText = `${hrs}h ${mins}m`;
            }, 60000);
            
            connect();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


def run_server():
    """Function to launch the server (used by the PySide6 app button)."""
    import subprocess
    import os
    
    logging.info("Starting FastAPI Waiter Server on port 8000...")
    
    # Run Database Migrations
    logging.info("Running Database Migrations...")
    try:
        from slate.core.infra.migrations.auto_migrate import run_auto_migrations
        success = run_auto_migrations()
        if success:
            logging.info("Database migrations applied successfully.")
        else:
            logging.warning("Database migrations reported a failure or were skipped.")
    except Exception as e:
        logging.error(f"Failed to apply migrations: {e}")
        
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

if __name__ == "__main__":
    run_server()
