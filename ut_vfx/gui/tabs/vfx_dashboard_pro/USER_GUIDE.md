# UT_VFX Dashboard - User Guide

## Installation
1. Ensure Python 3.9+ is installed.
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run the application:
   ```
   python main.py
   ```
   Or use `run_app.bat`.

## First Time Setup
1. **Projects**: The app comes with a default configuration in `config/projects.json`. You can edit this file to point to your actual Excel files.
2. **Users**: Default users are:
   - `artist` (No password)
   - `supervisor` (Password: `super123`)
   - `producer` (Password: `prod456`)
   To reset passwords, delete `config/users.json` and restart the app.

## Features

### Dashboard
- **Project Switcher**: Toggle between active projects.
- **Views**: Switch between List (Table) and Grid (Card) views.
- **Search**: Real-time filtering by shot name, sequence, or description.
- **Filters**: Filter by Sequence, Status, or Complexity.

### Editing
- **Double-click** any shot to open the Detail View.
- **Artist**: Read-only access.
- **Supervisor**: Can edit Status and Notes.
- **Producer**: Full access.
- **Save**: Click "Save Changes" to write back to Excel. Backups are created automatically.

### Thumbnails
- The app automatically scans for EXR/DPX files in the configured `base_path`.
- Thumbnails are generated and cached in `cache/thumbnails`.
- If no scan is found, a "NO SCAN" placeholder is shown.

### Google Sheets Sync (Producer Only)
- Requires `credentials.json` from Google Cloud Console placed in `config/`.
- Use the Sync features (implemented in core) to push/pull data.

## Troubleshooting
- **Locked File**: If you see a "Read Only" warning, another user (Supervisor/Producer) has the project open.
- **Missing Thumbnails**: Check if the `base_path` in `projects.json` matches your folder structure.
