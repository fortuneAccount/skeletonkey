import json
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _atomic_json_dump(data: any, path: Path):
    """Write JSON to a temporary file and rename it to prevent corruption."""
    fd, temp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        os.replace(temp_path, path)
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e


def migrate_systems_xml_to_json(xml_path: Path, output_path: Path):
    """Converts Systems.xml to Systems.json with full metadata (SHORTNM, etc)."""
    if not xml_path.exists():
        return
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        systems = {}
        for sys_node in root.findall("System"):
            name = sys_node.get("name")
            if not name:
                continue
            data = {}
            for child in sys_node:
                # Flatten XML nodes into JSON keys
                data[child.tag] = child.text if child.text else ""
            systems[name] = data
        
        if systems:
            _atomic_json_dump(systems, output_path)
            print(f"DEBUG: Successfully migrated {xml_path.name} metadata to {output_path.name}")
    except Exception as e:
        print(f"DEBUG: Failed to migrate master XML: {e}")


def migrate_emucfg_to_json(set_path: Path, output_dir: Path, delete_source: bool = False):
    """Splits emucfgpresets.set into Systems and emulators JSON files."""
    print(f"DEBUG: Starting migration of {set_path}...")
    try:
        content = set_path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        content = set_path.read_text(encoding="utf-16", errors="replace")

    print(f"DEBUG: Read {len(content.splitlines())} lines from source.")
    parsed_sections = []  # List of (name, data_dict)
    current_section_name = None
    current_data = {}

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue

        # Detect Section Header: [Section], Section], or Header (no equals sign)
        is_header = False
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            is_header = True
        elif line.endswith("]") and "[" not in line:
            header = line[:-1].strip()
            is_header = True
        elif "=" not in line:
            header = line.strip()
            is_header = True

        if is_header:
            if current_section_name is not None:
                parsed_sections.append((current_section_name, current_data))
            current_section_name = header
            current_data = {}
            continue

        # Detect Key-Value pair
        if current_section_name and "=" in line:
            key, _, val = line.partition("=")
            current_data[key.strip()] = val.strip()

    if current_section_name is not None:
        parsed_sections.append((current_section_name, current_data))

    print(f"DEBUG: Found {len(parsed_sections)} total sections.")
    sections = [s[0] for s in parsed_sections]
    try:
        emu_start = sections.index("MAME")
        emu_end = sections.index("z80tvgame_z80pio")
    except ValueError:
        emu_start = emu_end = -1

    print(f"DEBUG: Emulator range indices: {emu_start} to {emu_end}")

    outputs = {
        "Systems": {},
        "emulators": {}
    }

    for i, (section, data) in enumerate(parsed_sections):
        target = None
        
        # Priority 1: Systems (containing " - ")
        if " - " in section:
            target = "Systems"

        # Priority 2: Emulators (Range [MAME] to [z80tvgame_z80pio])
        elif emu_start <= i <= emu_end and emu_start != -1:
            target = "emulators"

        if target:
            outputs[target][section] = data

    # Save all files as JSON
    for key, data_dict in outputs.items():
        dest = output_dir / f"{key}.json"
        if data_dict:
            _atomic_json_dump(data_dict, dest)
            print(f"DEBUG: Successfully generated {dest} with {len(data_dict)} entries.")
        else:
            print(f"DEBUG: Skipping {key}.json - no entries found.")

    if delete_source and set_path.exists():
        set_path.unlink()
        print(f"DEBUG: Cleaned up legacy file: {set_path.name}")


def migrate_fuzsyslk_to_json(set_path: Path, systems_json_path: Path, output_path: Path, delete_source: bool = False):
    """Converts fuzsyslk.set to JSON using full system names derived from Systems.json SHORTNM."""
    if not systems_json_path.exists():
        print("DEBUG: Skipping fuzsyslk migration - Systems.json master list missing.")
        return

    try:
        # Load Systems metadata to map short-codes to full names
        short_to_full = {}
        if systems_json_path.exists():
            with open(systems_json_path, "r", encoding="utf-8") as f:
                sys_data = json.load(f)
                systems = sys_data.get("systems", sys_data)
                short_to_full = {info.get("SHORTNM"): name for name, info in systems.items() 
                                 if isinstance(info, dict) and info.get("SHORTNM")}

        if not short_to_full:
            print("DEBUG: fuzsyslk migration warning - No SHORTNM metadata found in Systems.json.")

        content = set_path.read_text(encoding="utf-8-sig", errors="replace")
        results = []
        for line in content.splitlines():
            line = line.strip()
            if not line or ">" not in line or line.startswith(";") or line.startswith("["):
                continue
            
            patterns, short_name = line.split(">", 1)
            # Handle multiple short-names on one line (e.g. SNES|NSNES)
            s_names = [sn.strip() for sn in short_name.split("|") if sn.strip()]
            
            full_name = None
            for sn in s_names:
                if sn in short_to_full:
                    full_name = short_to_full[sn]
                    break
            
            if full_name:
                results.append({
                    "name": full_name,
                    "search_terms": patterns.strip()
                })
        
        if results:
            _atomic_json_dump(results, output_path)
            print(f"DEBUG: Successfully generated fuzsyslk.json with {len(results)} patterns.")
            if delete_source and set_path.exists():
                set_path.unlink()
                print(f"DEBUG: Cleaned up legacy file: {set_path.name}")
        else:
            print("DEBUG: Migration result - No patterns could be mapped to full system names.")

    except Exception as e:
        print(f"DEBUG: Failed to migrate fuzsyslk: {e}")