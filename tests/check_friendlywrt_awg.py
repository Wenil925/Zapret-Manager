"""Run the real AWG installer functions against a disposable filesystem and fake commands."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[1]
source = (repo / 'ZapretManager_LuCI.sh').read_text(encoding='utf-8')
functions = source[source.index('_st_awg_loaded()'):source.index('_st_warp_field()')]
bash = os.environ.get('TEST_SHELL', 'bash')

def posix(path):
    value = path.as_posix()
    return '/' + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ':' else value

mocks = r'''
uname() { echo "${TEST_KERNEL:-6.1.141}"; }
_awg_board() { echo "${TEST_BOARD:-friendlyelec,nanopi-r3s}"; }
_awg_rel_ver() { echo "${TEST_RELEASE:-25.12.5}"; }
_awg_rel_arch() { echo aarch64_generic; }
_awg_rel_target() { echo rockchip/armv8; }
sha256sum() {
    local expected path actual
    if [ "$1 $2" != '-c -' ]; then command sha256sum "$@"; return; fi
    while read -r expected path; do
        case "$path" in */configs.ko|*/udp_tunnel.ko) [ "${TEST_FIRMWARE:-ok}" = ok ] || return 1 ;;
        *) actual=$(command sha256sum "$path") || return 1; [ "${actual%% *}" = "$expected" ] || return 1 ;;
        esac
    done
}
_rb_say() { echo "$*"; }
_rb_warn() { echo "$*"; }
_rb_fetch_pkg() {
    echo "vendor $1" >> "$TEST_ROOT/downloads"
    [ "${TEST_DOWNLOAD:-ok}" != failed ] || return 1
    if [ "${TEST_DOWNLOAD:-ok}" = corrupt ]; then echo invalid > "$2"; else cp "$TEST_MODULE" "$2"; fi
}
_pkg_is_installed() { [ -f "$TEST_ROOT/pkg/$1" ]; }
_awg_pkg_ver() { [ ! -s "$AWG_KMOD_MANUAL" ] || awk '{print $1; exit}' "$AWG_KMOD_MANUAL"; }
_awg_fetch() { echo "stock $2" >> "$TEST_ROOT/downloads"; echo package > "$ST_RUN/$2.apk"; echo "$ST_RUN/$2.apk"; }
_awg_legacy_feeds() { :; }
_awg_kmod_pin_bad() { return 1; }
_awg_proto_ok() { return 0; }
_rb_rpcd_ensure() { :; }
_st_own() { echo "$*" >> "$TEST_ROOT/owned"; }
test_update() { :; }
test_install() {
    local name
    name=${1##*/}; name=${name%.apk}
    touch "$TEST_ROOT/pkg/$name"
}
test_delete() { rm -f "$TEST_ROOT/pkg/$1"; }
_zm_run() { shift; "$@"; }
apk() {
    [ "${TEST_APK:-ok}" = ok ] || return 1
    touch "$TEST_ROOT/pkg/kmod-amneziawg"
}
modprobe() { mkdir -p "$TEST_ROOT/sys/module/amneziawg"; }
insmod() {
    [ -f "$1" ] || return 1
    echo "${1##*/}" >> "$TEST_ROOT/loads"
    [ "${1##*/}" != amneziawg.ko ] || mkdir -p "$TEST_ROOT/sys/module/amneziawg"
}
rmmod() { return 1; }
awg() { :; }
_test_init() {
    case "$1" in
        enabled) [ -f "$TEST_ROOT/enabled" ] ;;
        enable) touch "$TEST_ROOT/enabled" ;;
        disable) rm -f "$TEST_ROOT/enabled" ;;
        start) ( . "$AWG_VENDOR_INIT"; start ) ;;
    esac
}
PKG=apk; INSTALL=test_install; DELETE=test_delete; UPDATE=test_update; RAZ=apk
ST_RUN="$TEST_ROOT/run"; ST_AWG_MIRRORS=https://stock.invalid; ST_AWG_MIRROR_FLAT=https://stock.invalid
'''

def run_case(name, body, **overrides):
    temporary = repo.parent / 'zm-awg-tests'
    temporary.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='case-', dir=temporary) as folder:
        root = Path(folder)
        for directory in ['pkg', 'run', 'etc/init.d', 'etc/rc.d', 'etc/zm-awg', 'sys/module', 'proc', 'lib/modules/6.1.141']:
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / 'proc/modules').touch()
        for filename in ['configs', 'udp_tunnel', 'poly1305-neon', 'libcurve25519-generic', 'ip6_udp_tunnel', 'libchacha20poly1305']:
            (root / f'lib/modules/6.1.141/{filename}.ko').touch()
        mapped = functions
        for prefix in ['/etc/', '/lib/', '/sys/', '/proc/']:
            mapped = mapped.replace(prefix, posix(root) + prefix)
        mapped = mapped.replace('"$AWG_VENDOR_INIT" enable', '_test_init enable').replace('"$AWG_VENDOR_INIT" enabled', '_test_init enabled')
        mapped = mapped.replace('"$AWG_VENDOR_INIT" disable', '_test_init disable').replace('"$AWG_VENDOR_INIT" start', '_test_init start')
        env = dict(os.environ, TEST_ROOT=posix(root), TEST_MODULE=posix(repo / 'vendor/awg/r3s-fw20261013/amneziawg.ko'), **overrides)
        script = mapped + '\n' + mocks + '\nset -e\n' + body
        result = subprocess.run([bash, '-s'], input=script.encode('utf-8'), env=env, capture_output=True)
        assert result.returncode == 0, f'{name}:\n{result.stdout.decode("utf-8", errors="replace")}\n{result.stderr.decode("utf-8", errors="replace")}'
        print(f'PASS {name}')

run_case('native install, reboot loader and pinned reinstall', r'''
_st_install_awg install
_awg_vendor_current
[ "$(cat "$TEST_ROOT/loads" | tr '\n' ' ')" = 'poly1305-neon.ko libcurve25519-generic.ko udp_tunnel.ko ip6_udp_tunnel.ko libchacha20poly1305.ko amneziawg.ko ' ]
[ "$(grep -c '^stock kmod' "$TEST_ROOT/downloads")" = 0 ]
[ -f "$TEST_ROOT/pkg/amneziawg-tools" ] && [ -f "$TEST_ROOT/pkg/luci-proto-amneziawg" ]
_st_install_awg update
_awg_vendor_current
[ "$(grep -c '^vendor ' "$TEST_ROOT/downloads")" = 2 ]
before=$(wc -l < "$TEST_ROOT/loads")
TEST_KERNEL=6.12.94
if _test_init start; then exit 1; fi
[ "$(wc -l < "$TEST_ROOT/loads")" = "$before" ]
TEST_KERNEL=6.1.141
_awg_kmod_manual_rm
[ ! -f "$AWG_KMOD_MANUAL" ] && [ ! -e "$AWG_VENDOR_INIT" ] && [ ! -f "$TEST_ROOT/enabled" ]
''')

for state in ['failed', 'corrupt']:
    run_case(f'{state} download fails without stock fallback', r'''
if _st_install_awg install; then exit 1; fi
[ ! -f "$TEST_ROOT/lib/modules/6.1.141/amneziawg.ko" ]
[ ! -f "$AWG_KMOD_MANUAL" ] && [ ! -e "$AWG_VENDOR_INIT" ]
[ "$(grep -c '^stock ' "$TEST_ROOT/downloads")" = 0 ]
''', TEST_DOWNLOAD=state)

run_case('different firmware rejected before download', r'''
if _st_install_awg install; then exit 1; fi
[ ! -f "$TEST_ROOT/downloads" ]
''', TEST_FIRMWARE='changed')

run_case('APK failure restores old module and metadata', r'''
echo previous > "$TEST_ROOT/lib/modules/6.1.141/amneziawg.ko"
echo old-marker > "$AWG_KMOD_MANUAL"
printf '#!/bin/sh\n# old-init\n' > "$AWG_VENDOR_INIT"; chmod +x "$AWG_VENDOR_INIT"; touch "$TEST_ROOT/enabled"
if _awg_vendor_install; then exit 1; fi
[ "$(cat "$TEST_ROOT/lib/modules/6.1.141/amneziawg.ko")" = previous ]
[ "$(cat "$AWG_KMOD_MANUAL")" = old-marker ]
[ "$(tail -n 1 "$AWG_VENDOR_INIT")" = '# old-init' ] && [ -f "$TEST_ROOT/enabled" ]
''', TEST_APK='failed')

run_case('official firmware keeps upstream package path', r'''
_st_install_awg install
[ "$(grep -c '^vendor ' "$TEST_ROOT/downloads")" = 0 ]
[ "$(grep -c '^stock kmod-amneziawg' "$TEST_ROOT/downloads")" = 1 ]
[ ! -e "$AWG_VENDOR_INIT" ]
''', TEST_KERNEL='6.12.94')

backend = source.split("<< 'ZM_INSTALLER_EOF'\n", 1)[1].split('\nZM_INSTALLER_EOF\n', 1)[0]
with tempfile.NamedTemporaryFile(suffix='.sh', mode='w', encoding='utf-8', delete=False, dir=repo.parent / 'zm-awg-tests') as stream:
    stream.write(backend)
    script_path = Path(stream.name)
try:
    subprocess.run([bash, '-n', str(script_path)], check=True)
finally:
    script_path.unlink()
print('PASS generated backend shell syntax')
