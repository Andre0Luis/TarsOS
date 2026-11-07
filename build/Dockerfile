FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      git python3 openssl ca-certificates sudo util-linux kpartx procps \
      xz-utils curl less nano \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth=1 https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git /opt/pmbootstrap \
 && ln -s /opt/pmbootstrap/pmbootstrap.py /usr/local/bin/pmbootstrap
RUN useradd -m -s /bin/bash pmos && echo 'pmos ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/pmos
USER pmos
WORKDIR /home/pmos
CMD ["bash"]
