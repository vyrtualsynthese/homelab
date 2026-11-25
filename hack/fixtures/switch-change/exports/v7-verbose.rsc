# 2026-11-25 09:14:02 by RouterOS 7.16.1
#
/interface bridge
add name=bridge mtu=1500 auto-mac=yes protocol-mode=rstp
/interface bridge port
add bridge=bridge interface=sfp-sfpplus1 pvid=10 hw=yes auto-isolate=no
add bridge=bridge interface=sfp-sfpplus2 pvid=1 hw=yes auto-isolate=no comment="a \"b\" c=d"
/ip address
add address=192.168.1.11/24 interface=bridge network=192.168.1.0 actual-interface=bridge
add address=192.168.1.12/24 interface=bridge network=192.168.1.0 actual-interface=bridge
/ip route
add dst-address=0.0.0.0/0 gateway=192.168.1.1 check-gateway=ping distance=1
