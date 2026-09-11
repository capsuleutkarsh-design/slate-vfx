# Studio Deployment Guide

UT_VFX is built for high portability and rapid deployment across local networks. It can be deployed in a purely decentralized manner (P2P via UDP/TCP) or with a Central Server (FastAPI + Postgres) for larger studios.

## 1. Network Requirements
- **Local Area Network (LAN)**: All clients and servers should ideally be on the same subnet for UDP discovery to work properly.
- **Firewall Exceptions**:
  - `UDP Port 5005 - 5015` (P2P Discovery)
  - `TCP Port 5006 - 5015` (P2P Messaging)
  - `TCP Port 5440` (If using the Central PostgreSQL server)

## 2. Server Deployment (Optional but Recommended)
The central server acts as the source of truth for the studio, resolving tracking states faster than P2P.

### A. Environment
1. Ensure Python 3.10+ is installed.
2. Install the requirements: `pip install -r requirements.txt`
3. Setup PostgreSQL 14+ on the server.

### B. Configuring the Server Hub
1. Launch the server script (e.g. `uvicorn ut_vfx.core.infra.server_hub:app --host 0.0.0.0 --port 5440`).
2. The server will initialize its master config directory at `RuntimeData/UT_Central/`.
3. Generate the client configuration using the built-in tool:
   ```bash
   python tools/utilities/create_client_config.py
   ```
   *Edit this script to specify the server's static IP (e.g. `192.168.0.45`).*

## 3. Client Deployment

### A. Packaging the Application
For artists, do not distribute raw python files. Package the app into an executable using PyInstaller.
1. Run the PyInstaller spec or standard command from the root of the project.
2. Ensure the `external/olive-editor` binary is either bundled or deployed alongside the final `.exe`.

### B. Client Configuration
1. Take the `client_config.json` generated in Step 2B.
2. Place it in the user's home directory on the client PC:
   `C:\Users\<ArtistName>\RuntimeData\UTVFX\config.json`
3. When the artist launches the `.exe`, the `ConfigManager` will read this file, realize it's connected to a network, and seamlessly switch from the local `SQLite` fallback database to the central `Postgres` database.

## 4. Storage Setup
- Mount the Studio's NAS/SAN to a shared drive letter (e.g. `X:\` or `Z:\`) or ensure all clients use the same UNC path (e.g. `\\STUDIO-NAS\VFX_Library`).
- Ensure the `SERVER_ROOT` config parameter in `client_config.json` points to this exact drive letter/path so all artists resolve file locations identically.
