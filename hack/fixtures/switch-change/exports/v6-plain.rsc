# 2026-11-25 09:14:02 by RouterOS 6.49.10
# software id = WXYZ-5678
#
/interface bridge
add admin-mac=AA:BB:CC:DD:EE:FF auto-mac=no name=bridge
/interface ethernet
set [ find default-name=sfp-sfpplus1 ] name=sfp-sfpplus1
/ip address
add address=192.168.1.43/24 interface=bridge network=192.168.1.0
