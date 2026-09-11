# User Guide: UT_VFX

Welcome to UT_VFX. This application acts as the central hub for tracking shots, managing massive EXR sequence ingests, reviewing proxies, and connecting to your VFX software.

## 1. Advanced Dashboard
The dashboard provides a high-level overview of the active project.
- **Projects Tab**: Switch between active projects in the studio database.
- **Filtering**: You can filter by artist, status, or department (e.g. `Show only In Progress from Matchmove`).
- **Real-Time Polling**: The dashboard uses a background worker to constantly fetch updates from the database without refreshing.

## 2. Shot Review & Lineup Editor
Instead of opening heavy compositing software just to check a frame, UT_VFX has a built-in sequence reviewer.

1. Navigate to the **Shot Review** tab.
2. The UI will scan for available EXR or DPX sequences in the `01_Scan` or `06_Comp` directories.
3. Click a sequence to load it into the **Lineup Editor**.
4. The system will trigger `ShotProxyRenderWorker` in the background to transcode the EXR sequence into an MP4 proxy using FFmpeg.
5. Once complete, you can scrub the timeline smoothly. 
6. You can launch the sequence directly into **Olive Video Editor** to make cuts by clicking the "Launch Editor" button. The Olive window is physically embedded inside the UT_VFX application using Windows API re-parenting.

## 3. Smart Ingestion Tool
When you receive a 2TB drive from a client full of unsorted files:
1. Open the **Beta Smart** ingest tab.
2. Select the client drive as the source.
3. Specify your Target Project folder (e.g. `Z:\UT2026`).
4. Click **Start Ingestion**.
5. The `SmartIngestAnalyzer` will read every file, categorize it into Audio, Plates, 3D, or Documents, and safely copy it to the correct template structure. It verifies the checksum to ensure no data corruption occurs.

## 4. Troubleshooting
If the application behaves unexpectedly:
- **Advanced Log Viewer**: Click the Logs button on the bottom bar to open a real-time console. It shows you the raw logging from the Database, Network, and Thread workers. Look for `[ERROR]` tags.
- **Network Discovery Issues**: If you don't see other users in the User List, check your Windows Firewall settings to ensure UDP Port 5005 is allowed for broadcast discovery.
