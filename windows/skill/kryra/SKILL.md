---
name: kryra
description: Query Q101 or MQ80 keyboard battery and send short USB or 2.4G lighting reminders through Kryra Windows with firmware 3.1.0. Use for battery, solid, blink, breathe, rainbow, and restoring original lighting; not firmware flashing or VIA keymaps.
---

# Kryra Windows

Use the native CLI identified by `app.json` beside this SKILL.md. The binding contains only the application path and runtime directory; the CLI reads its private local API credential itself. Do not print or copy the token. No Python, PowerShell script installation, direct HID access, or cloud service is needed.

Resolve `$kryraSkillDirectory` to this skill's actual directory, then load the binding in PowerShell:

```powershell
$kryraBinding = Get-Content -LiteralPath (Join-Path $kryraSkillDirectory 'app.json') -Raw | ConvertFrom-Json
$env:KRYRA_HOME = $kryraBinding.runtime_home
& $kryraBinding.cli_path battery
& $kryraBinding.cli_path battery --refresh
& $kryraBinding.cli_path status
& $kryraBinding.cli_path settings --poll-interval 60
& $kryraBinding.cli_path solid 34D399 --brightness 25 --seconds 3
& $kryraBinding.cli_path blink 38BDF8 --period 1000 --seconds 3
& $kryraBinding.cli_path breathe C084FC --period 2000 --seconds 3
& $kryraBinding.cli_path rainbow --period 5000 --seconds 5
& $kryraBinding.cli_path release
```

If the CLI path no longer exists, ask the user to update the skill from Kryra's settings after moving the application. If the app is stopped, run `launch` through the same CLI, then retry the original read once. An effect timeout may mean the keyboard accepted it: report the uncertainty rather than repeatedly restarting the effect. For no reply, report the connection error and suggest the matching USB/2.4G mode and waking the keyboard.

Normal `battery` reads the cache; use `--refresh` only when a fresh query is needed. Include staleness when reporting a cached or disconnected sample. The firmware percentage is a cached estimate, not a fresh raw ADC or calibrated capacity reading. Polling defaults to 60 seconds; settings accepts integers 30–3600 only when the user asks to change it. Query and preference commands do not acquire or extend lighting control.

Prefer one dim three-to-five-second reminder. The four effects send one PLAY with all parameters and run on the keyboard. Do not build animation loops, renew notifications, or poll during playback. Colors are six hex digits; brightness is 0–100 (default 25), duration 0.1–60 seconds (default 5), period 500–20000 ms (default 1000 for blink/breathe, 5000 for rainbow; solid uses zero). A new reminder replaces the previous one and `release` cancels it. Local effects yield to original wireless sleep, can end early, and do not replay or remotely wake the keyboard. Do not claim measured battery-life improvements.

Read the returned JSON to confirm success. `demo: true` means simulation, never physical-device confirmation. Kryra serializes UI and API requests and requires firmware 3.1.0. This skill reads battery data and controls lighting; it does not flash firmware, edit VIA settings, or install other software.
