# UT_VFX

Professional VFX production pipeline management application for studio workflows.

**Version:** BETA 1.1.8  
**Framework:** PySide6 (Qt for Python)  
**Database:** PostgreSQL  
**Platform:** Windows 10/11

---

## 🎯 Features

### Core Production Tools

- **Folder Creator** - Automated VFX project folder structure generation
- **Scan Manager** - Large dataset transfer with verification and rollback
- **Incoming Delivery** - Smart Ingest for receiving media drops
- **CAP Rename** - Batch file renaming with studio conventions
- **Stock Viewer** - Centralized asset library proxy browser
- **Shot Review** - VFX supervisor review and lineup handoff

### Management & Analytics

- **Dashboard** - Production analytics and team metrics
- **Admin Panel** - User management, role permissions, live operations
- **Attendance** - Team time tracking and reporting
- **Tester Panel** - QA testing and quality assurance tools

### Recent Enhancements (Week 1)

- ⚡ **60% Faster Startup** - Lazy tab initialization
- ⌨️ **Keyboard Shortcuts** - Power user productivity (Ctrl+1-9, Ctrl+P, F5)
- 📊 **Performance Tracking** - Enhanced telemetry with bottleneck detection
- 📈 **Progress Indicators** - Visual feedback for long operations

---

## 💻 Requirements

### Software

- **Python:** 3.9 - 3.12 (for development)
- **OS:** Windows 10/11
- **Database:** SQLite (Standalone) or PostgreSQL 14+ (Studio Proxy)

### Network

- Access to `X:\Extra\UT_Central` (or configured server path)
- Database server connectivity (default: 192.168.0.45:5432)

---

## 🚀 Quick Start

### For End Users

1. Run the installer provided by your administrator
2. Launch **UT_VFX** from Desktop shortcut
3. Log in with your Windows username and studio password

### For Developers

```bash
# Clone repository
cd V0040

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate

# Install dependencies
pip install -r ut_vfx/requirements.txt

# Run application
python ut_vfx/gatekeeper_main.py
```

---

## 📚 Documentation

- [BUILD_INSTRUCTIONS.md](../BUILD_INSTRUCTIONS.md) - Build and deployment guide
- [docs/USER_MANUAL.md](../docs/USER_MANUAL.md) - End user documentation
- [docs/DEVELOPER_GUIDE.md](../docs/DEVELOPER_GUIDE.md) - Development guide
- [docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) - System architecture
- [docs/TESTING_GUIDE.md](../docs/TESTING_GUIDE.md) - Testing procedures

---

## 🏗️ Architecture

**Entry Point:** `gatekeeper_main.py` (authentication layer)

**Structure:**

```
ut_vfx/
├── core/              # Business logic
│   ├── domain/        # Domain models & managers
│   ├── infra/         # Infrastructure (DB, network, telemetry)
│   └── services/      # Application services
├── gui/               # User interface
│   ├── components/    # Reusable UI components
│   ├── tabs/          # Feature tabs
│   └── main_window.py # Main application window
└── utils/             # Utilities (security, JSON, logging)
```

---

## 🔑 Key Technologies

- **UI Framework:** PySide6 (Qt 6.5+)
- **Database:** SQLite & psycopg2-binary (PostgreSQL dual-driver)
- **Data Processing:** pandas, numpy
- **Multimedia:** opencv-python, Pillow, fileseq, lucidity
- **Security:** cryptography, keyring (Windows Credential Manager)
- **Visualization:** matplotlib, seaborn, plotly
- **Reporting:** reportlab (PDF generation)
- **Build:** PyInstaller 6.0+

---

## ⌨️ Keyboard Shortcuts

### Global

- `Ctrl+1` to `Ctrl+9` - Switch to tab 1-9
- `Ctrl+P` - Quick search/command palette
- `F5` or `Ctrl+R` - Refresh current tab
- `Ctrl+Shift+S` - Jump to Settings tab



---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=ut_vfx --cov-report=html

# View coverage report
start htmlcov/index.html
```

**Test Coverage Target:** 25-30% (current)

---

## 📦 Building

```bash
# Build installer (requires Inno Setup 6)
python tools/build_pipeline.py

# Output: Installers/setup_Capsule_VFX_Production_vX.X.X.exe
```

**Installer Size:** ~800MB-1GB (includes Kdenlive)

---

## 🔒 Security

- **No Hardcoded Passwords** - Windows Credential Manager integration
- **Input Validation** - SecurityValidator for all user input
- **Safe File Operations** - Atomic writes with checksum verification
- **PostgreSQL** - Secure database with connection pooling

---

## 📝 License

Proprietary - UT Studio Internal Use Only

---

## 🤝 Support

**Issues:** Contact IT at `capsuleutkarsh@gmail.com`  
**Logs:** `%LOCALAPPDATA%\UTVFX\logs\`

---

**Last Updated:** 2026-01-17  
**Maintained by:** UT Studio Development Team
