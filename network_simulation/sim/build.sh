#!/usr/bin/env bash
# Compile safevoice-sim.cc against a prebuilt ns-3 (here: ns-3.44 from the PyPI "ns3" wheel).
# Usage: NS3_ROOT=/path/to/extracted/ns3 ./build.sh
set -euo pipefail
NS3_ROOT="${NS3_ROOT:-/home/claude/work/ns3w/ns3}"
VER="${NS3_VER:-3.44}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LIBS=""
for m in core network internet wifi mobility aodv dsdv applications propagation traffic-control \
         flow-monitor csma bridge spectrum antenna point-to-point energy stats; do
  LIBS="$LIBS -lns${VER}-$m"
done
# NOTE: do NOT add -I$NS3_ROOT/include/ns3 (its headers shadow libstdc++ <cstring> etc.)
g++ -std=c++20 -O2 -I"$NS3_ROOT/include" "$HERE/safevoice-sim.cc" -o "$HERE/safevoice-sim.new" \
    -L"$NS3_ROOT/lib64" $LIBS -Wl,-rpath,"$NS3_ROOT/lib64"
mv -f "$HERE/safevoice-sim.new" "$HERE/safevoice-sim"   # atomic replace
echo "built $HERE/safevoice-sim"
