#!/bin/bash
source venv/bin/activate
pip install -r requirements.txt
pip install pyinstaller
rm -rf build dist
python -m PyInstaller --onedir --name ssh-GUI ssh_gui.py
./dist/ssh-GUI/ssh-GUI

rm -rf AppDir

mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

cp -a dist/ssh-GUI AppDir/usr/bin/

cat > AppDir/AppRun <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/ssh-GUI/ssh-GUI" "$@"
EOF

chmod +x AppDir/AppRun

cat > AppDir/usr/share/applications/ssh-GUI.desktop <<'EOF'
[Desktop Entry]
Name=ssh-GUI
Exec=ssh-GUI
Icon=ssh-GUI
Type=Application
Categories=Utility;
Terminal=false
EOF

ln -s usr/share/applications/ssh-GUI.desktop AppDir/ssh-GUI.desktop

cp icon.png AppDir/usr/share/icons/hicolor/256x256/apps/ssh-GUI.png
cp icon.png AppDir/ssh-GUI.png

# Set APPIMAGETOOL if appimagetool is not on PATH.
"${APPIMAGETOOL:-appimagetool}" AppDir

./ssh-GUI-x86_64.AppImage