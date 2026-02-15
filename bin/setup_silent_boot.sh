#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Anthias Player — Silent Boot Setup
# Runs on the Pi HOST (not inside containers).
# Idempotent: safe to run multiple times, patches only once.
#
# What it does:
#   1. Disables rainbow splash & firmware boot delay
#   2. Silences kernel output (quiet, no logos, no cursor)
#   3. Redirects console from tty1 to tty3 (invisible)
#   4. Disables login prompt on the display (getty@tty1)
#   5. Installs fbi and creates a splash service that shows
#      the Anthias standby image immediately on boot
#
# Usage:
#   sudo bash bin/setup_silent_boot.sh
#
# A reboot is required after the first run.
# ──────────────────────────────────────────────────────────────

set -euo pipefail

SCREENLY_DIR="${HOME}/.screenly"
FLAG="${SCREENLY_DIR}/.silent-boot-done"

# Detect config file locations (Pi OS Bookworm vs older)
if [ -f /boot/firmware/config.txt ]; then
    CONFIG="/boot/firmware/config.txt"
    CMDLINE="/boot/firmware/cmdline.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG="/boot/config.txt"
    CMDLINE="/boot/cmdline.txt"
else
    echo "[silent-boot] ERROR: Cannot find config.txt"
    exit 1
fi

# Splash image: use standby.png from Anthias static files
SCREENLY_DIR_RESOLVED="$(eval echo ~${SUDO_USER:-$USER})/.screenly"
SPLASH_IMG="${SCREENLY_DIR_RESOLVED}/splash.png"

# Try to find standby.png in common locations
SPLASH_SRC=""
for candidate in \
    "$(eval echo ~${SUDO_USER:-$USER})/screenly/static/img/standby.png" \
    "/usr/src/app/static/img/standby.png" \
    "/data/screenly/staticfiles/img/standby.png"; do
    if [ -f "$candidate" ]; then
        SPLASH_SRC="$candidate"
        break
    fi
done

if [ -f "$FLAG" ]; then
    echo "[silent-boot] Already configured, skipping."
    exit 0
fi

echo "[silent-boot] Configuring silent boot..."

# ── 1. config.txt — disable rainbow splash and boot delay ──
if ! grep -q "disable_splash=1" "$CONFIG" 2>/dev/null; then
    echo "" >> "$CONFIG"
    echo "# Anthias: silent boot" >> "$CONFIG"
    echo "disable_splash=1" >> "$CONFIG"
    echo "boot_delay=0" >> "$CONFIG"
    echo "[silent-boot] Patched config.txt"
else
    echo "[silent-boot] config.txt already patched"
fi

# ── 2. cmdline.txt — silent kernel boot ──
CMDLINE_CONTENT=$(cat "$CMDLINE")
CHANGED=false

# Replace console=tty1 → console=tty3
if echo "$CMDLINE_CONTENT" | grep -q "console=tty1"; then
    CMDLINE_CONTENT=$(echo "$CMDLINE_CONTENT" | sed 's/console=tty1/console=tty3/')
    CHANGED=true
fi

# Add silent parameters
for param in "quiet" "loglevel=0" "logo.nologo" "vt.global_cursor_default=0" "consoleblank=0"; do
    if ! echo "$CMDLINE_CONTENT" | grep -q "$param"; then
        CMDLINE_CONTENT="$CMDLINE_CONTENT $param"
        CHANGED=true
    fi
done

if [ "$CHANGED" = true ]; then
    echo "$CMDLINE_CONTENT" > "$CMDLINE"
    echo "[silent-boot] Patched cmdline.txt"
else
    echo "[silent-boot] cmdline.txt already patched"
fi

# ── 3. Disable getty on tty1 (login prompt on screen) ──
if systemctl is-enabled getty@tty1.service &>/dev/null; then
    systemctl disable getty@tty1.service 2>/dev/null || true
    echo "[silent-boot] Disabled getty@tty1"
else
    echo "[silent-boot] getty@tty1 already disabled"
fi

# ── 4. Install fbi for framebuffer splash ──
if ! command -v fbi &>/dev/null; then
    echo "[silent-boot] Installing fbi..."
    apt-get update -qq && apt-get install -y -qq fbi
    echo "[silent-boot] fbi installed"
else
    echo "[silent-boot] fbi already installed"
fi

# ── 5. Copy splash image ──
if [ -n "$SPLASH_SRC" ]; then
    mkdir -p "$(dirname "$SPLASH_IMG")"
    cp "$SPLASH_SRC" "$SPLASH_IMG"
    echo "[silent-boot] Splash image copied from $SPLASH_SRC"
else
    echo "[silent-boot] WARNING: standby.png not found, splash service will not show an image"
    echo "[silent-boot] You can copy one manually to $SPLASH_IMG"
fi

# ── 6. Create splash systemd service ──
cat > /etc/systemd/system/anthias-splash.service << UNIT
[Unit]
Description=Anthias boot splash
DefaultDependencies=no
After=local-fs.target
Before=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/fbi -d /dev/fb0 --noverbose -a -T 1 -1 ${SPLASH_IMG}
ExecStop=/bin/sh -c "dd if=/dev/zero of=/dev/fb0 bs=1M count=8 2>/dev/null"
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes

[Install]
WantedBy=sysinit.target
UNIT

systemctl daemon-reload
systemctl enable anthias-splash.service
echo "[silent-boot] Splash service created and enabled"

# ── 7. Set flag ──
mkdir -p "$SCREENLY_DIR_RESOLVED"
touch "${SCREENLY_DIR_RESOLVED}/.silent-boot-done"

echo "[silent-boot] Done! Reboot required for changes to take effect."
echo "[silent-boot] Run: sudo reboot"
