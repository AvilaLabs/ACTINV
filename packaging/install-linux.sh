#!/usr/bin/env bash
# Optional user-local launcher; run alongside the downloaded AppImage and icon.
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
appimage=${1:-}
if [[ -z "$appimage" ]]; then
  candidates=("$source_dir"/*.AppImage)
  [[ ${#candidates[@]} == 1 && -f "${candidates[0]}" ]] || { echo 'Pass the downloaded ACTINV AppImage path.' >&2; exit 1; }
  appimage=${candidates[0]}
fi
[[ -f "$appimage" && -f "$source_dir/actinv.png" ]] || { echo 'AppImage or actinv.png is missing.' >&2; exit 1; }
data_dir=${XDG_DATA_HOME:-"$HOME/.local/share"}
app_dir="$data_dir/actinv-desktop"
mkdir -p "$app_dir" "$data_dir/applications"
cp -- "$appimage" "$app_dir/ACTINV.AppImage"
cp -- "$source_dir/actinv.png" "$app_dir/actinv.png"
chmod u+x "$app_dir/ACTINV.AppImage"
# Desktop-entry escaping for quoted Exec values and literal percent signs.
executable=${app_dir//\\/\\\\}
executable=${executable//\"/\\\"}
executable=${executable//\$/\\\$}
executable=${executable//\`/\\\`}
executable=${executable//%/%%}
cat > "$data_dir/applications/com.avilalabs.actinv.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ACTINV
Comment=Activation and nuclide inventory
Exec="$executable/ACTINV.AppImage"
Icon=$app_dir/actinv.png
Terminal=false
Categories=Science;Education;
EOF
echo 'ACTINV is installed in your application menu. Nuclear data and problem files are stored separately.'
