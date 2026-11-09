# Third-party notices

CK Music Converter's own code is MIT-licensed (see `LICENSE`). The installer also
contains the following components, each under its own license:

| Component | License | Source |
|---|---|---|
| Python runtime | Python Software Foundation License | https://www.python.org |
| Tcl/Tk (the window toolkit) | Tcl/Tk License (BSD-style) | https://www.tcl.tk |
| Pillow (images, icons) | MIT-CMU (HPND) License | https://github.com/python-pillow/Pillow |
| PyInstaller bootloader | GPL-2.0 with the PyInstaller bootloader exception, which allows bundling programs under any license | https://github.com/pyinstaller/pyinstaller |
| mutagen (audio tags) | GPL-2.0-or-later | https://github.com/quodlibet/mutagen |
| FFmpeg (ffmpeg.exe / ffprobe.exe, in the `bin` folder) | GPL-3.0 (the gyan.dev "essentials" build). It is a separate program the app runs; its license is included as `bin\FFMPEG-LICENSE.txt` | Source: https://ffmpeg.org/download.html · Build: https://www.gyan.dev/ffmpeg/builds/ |

The full license texts of the bundled Python packages are included in the installed program's `_internal` folder (`*.dist-info` folders).
