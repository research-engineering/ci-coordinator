set -eu
set -o pipefail
test "$(dpkg --print-architecture)" = amd64

mkdir -p /runtime/usr/lib /runtime/usr/lib64 /runtime/app/backend
ln -s usr/lib /runtime/lib
ln -s usr/lib64 /runtime/lib64
cp -a /usr/local /runtime/usr/local
cp -a /app/backend/.venv /runtime/app/backend/.venv
cp -a /repairs/stdlib/. /runtime/usr/local/lib/python3.13/
find /repairs/stdlib -name '*.py' | while IFS= read -r repaired; do
    relative="${repaired#/repairs/stdlib/}"
    directory="$(dirname "$relative")"
    name="$(basename "$relative" .py)"
    find "/runtime/usr/local/lib/python3.13/$directory/__pycache__" \
        -maxdepth 1 -name "$name.*.pyc" -delete
done
rm -rf /runtime/usr/local/include /runtime/usr/local/lib/pkgconfig \
    /runtime/usr/local/share/man /runtime/app/backend/uv.lock \
    /runtime/usr/local/lib/python3.13/ensurepip
find /runtime/usr/local/lib -maxdepth 2 -type f -name 'libpython*.a' -delete
find /runtime/usr/local/lib -maxdepth 2 -type d -name 'config-*-linux-*' -exec rm -rf '{}' +
find /runtime/usr/local/bin -maxdepth 1 \
    \( -name 'pip*' -o -name '*-config' -o -name 'idle*' -o -name 'pydoc*' \) -delete
find /runtime/usr/local/lib/python3.13/site-packages -mindepth 1 -maxdepth 1 \
    \( -name 'pip' -o -name 'pip-*.dist-info' \) -exec rm -rf '{}' +
rm -rf /runtime/usr/local/lib/python3.13/tkinter \
    /runtime/usr/local/lib/python3.13/idlelib \
    /runtime/usr/local/lib/python3.13/turtledemo
rm -f /runtime/usr/local/lib/python3.13/turtle.py
find /runtime/usr/local/lib/python3.13/lib-dynload -maxdepth 1 \
    -name '_tkinter.*.so' -delete

dpkg-deb --extract /repairs/zlib1g.deb /runtime
scanelf --recursive --nobanner --format '%F' \
    /runtime/usr/local /runtime/app/backend/.venv > /tmp/runtime-elf.txt
test -s /tmp/runtime-elf.txt
set -- /usr/lib/x86_64-linux-gnu/libgcc_s.so.1
while IFS= read -r elf; do
    set -- "$@" "${elf#/runtime}"
done < /tmp/runtime-elf.txt
/usr/bin/python3 /usr/bin/lddtree --list "$@" | sort -u > /tmp/runtime-library-paths.txt
if grep -v '^/' /tmp/runtime-library-paths.txt; then exit 1; fi
/usr/bin/python3 /usr/bin/lddtree --copy-to-tree /runtime "$@"

mkdir -p /runtime/etc /runtime/usr/share /runtime/usr/lib/locale
cp -a /etc/ssl /runtime/etc/
cp -a /usr/share/ca-certificates /usr/share/zoneinfo /runtime/usr/share/
cp -a /usr/lib/locale/C.utf8 /runtime/usr/lib/locale/
cp /usr/lib/os-release /runtime/etc/os-release
printf '/usr/local/lib\n/usr/lib/x86_64-linux-gnu\n/lib/x86_64-linux-gnu\n' \
    > /runtime/etc/ld.so.conf
printf 'passwd: files\ngroup: files\nhosts: files dns\n' > /runtime/etc/nsswitch.conf
ldconfig -r /runtime
python -I -B /tmp/package_metadata.py
while IFS= read -r elf; do
    chroot /runtime /lib64/ld-linux-x86-64.so.2 --list \
        --preload /usr/local/lib/libpython3.13.so.1.0 "${elf#/runtime}" \
        >> /tmp/runtime-loader.txt
done < /tmp/runtime-elf.txt
find /repairs/stdlib -name '*.py' | while IFS= read -r repaired; do
    chroot /runtime /usr/local/bin/python3.13 -I -B -m compileall \
        --invalidation-mode checked-hash -q "/usr/local/lib/python3.13/${repaired#/repairs/stdlib/}"
done

mkdir -p /runtime/home/ci-coordinator /runtime/tmp /runtime/usr/share/ci-coordinator
printf 'root:x:0:0:root:/root:/nonexistent\nci-coordinator:x:10001:10001::/home/ci-coordinator:/nonexistent\n' \
    > /runtime/etc/passwd
printf 'root:x:0:\nci-coordinator:x:10001:\n' > /runtime/etc/group
chmod 1777 /runtime/tmp
chown 10001:10001 /runtime/home/ci-coordinator
cp /tmp/runtime-library-paths.txt /runtime/usr/share/ci-coordinator/library-paths.txt
cp /etc/apt/apt.conf.d/50snapshot /runtime/usr/share/ci-coordinator/ubuntu-snapshot.conf
test -s /runtime/var/lib/dpkg/status
test -s /runtime/etc/ssl/certs/ca-certificates.crt
test ! -e /runtime/bin/sh
test ! -e /runtime/usr/bin/dpkg
mkdir -p /dependency-layer/app/backend
mv /runtime/app/backend/.venv /dependency-layer/app/backend/.venv
