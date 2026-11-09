# Troubleshooting — problems we actually hit

| Symptom | Cause | Fix |
|---|---|---|
| Commands seem to run, but `/interface wireguard print` is empty | Multi-line paste into the RouterOS console in GNS3 drops lines | Paste one command at a time, or use SSH |
| `/interface wireguard peers print detail` empty | `peers add` was rejected because the key placeholder wasn't replaced (`invalid public key`) | Re-add with the real `public-key` from the other side |
| No handshake, `tx` grows, `rx=0` | Wrong key (own key instead of the peer's, or wg-r1/wg-r2 swapped) | Compare `/interface wireguard print` on one side with the peer on the other |
| `/ip firewall connection print where dst-address~"…"` is empty on the ISP | ICMP entries expire in ~10 s; or no WireGuard traffic at all | Run a long ping while checking; check the site's peers |
| PC can't ping its router | LAN address missing on the router (`/ip address print`) | `/ip address add address=192.168.88.1/24 interface=ether2` |
| `traceroute` shows `* * *` for middle hops (PC-D) | Alpine `rp_filter`: replies from 10.101/10.102 arrive on eth0, but the default route is eth1 | `ip route add 10.101.0.0/24 via 10.10.4.1` and the same for 10.102.0.0/24 |
| P2P: all pairs `via relay (no handshake)` | NAT conntrack collision on simultaneous hole punching (port changes, e.g. 13233 → 46404) | Port mapping (dst-nat UDP 13233) on "open" ISPs + clear :13233 connections |
| A removed port mapping, but P2P still works | The established tunnel's conntrack entries stay valid while keepalives flow | `/ip firewall connection remove [find where dst-address~":13233"]` and `src-address~":13233"` |
| `git push`: `Password authentication is not supported` | GitHub needs a token or SSH key | SSH key (`ssh-keygen`, add `.pub` to GitHub) or token in the remote URL |
| `git push`: `Permission denied (publickey)` | Remote switched to SSH without a key | Add an SSH key, check with `ssh -T git@github.com` |
| Can't change adapters on a node | GNS3 allows it only on a stopped node | Stop only that node; others keep running |
| Second link to the NAT node not possible | One GNS3 NAT node takes one link | Add another NAT node (NAT-D) |
| `optimize` shows `-` for one site/relay once | All 3 pings of one sample lost (jitter on a loaded host) | Rerun; TODO: require two failed samples before calling a relay down |
| RTT 95–135 ms with a 40 ms delay filter | Filter delays both directions (≈80 ms) + jitter from many VMs | Expected; doesn't change relay choice |
| Alpine network settings gone after restart | Live image / no `/etc/network/interfaces` | Re-run the `ip` commands, or persist them (see `lab-setup.md`) |
| `apply` fails: site not reachable | Controller routes to 10.101/10.102 missing after controller reboot, or mgmt NAT missing on the relay | Re-add `ip route add 10.101.0.0/24 via 203.0.113.101` etc.; `/ip firewall nat print` on relays |
