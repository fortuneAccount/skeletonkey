# skeletonKey

**The Ultimate Unified Interface for ROMs, Emulators, and Frontends.**

### Project Status: The Python Evolution
skeletonKey is currently in a high-intensity porting stage, evolving from its legacy AutoHotKey (AHK) roots into a modern, cross-platform Python 3.10+ powerhouse. We are systematically dismantling the old `.ahk` logic and forging it into a modular, robust PyQt6 application. 

This tool serves as a comprehensive GUI to download and configure emulators, deploy frontends, launch ROMs, and manage assets/artwork by leveraging metadata databases and scrapers.

---

## Legacy AHK Overview
**Version:** 0.99.50.011  
**Author:** oldtools  (hey...this guy looks like the poseur I used to pretend I wanted to be)
The legacy version remains functional via `working.ahk`. To build from source, use AutoHotKey (Unicode-32bit) and the dependencies located in `\bin\`.

---

## Requirements

- Python 3.10+
- PyQt6
- Requests
- (Optional) winshell (for Windows shortcut support)

## Installation

```bash
pip install -r requirements.txt
```

## Running

```bash
python Python/main.py
```

## Project Structure

```
skeletonkey/
├── requirements.txt     # Python dependencies
├── python/main.py              # Entry point
├── python/assets/              # Entry point
│   ├── AHKSock.ahk
│   ├── AppParams.set
│   ├── archiveeula.set
│   ├── archive_eula.set
│   ├── arcorg.put
│   ├── arcorg.set
│   ├── Assignments.set
│   ├── bios.set
│   ├── BSL.ahk
│   ├── BuildTools.set
│   ├── colorpicker.ahk
│   ├── corelk.set
│   ├── dosbox.set
│   ├── emuCfgPresets.set
│   ├── emuexe.ahk
│   ├── EmuParts.set
│   ├── es_input.cfg.set
│   ├── es_settings.cfg.set
│   ├── excludeExtract.set
│   ├── fullsets_eula.set
│   ├── fuzSysLk.set
│   ├── generic_eula.set
│   ├── gets.ahk
│   ├── HtmlDlg.ahk
│   ├── init.ahk
│   ├── launchparams.set
│   ├── lbex.ahk
│   ├── LkUp.set
│   ├── LVA.ahk
│   ├── LV_InCellEdit.ahk
│   ├── MAME - Arcade.set
│   ├── mediafe.set
│   ├── moonbound_eula.set
│   ├── pgsettings.set
│   ├── pg_input.cfg.set
│   ├── PortableUtil.ahk
│   ├── Public-Domain_eula.set
│   ├── Public_Domain_eula.set
│   ├── racoreopt.set
│   ├── ReadMe.set
│   ├── retroarch.set
│   ├── rfcontrols.set
│   ├── rfsettings.set
│   ├── rjcmd_header.set
│   ├── rjcmd_postjoy.set
│   ├── rjcmd_prejoy.set
│   ├── rjcmd_runloop.set
│   ├── rjcmd_runproc.set
│   ├── sets.ahk
│   ├── size.set
│   ├── skdeploy.set
│   ├── Skey-Deploy.ahk
│   ├── SystemEmulators.set
│   ├── SystemLocations.set
│   ├── tf.ahk
│   ├── the-eyeeula.set
│   ├── the-eye_eula.set
│   ├── Themes.put
│   ├── themes.set
│   ├── the_eye_eula.set
│   ├── update.ahk
│   ├── various_eula.set
│   ├── working.ahk
       # INI-based settings read/write
├── python/core/                # Business logic (platform-agnostic)
│   ├── config.py        # INI-based settings read/write
│   ├── launcher.py      # ROM/emulator launch logic
│   ├── downloader.py    # aria2c/wget/requests download wrapper
│   ├── updater.py       # Application update logic
│   └── portable.py      # Portable mode / path migration
├── python/data/                # Data model loaders for .set config files
│   ├── systems.py       # SystemLocations.set parser
│   ├── emulators.py     # EmuParts.set parser
│   ├── assignments.py   # Assignments.set parser
│   └── launch_params.py # launchparams.set parser
├── python/ui/                  # PyQt6 UI layer
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
