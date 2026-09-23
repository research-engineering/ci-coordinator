set -eu
set -o pipefail
umask 022
mkdir -p /out /tmp/zlib-baseline
chmod 755 /out
curl --fail --location --proto '=https' --proto-redir '=https' --max-time 60 \
    --max-filesize 2097152 \
    https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz -o /tmp/zlib.tar.gz
printf '%s  %s\n' bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16 \
    /tmp/zlib.tar.gz | sha256sum -c -
tar -xzf /tmp/zlib.tar.gz -C /tmp/zlib-baseline --strip-components=1
(cd /tmp/zlib-baseline && ./configure --shared --disable-crcvx && make -j2)
cc -O2 -Wall -Wextra -Werror -I/tmp/zlib-baseline /security/check_zlib.c \
    -L/tmp/zlib-baseline -Wl,-rpath,/usr/lib/x86_64-linux-gnu \
    -Wl,--export-dynamic -lz -o /out/check_zlib
chmod 755 /out/check_zlib
baseline=0
LD_LIBRARY_PATH=/tmp/zlib-baseline timeout 10 /out/check_zlib \
    /tmp/zlib-baseline/libz.so.1.3.2 > /out/zlib-before.json || baseline=$?
test "$baseline" -eq 10

baseline=0
timeout 10 python -I -B /security/check_tarfile.py > /out/python-before.json || baseline=$?
test "$baseline" -eq 10
timeout 30 python -I -B /security/check_stdlib.py before > /out/stdlib-before.json
python -I -B /security/apply_stdlib.py
timeout 10 python -I -B /security/check_tarfile.py > /out/python-after.json
timeout 30 python -I -B /security/check_stdlib.py after > /out/stdlib-after.json

mkdir -p /tmp/zlib-repaired /tmp/zlib-package/DEBIAN
tar -xzf /tmp/zlib.tar.gz -C /tmp/zlib-repaired --strip-components=1
(
    cd /tmp/zlib-repaired
    patch --batch --fuzz=0 -p1 < /security/zlib.patch
    ./configure --prefix=/usr --shared --disable-crcvx
    make -j2
    make check
    LD_LIBRARY_PATH="$PWD" timeout 10 /out/check_zlib \
        "$PWD/libz.so.1.3.2" > /out/zlib-after.json
)
install -Dm755 /tmp/zlib-repaired/libz.so.1.3.2 \
    /tmp/zlib-package/usr/lib/x86_64-linux-gnu/libz.so.1.3.2
ln -s libz.so.1.3.2 /tmp/zlib-package/usr/lib/x86_64-linux-gnu/libz.so.1
install -Dm644 /tmp/zlib-repaired/LICENSE /tmp/zlib-package/usr/share/doc/zlib1g/copyright
cp /security/zlib-control /tmp/zlib-package/DEBIAN/control
dpkg-deb --build --root-owner-group /tmp/zlib-package /out/zlib1g.deb
test -s /out/zlib-after.json
