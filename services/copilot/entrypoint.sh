#!/bin/bash
set -e

echo "[Entrypoint] Creating PulseAudio runtime directory..."
mkdir -p /tmp/pulse
chmod 777 /tmp/pulse

# Start virtual display (Xvfb) so Chromium initializes full audio pipeline
# Without a display, headless Chromium skips PulseAudio audio output routing
echo "[Entrypoint] Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1280x720x24 -ac &
XVFB_PID=$!
export DISPLAY=:99
sleep 1
echo "[Entrypoint] Xvfb started on DISPLAY=:99"

export XDG_RUNTIME_DIR=/tmp/pulse
export PULSE_SERVER=unix:/tmp/pulse/native
# Tell PulseAudio not to use D-Bus (not available in Docker)
export DBUS_SESSION_BUS_ADDRESS=does-not-exist

echo "[Entrypoint] Starting PulseAudio virtual audio daemon..."

pulseaudio \
    --daemonize=false \
    --exit-idle-time=-1 \
    --disallow-exit \
    --log-target=stderr \
    --log-level=error \
    --no-cpu-limit \
    -n \
    --load="module-null-sink sink_name=VirtualSink sink_properties=device.description=VirtualSink" \
    --load="module-null-source source_name=VirtualMic" \
    --load="module-native-protocol-unix socket=/tmp/pulse/native" \
    &

PULSE_PID=$!

# Wait for unix socket to appear (max 15s)
MAX_WAIT=15
COUNT=0
while [ ! -S /tmp/pulse/native ]; do
    if [ $COUNT -ge $MAX_WAIT ]; then
        echo "[Entrypoint] ERROR: PulseAudio socket not ready after ${MAX_WAIT}s"
        break
    fi
    echo "[Entrypoint] Waiting for PulseAudio socket... (${COUNT}/${MAX_WAIT})"
    sleep 1
    COUNT=$((COUNT + 1))
done

if [ -S /tmp/pulse/native ]; then
    echo "[Entrypoint] PulseAudio socket ready."
    pactl -s unix:/tmp/pulse/native set-default-sink VirtualSink 2>/dev/null && \
    pactl -s unix:/tmp/pulse/native set-default-source VirtualMic 2>/dev/null && \
    echo "[Entrypoint] VirtualSink + VirtualMic set as defaults." || \
    echo "[Entrypoint] WARNING: Could not set default sink/source."
else
    echo "[Entrypoint] WARNING: PulseAudio socket not found — audio capture will not work."
fi

echo "[Entrypoint] Starting Uvicorn..."
exec uvicorn services.copilot.src.main:app \
    --host 0.0.0.0 \
    --port "${COPILOT_PORT:-8001}"
