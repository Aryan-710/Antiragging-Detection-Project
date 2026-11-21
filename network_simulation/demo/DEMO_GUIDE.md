# How to show the demo (simple version)

## Easiest: one file, no install
Double-click `network_simulation/demo/alert_demo.html`. It opens in Chrome/Edge/Firefox. Press **Send critical alert**. That's the whole demo.
(It needs no internet, no Python, no ns-3.)

## Inside your app (alert pops up automatically when a report is CRITICAL)
Open a terminal **in the project folder** (Windows: open the folder in File Explorer -> type `cmd` in the address bar -> Enter; or use the terminal inside VS Code).
```
git checkout network-simulation
pip install -r requirements.txt
streamlit run safecampus_app.py
```
Paste a severe message, press Analyze. When the score is 75% or more, the animated panel appears under the report.

## What the screen shows
Four networks carry the same alert to the security desk: SafeVoice phone mesh with priority, mesh without priority, normal campus Wi-Fi, Wi-Fi with a congested uplink.
Every click replays a real recorded outcome from the simulation (delivered / lost, hops, delay). The four buttons at the top change how busy the network is.

## What to say
Provisioned Wi-Fi is the best; priority did not beat the plain mesh; the phone mesh keeps working when the Wi-Fi uplink is overloaded, so SafeVoice proposes it as a backup path.

## Not needed for the demo
Re-running the simulator itself (ns-3) is only for proving the numbers; it needs Linux (see the paper/README). The 208 simulation runs are already stored in `results/`.
