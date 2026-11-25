# 2026-11-25 09:14:02 by RouterOS 7.16.1
#
/interface bridge
add name=bridge mtu=1500 auto-mac=yes protocol-mode=rstp
/interface bridge port
add bridge=bridge disabled=yes interface=sfp-sfpplus1 pvid=1 hw=yes auto-isolate=no
add bridge=bridge interface=sfp-sfpplus2 pvid=1 hw=yes auto-isolate=no
