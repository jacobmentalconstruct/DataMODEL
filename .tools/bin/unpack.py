"""Unpack a project skeleton archive into a folder you choose.

`helpers pack` stores this file as the archive's __main__.py, so the archive runs itself:

    python DataMODEL.zip                 # pick a folder in a dialog (or type one)
    python DataMODEL.zip path/to/folder  # unpack straight there

Existing files are never overwritten, so it can be unpacked into a folder that already
holds files. Standard library only.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def _choose_destination(name: str) -> Path | None:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    try:
        import tkinter
        from tkinter import filedialog

        window = tkinter.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title=f"Unpack {name} into which folder?", mustexist=False)
        window.destroy()
        return Path(chosen) if chosen else None
    except Exception:  # no display or no tkinter: fall back to a prompt
        answer = input(f"Unpack {name} into folder: ").strip().strip('"')
        return Path(answer) if answer else None


def main() -> int:
    archive = Path(sys.argv[0]).resolve()
    destination = _choose_destination(archive.stem)
    if destination is None:
        print("cancelled; nothing unpacked")
        return 1
    destination = destination.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    written = 0
    kept: list[str] = []
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            if info.is_dir() or info.filename == "__main__.py":
                continue
            target = (destination / info.filename).resolve()
            if destination not in target.parents:
                continue  # never write outside the chosen folder
            if target.exists():
                kept.append(info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, open(target, "wb") as output:
                output.write(source.read())
            written += 1
    print(f"unpacked {written} files into {destination}")
    if kept:
        more = f" and {len(kept) - 8} more" if len(kept) > 8 else ""
        print(f"kept {len(kept)} existing files unchanged: {', '.join(kept[:8])}{more}")
    print("next: open that folder with your agent; the tools set themselves up on first use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
