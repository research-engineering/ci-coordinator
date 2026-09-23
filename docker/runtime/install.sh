set -eu
test "$(dpkg --print-architecture)" = amd64
test -s /etc/ssl/certs/ca-certificates.crt
case "${1-}" in
    runtime)
        set -- ca-certificates tzdata libbz2-1.0 libdb5.3t64 libexpat1 libffi8 \
            libgdbm6t64 libgdbm-compat4t64 liblzma5 libncursesw6 libreadline8t64 \
            libsqlite3-0 libssl3t64 libuuid1 zlib1g libgcc-s1
        ;;
    security) set -- build-essential patch curl ;;
    assembly) set -- pax-utils ;;
    *) exit 2 ;;
esac
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "$@"
rm -rf /var/lib/apt/lists/*
