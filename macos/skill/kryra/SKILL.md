---
name: kryra
description: Query Q101 or MQ80 keyboard battery and send short lighting notifications through the local Kryra macOS app. Supports solid, blink, breathe, rainbow, nine-segment static colors, and restoring original lighting.
---

# Kryra

Use the bundled native `scripts/kryractl` from this skill directory. It discovers the running app's loopback API and reads its private credential locally; do not print or copy the token. No Python, package installation, direct HID access, or cloud service is needed.

Examples (resolve `scripts/kryractl` relative to this skill's actual directory):

```sh
scripts/kryractl battery
scripts/kryractl battery --refresh
scripts/kryractl status
scripts/kryractl solid 0080FF
scripts/kryractl solid 34D399 --brightness 25 --seconds 3
scripts/kryractl blink FFAA33 --period 1000 --seconds 3
scripts/kryractl breathe 34D399 --period 1000 --seconds 3
scripts/kryractl rainbow --period 5000 --seconds 5
scripts/kryractl frame FF0000 FF8000 FFFF00 80FF00 00FF00 00FF80 00FFFF 0080FF 8000FF
scripts/kryractl release
```

If the app is not running, run `scripts/kryractl launch`, then retry once. If no device responds, report the connection error and ask the user to connect USB or the 2.4G receiver, select the matching keyboard mode, or press a key to wake it. Never fabricate a percentage from an unavailable or stale sample; include the cache age when relevant.

Kryra v1.1.0 uses firmware-local solid, blink, breathe and rainbow effects, requiring firmware v3.1.0 or a compatible firmware advertising local effects. Each notification sends one PLAY command with all parameters. Do not build animation loops, renew the notification, or poll during playback. The app refuses older firmware instead of streaming RGB as a fallback. An effect request that times out may already have been accepted: report the uncertain result rather than repeatedly restarting it.

Colors use six hexadecimal digits. Brightness is 0–100 (default 25); `--period` is 500–20000 milliseconds (default 1000 for blink/breathe, 5000 for rainbow). Duration is 0.1–60 seconds, default five. Prefer a single dim three-to-five-second notification for status reminders. There is no unlimited/continuous mode. Firmware finishes the effect independently if the app stops sending. A new notification replaces the old one; `release` cancels immediately. Local effects yield to the keyboard's original sleep policy and can end early when it sleeps; waking does not replay them. They do not remotely wake a sleeping keyboard. Do not claim measured battery-life improvements.

`frame` retains nine independent static colors for 0.5–10 seconds with no heartbeat, including on older side-HID firmware. This legacy command holds wireless idle reception until release or expiry. Battery/status queries do not acquire or extend either kind of lighting control. Battery values are the firmware's cached estimates, not a fresh raw ADC or capacity measurement.

The app serializes UI and API commands. Read the returned JSON to confirm success. `demo: true` denotes simulated results and must never be described as physical-device confirmation. This skill controls lighting and reads cached firmware battery data; it does not flash firmware or edit VIA settings.
