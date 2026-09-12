#!/usr/bin/bash
# Launched in a visible terminal from the app. Package groups are fixed.
set -euo pipefail
export PATH=/usr/bin
unset BASH_ENV ENV

case "${1:-}" in
  core) packages=(scrcpy android-tools); label="Android mirroring tools" ;;
  companion) packages=(kdeconnect); label="KDE Connect companion features" ;;
  discovery) packages=(avahi); label="Automatic wireless discovery" ;;
  usb) packages=(android-udev); label="USB device access rules" ;;
  *) echo "Unknown dependency group." >&2; exit 2 ;;
esac

finish() {
  result=$?
  if (( result != 0 )); then
    echo
    echo "Installation did not finish. Review the message above, then try again."
  fi
  if [[ -t 0 ]]; then
    read -r -p "Press Enter to close this window…" _ || true
  fi
  exit "$result"
}
trap finish EXIT

echo "$label"
echo "Packages: ${packages[*]}"
echo "Your desktop password may be requested."
echo
/usr/bin/sudo /usr/bin/pacman -S --needed "${packages[@]}"
if [[ $1 == discovery ]]; then
  echo "Enabling the Avahi discovery service…"
  /usr/bin/sudo /usr/bin/systemctl enable --now avahi-daemon.service
fi
echo
echo "All set. Return to the app; tool status refreshes automatically."
