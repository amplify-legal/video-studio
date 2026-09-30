#!/usr/bin/env bash
# Cloud environment setup script for the Cloud Video Edit pack.
#
# Paste this whole file into the "Setup script" box of your Claude Code cloud environment
# (claude.ai/code -> your environment -> settings). It runs when a session's machine is
# built, and Anthropic's docs say a setup script that finishes in about 5 minutes is cached,
# so the next session starts with everything already installed.
#
# It installs 3 things and configures nothing secret: your Google Drive token arrives as an
# environment variable (RCLONE_CONFIG_GDRIVE_TOKEN) that you typed into the environment's
# settings, never into a file in your repo.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

$SUDO apt-get update -qq
# ffmpeg: the editor. rclone: the window onto Drive. fonts: captions and option labels.
$SUDO apt-get install -y -qq --no-install-recommends ffmpeg rclone fontconfig fonts-dejavu-core >/dev/null
$SUDO apt-get install -y -qq --no-install-recommends fonts-inter >/dev/null 2>&1 || true

# The tone-map filter that turns iPhone HDR into normal colour lives in zscale.
if ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q " zscale "; then
  echo "WARNING: this ffmpeg has no zscale filter, so HDR phone footage cannot be tone-mapped."
  echo "         TROUBLESHOOTING.md, 'no zscale', has the one-line fix."
fi

ffmpeg -version | head -1
rclone version | head -1
echo "setup done"
