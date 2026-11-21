# SafeVoice app + live ns-3 simulation. Linux x86_64 image (the ns-3 wheel is cp312 manylinux x86_64).
#   docker build -t safevoice .
#   docker run --rm -p 8501:8501 safevoice        -> open http://localhost:8501
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PIP_BREAK_SYSTEM_PACKAGES=1
RUN apt-get update && apt-get install -y --no-install-recommends g++ python3 python3-pip unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# ns-3.44 headers + libraries from the PyPI wheel (same source used for the study)
RUN pip3 download ns3==3.44.post0 --no-deps -d /tmp/w && mkdir -p /opt/ns3w && cd /opt/ns3w && unzip -q /tmp/w/ns3-*.whl 'ns3/*' && rm -rf /tmp/w
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY . .
RUN NS3_ROOT=/opt/ns3w/ns3 bash network_simulation/sim/build.sh
EXPOSE 8501
CMD ["streamlit", "run", "safecampus_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
