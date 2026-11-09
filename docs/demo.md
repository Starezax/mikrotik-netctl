# Defense demo script

Order chosen so every requirement of the assignment gets shown once. Commands are explicit so they can
go into the report as-is. Expected results are what we actually observed in GNS3.

## 0. Before the demo

- Start all nodes; re-run Alpine network commands (PCs, CONTROLLER, PC-D) — see `lab-setup.md`.
- CONTROLLER: `cd /root/netctl` and `python3 netctl.py status` — all 4 sites connected, handshakes fresh.

## 1. The network: secure, distributed, behind NAT

RELAY1:
```
/interface wireguard peers print detail
```
Expect: sites connect from 203.0.113.11/12/13/14 (ISP addresses), not from 100.64.x.x.

ISP-A:
```
/ip firewall connection print detail where protocol=udp
```
Expect: a connection from 100.64.1.2 with `s` (srcnat) and reply-dst-address 203.0.113.11.

Wireshark on ISP-A ↔ INTERNET while PC-A pings PC-B: only "WireGuard Transport Data" between public
addresses; no ICMP, no 10.10.x.x.

## 2. Fault tolerance

PC-A: `ping 10.10.2.10` (leave running). RELAY1: `/interface wireguard disable wg0`.
Expect ~20–30 s of loss, then replies again. hEX-A:
```
/ip route print where dst-address=10.10.2.0/24
```
Expect the backup route active (`A`). RELAY1: `/interface wireguard enable wg0`.
(With P2P enabled, A↔B traffic is direct and unaffected — disable P2P or test C↔D to show relay failover.)

## 3. Centralized management, configuration, drift repair

hEX-B, then CONTROLLER:
```
/ip route remove [find where dst-address=10.10.1.0/24 and gateway=10.102.0.1]
python3 netctl.py apply
```
Expect: `[hEX-B] + /ip/route dst-address=10.10.1.0/24 gateway=10.102.0.1`, then a second `apply` says
`everything already matches network.yaml`.

Rebuild a relay without copying keys — RELAY2, then CONTROLLER:
```
/interface wireguard peers remove [find]
python3 netctl.py apply
python3 netctl.py status
```

## 4. Provisioning

Show a fresh router joining (or the recorded output for hEX-D):
```
python3 enroll_server.py
python3 provision.py --password ROUTER_PASSWORD --token NEW-INVITE --name hEX-E
```
Expect on the controller: relays updated, then `[enroll] hEX-E is in the network`; a reused invite gets
`403 invalid or already used invite`.

## 5. Route optimization: relay by RTT

CONTROLLER:
```
python3 netctl.py optimize
python3 netctl.py watch 30
```
Move the GNS3 delay between relay links during `watch`. Expect `-> RELAY1 (was RELAY2)` and routes
switching; traceroute on PC-A follows (~164 ms → ~3 ms).

## 6. Route optimization: P2P and fallback

PC-A:
```
traceroute 10.10.2.10
```
Expect 3 hops: 10.10.1.1 → 10.100.0.12 → 10.10.2.10 (no relay).

PC-C and PC-A:
```
traceroute 10.10.4.10
```
Expect PC-C via a relay (ISP-C and ISP-D both closed), PC-A direct (ISP-A open).

CONTROLLER:
```
python3 netctl.py apply
```
Expect the P2P summary: every pair `direct` except `hEX-C <-> hEX-D: via relay (no handshake)`.

## Screenshots for the report

1. GNS3 topology.
2. Traceroute LAN→LAN through a relay (4 hops).
3. Route table before/after failover (`I` and `A` flags) + the ping gap.
4. RELAY1 peers with ISP addresses as endpoints.
5. ISP-A connection with srcnat translation.
6. Wireshark: only WireGuard UDP.
7. First `apply` (adoption), second `apply` (no changes), drift repair.
8. provision.py output + controller log for hEX-D; status with 4 sites.
9. `optimize` table and traceroute before/after; `watch` switching.
10. ISP-B conntrack with unreplied :13233 entries and port 46404 (why hole punching failed).
11. `apply` with 6 direct pairs; traceroute with 3 hops.
12. PC-C via relay vs PC-A direct; `apply` with C↔D via relay.

Conclusions worth stating: the data plane survives controller loss; reconcile makes configuration
repeatable and self-healing; RTT-based choice with hysteresis avoids flapping; P2P needs at least one
reachable side, otherwise traffic automatically stays on a relay.
