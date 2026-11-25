# 2026-11-25 09:14:02 by RouterOS 7.16.1
# software id = ABCD-1234
#
# model = CRS326-24G-2S+
/interface bridge
add name=bridge
/interface bridge port
add bridge=bridge interface=sfp-sfpplus1 pvid=10
add bridge=bridge interface=sfp-sfpplus2 comment="a \"b\" c=d"
/ip address
add address=192.168.1.11/24 interface=bridge network=192.168.1.0
add address=192.168.1.12/24 interface=bridge network=192.168.1.0
/ip route
add dst-address=0.0.0.0/0 gateway=192.168.1.1 \
    check-gateway=ping
