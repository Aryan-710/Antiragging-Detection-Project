# How to show the demo

## Level 1 - one file, no install (backup)
Double-click `network_simulation/demo/alert_demo.html`. Press **Send critical alert**. Replays recorded results of the 208-run study.

## Level 2 - full app, live ns-3 simulation on every CRITICAL alert (what to show the professors)
When a report scores >= 75%, the app runs the REAL ns-3 simulator for that alert (4 systems, same workload, ~15-70 s) and shows delivered/lost, delay, hops, then the animation.
It only works where the simulator is installed, i.e. **Linux** (Windows/Mac: through Docker). Streamlit Cloud cannot run it (no ns-3) -> it shows the Level 1 replay there.

### Option A - Docker (recommended on Windows/Mac)
1. Install **Docker Desktop** (Windows: enable WSL2 when asked). Start it.
2. Open a terminal in the project folder (Windows: open the folder in Explorer, type `cmd` in the address bar, Enter).
3. `git checkout network-simulation`
4. `docker build -t safevoice .`   (first time 10-30 min: downloads ns-3 and the NLP libraries, compiles the simulator)
5. `docker run --rm -p 8501:8501 safevoice`  then open http://localhost:8501
   (Apple-silicon Mac: add `--platform linux/amd64` to both commands; slower.)
6. Paste a severe message -> Analyze. In the sidebar "Run LIVE ns-3 simulation" should be ticked. Wait for the spinner (~30 s).
The NLP models download from Hugging Face on first run, so the machine needs internet the first time; do a trial run before the meeting.

### Option B - WSL2 Ubuntu 24.04 / native Linux, no Docker
```
sudo apt update && sudo apt install -y g++ python3-pip unzip
git clone https://github.com/Aryan-710/Antiragging-Detection-Project && cd Antiragging-Detection-Project && git checkout network-simulation
pip3 download ns3==3.44.post0 --no-deps -d /tmp/w && mkdir -p ~/ns3w && (cd ~/ns3w && unzip -q /tmp/w/ns3-*.whl 'ns3/*')
NS3_ROOT=~/ns3w/ns3 bash network_simulation/sim/build.sh
pip3 install -r requirements.txt
streamlit run safecampus_app.py
```
## What to say
Live run = ONE random seed, short window: it illustrates the mechanism. The statistics (208 runs, tests, limits) are in the paper. Honest result: provisioned Wi-Fi best; priority did not beat the plain mesh; the mesh works as a backup when the Wi-Fi uplink is overloaded.
