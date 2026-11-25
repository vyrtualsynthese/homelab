# 2026-11-25 09:14:02 by RouterOS 7.16.1
# software id = ABCD-1234
#
/interface bridge
add name=bridge
/interface bridge port
add bridge=bridge interface=sfp-sfpplus1 disabled=yes
add bridge=bridge interface=sfp-sfpplus2
