FROM --platform=linux/amd64 debian:12@sha256:f9c4778ec75d6b054367315b1eb4f3992ee5866b50de6d591a33e8904cba3f17

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

# Tools only: prompts, official tests, references and submissions are runtime mounts.
RUN mkdir -p /opt/provenance && \
    (apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl bash python3 python3-yaml git make gcc g++ patch \
        build-essential autoconf gperf flex bison verilator tini procps) \
        2>&1 | tee /opt/provenance/apt-build.log

# Stage the ARCA files with prepare_verilogeval_build.py on an office-network host.
# Equivalent to the official ARCA installers, with every downloaded byte checked.
# Disable the build API's automatic feature injection when using this Dockerfile.
COPY arca/ /tmp/arca/
RUN set -eux; \
    cp /tmp/arca/*.crt /usr/local/share/ca-certificates/; \
    echo 'b5cab49196cae130d135ac737db881675aaf8d9178bfbf29c640d9136c7b0cd0  /usr/local/share/ca-certificates/autocert_swg_ca.crt' | sha256sum -c -; \
    echo '4382cb2df1b57872b03779dbab0781f3c26af52684f4f6957ffd4c55369ac70e  /usr/local/share/ca-certificates/nautiles_swg_ca.crt' | sha256sum -c -; \
    update-ca-certificates; \
    useradd --create-home --shell /bin/bash admin; \
    install -d -o admin -g admin /home/admin/bin /home/admin/logs /workspace; \
    cp /tmp/arca/arca_envd /home/admin/bin/arca_envd; \
    echo '47fd9c0b446597f855de653e43ec5d1adf412a303c62d0b9176a0e55eed3e514  /home/admin/bin/arca_envd' | sha256sum -c -; \
    chmod 755 /home/admin/bin/arca_envd; \
    chown admin:admin /home/admin/bin/arca_envd; \
    sha256sum /home/admin/bin/arca_envd /usr/local/share/ca-certificates/* > /opt/provenance/arca-components.sha256; \
    rm -rf /tmp/arca

ENV ARCA_SANDBOX_SERVICE_ENABLED=true \
    ARCA_EGRESS_GW_CA_ENABLED=true \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    PYTHONNOUSERSITE=1

RUN set -eux; \
    curl -fSL --retry 3 https://codeload.github.com/steveicarus/iverilog/tar.gz/refs/tags/v12_0 -o /tmp/iverilog.tar.gz; \
    echo 'a68cb1ef7c017ef090ebedb2bc3e39ef90ecc70a3400afb4aa94303bc3beaa7d  /tmp/iverilog.tar.gz' | tee /opt/provenance/iverilog-source.sha256 | sha256sum -c -; \
    mkdir /tmp/iverilog; \
    tar -xzf /tmp/iverilog.tar.gz --strip-components=1 -C /tmp/iverilog; \
    cd /tmp/iverilog; \
    (sh autoconf.sh && ./configure --prefix=/usr/local && make -j8 && make install) \
        2>&1 | tee /opt/provenance/iverilog-build.log; \
    cd /; rm -rf /tmp/iverilog /tmp/iverilog.tar.gz /var/lib/apt/lists/*

RUN set -eux; \
    test "$(dpkg --print-architecture)" = amd64; \
    . /etc/os-release; test "$VERSION_ID" = 12; \
    python3 -c 'import sys; assert sys.version_info[:2] == (3, 11)'; \
    iverilog -V 2>&1 | tee /opt/provenance/iverilog-version.txt; \
    grep -q '^Icarus Verilog version 12.0' /opt/provenance/iverilog-version.txt; \
    verilator --version | tee /opt/provenance/verilator-version.txt; \
    grep -q '^Verilator 5.006' /opt/provenance/verilator-version.txt; \
    python3 --version > /opt/provenance/python-version.txt; \
    cp /etc/os-release /opt/provenance/os-release; \
    dpkg-query -W > /opt/provenance/packages.tsv

LABEL org.opencontainers.image.title="VerilogEval v2 tools" \
      org.opencontainers.image.description="Debian 12, Python 3.11, Icarus 12.0; benchmark data mounted at runtime"
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
