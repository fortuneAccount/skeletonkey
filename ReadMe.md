# skeletonKey

**The Supreme Unified Interface for ROMs, Emulators, and Frontends.**

### Project Status: The Python Evolution
`skeletonKey` is currently in a high-intensity architectural ascension. We are systematically dismantling the legacy AutoHotKey (AHK) foundation—a relic of a bygone era—and forging it into a modular, high-performance Python 3.10+ powerhouse driven by PyQt6. 

This tool serves as a comprehensive, cross-platform command center to download and configure emulators, deploy frontends, launch ROMs, and manage assets via metadata databases and advanced scrapers.

---

## Legacy AHK Status
**Version:** 0.99.50.011  
**Author:** oldtools  
The legacy version remains functional via `working.ahk`. To build from source, use AutoHotKey (Unicode-32bit) and the dependencies located in `\bin\`.

---

## Requirements

- Python 3.10+
- PyQt6
- Requests
- (Optional) winshell (for Windows shortcut support)

## Installation

```bash
pip install -r Python/requirements.txt
```

## Running

```bash
python main.py
```

## Project Structure

```
skeletonkey/
├── main.py              # Entry point
├── requirements.txt     # Python dependencies
├── core/                # Business logic (platform-agnostic)
│   ├── config.py        # INI-based settings read/write
│   ├── launcher.py      # ROM/emulator launch logic
│   ├── downloader.py    # aria2c/wget/requests download wrapper
│   ├── updater.py       # Application update logic
│   └── portable.py      # Portable mode / path migration
├── data/                # Data model loaders for .set config files
│   ├── systems.py       # SystemLocations.set parser
│   ├── emulators.py     # EmuParts.set parser
│   ├── assignments.py   # Assignments.set parser
│   └── launch_params.py # launchparams.set parser
├── ui/                  # PyQt6 UI layer
│   ├── main_window.py   # Main tabbed window
│   ├── tabs/            # One module per tab
│   └── widgets/         # Reusable custom widgets
└── utils/               # Shared utilities
    ├── paths.py         # Path resolution helpers
    └── archive.py       # 7z/zip extraction wrapper
```

## License

Personal, non-commercial use only.
You may not compile, deploy or distribute skeletonKey in any manner which facilitates
financial profit or piracy.

You must include this unaltered readme along with any binary.
