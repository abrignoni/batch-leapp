#!/usr/bin/env bash
#
# Build standalone macOS binaries of Batch LEAPP with PyInstaller:
#   - dist/Batch LEAPP.app   the double-clickable GUI (bundles Python + Tk)
#   - dist/batch-leapp       the single-file command-line binary
#
# Requirements:  python3 with tkinter, and PyInstaller (pip install pyinstaller)
# Usage:         ./build_macos.sh            # both
#                ./build_macos.sh gui        # just the .app
#                ./build_macos.sh cli        # just the CLI binary
# Output:        dist/   (≈30 MB .app, ≈8 MB CLI)
#
# The binaries are UNSIGNED. On the machine that built them they open normally.
# If you zip and share, the recipient must right-click → Open the first time (or
# run `xattr -dr com.apple.quarantine <file>`) to clear Gatekeeper. The build is
# for this Mac's architecture (Apple Silicon or Intel).
#
set -euo pipefail
cd "$(dirname "$0")"
what="${1:-all}"

if ! python3 -c "import PyInstaller" 2>/dev/null; then
    echo "PyInstaller is not installed. Run:  python3 -m pip install pyinstaller" >&2
    exit 1
fi

if [ "$what" = "all" ] || [ "$what" = "gui" ]; then
    python3 -m PyInstaller --noconfirm --windowed --clean \
        --name "Batch LEAPP" \
        --icon leapps.icns \
        --osx-bundle-identifier org.leapps.batch-leapp \
        batch_leapp_gui.py
fi

if [ "$what" = "all" ] || [ "$what" = "cli" ]; then
    python3 -m PyInstaller --noconfirm --onefile --clean \
        --name batch-leapp \
        batch_leapp.py
fi

echo
echo "Built into dist/:"
[ -d "dist/Batch LEAPP.app" ] && echo "  • Batch LEAPP.app   (GUI)"
[ -f "dist/batch-leapp" ]      && echo "  • batch-leapp       (CLI)"
echo "Zip the app for sharing:"
echo "  ditto -c -k --sequesterRsrc --keepParent 'dist/Batch LEAPP.app' Batch-LEAPP-macos.zip"
