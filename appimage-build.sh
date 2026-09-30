#!/bin/bash
set -e

source venv/bin/activate
pip install -r requirements.txt
pip install pyinstaller
rm -rf build dist
python -m PyInstaller --onedir --name ssh-deck --add-data "icon.png:." ssh_deck.py

rm -rf AppDir

mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

cp -a dist/ssh-deck AppDir/usr/bin/

cat > AppDir/AppRun <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/ssh-deck/ssh-deck" "$@"
EOF

chmod +x AppDir/AppRun

cat > AppDir/usr/share/applications/ssh-deck.desktop <<'EOF'
[Desktop Entry]
Name=ssh-deck
Exec=ssh-deck
Icon=ssh-deck
Type=Application
Categories=Utility;
Terminal=false
EOF

ln -s usr/share/applications/ssh-deck.desktop AppDir/ssh-deck.desktop

cp icon.png AppDir/usr/share/icons/hicolor/256x256/apps/ssh-deck.png
cp icon.png AppDir/ssh-deck.png

# Set APPIMAGETOOL if appimagetool is not on PATH.
"${APPIMAGETOOL:-appimagetool}" AppDir

echo "Built ssh-deck-x86_64.AppImage"