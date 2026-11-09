# Lab setup: rebuilding the GNS3 lab from scratch

Every command, in the order the lab was actually built. Steps 1–7 build the network by hand;
steps 8–14 hand it over to the controller. The same content, per node, is in `lab/topology.html`.

Rules that save time:
- In the RouterOS console, **paste one command at a time** (multi-line paste drops lines).
- Alpine `ip addr` / `ip route` settings are lost on reboot; re-run them.
- Replace `RELAY1_PUB`, `A_R1_PUB` etc. with the real `public-key` values (steps 3–5 only).

## Step 1 — nodes and cables

| Node | GNS3 module | Adapters |
|---|---|---|
| INTERNET | Ethernet switch | 8 ports |
| RELAY1, RELAY2 | MikroTik CHR 7.19.4 | 1 |
| ISP-A, ISP-B, ISP-C, ISP-D | MikroTik CHR 7.19.4 | 2 |
| hEX-A, hEX-B, hEX-C, hEX-D | MikroTik CHR 7.19.4 | 2 |
| PC-A, PC-B, PC-C | Alpine Linux Virt 3.22.1 | 1 |
| PC-D | Alpine Linux Virt 3.22.1 | 2 |
| CONTROLLER | Alpine Linux Virt 3.22.1 | 2 |
| NAT, NAT-D | NAT | — |

```
INTERNET e0     <-> ISP-A ether1
INTERNET e1     <-> ISP-B ether1
INTERNET e2     <-> ISP-C ether1
INTERNET e3     <-> RELAY1 ether1
INTERNET e4     <-> RELAY2 ether1
INTERNET e5     <-> CONTROLLER eth0
INTERNET e6     <-> ISP-D ether1
CONTROLLER eth1 <-> NAT
ISP-A ether2    <-> hEX-A ether1
ISP-B ether2    <-> hEX-B ether1
ISP-C ether2    <-> hEX-C ether1
ISP-D ether2    <-> hEX-D ether1
hEX-A ether2    <-> PC-A eth0
hEX-B ether2    <-> PC-B eth0
hEX-C ether2    <-> PC-C eth0
hEX-D ether2    <-> PC-D eth0
PC-D eth1       <-> NAT-D
```

ISP-D, hEX-D, PC-D, NAT-D, CONTROLLER and NAT were added later (steps 8 and 11).

## Step 2 — providers ISP-A, ISP-B, ISP-C

ISP-A:
```
/system identity set name=ISP-A
/ip address add address=203.0.113.11/24 interface=ether1
/ip address add address=100.64.1.1/24 interface=ether2
/ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade
```
ISP-B:
```
/system identity set name=ISP-B
/ip address add address=203.0.113.12/24 interface=ether1
/ip address add address=100.64.2.1/24 interface=ether2
/ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade
```
ISP-C:
```
/system identity set name=ISP-C
/ip address add address=203.0.113.13/24 interface=ether1
/ip address add address=100.64.3.1/24 interface=ether2
/ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade
```

## Step 3 — relays: addresses and WireGuard keys

RELAY1:
```
/system identity set name=RELAY1
/ip address add address=203.0.113.101/24 interface=ether1
/interface wireguard add name=wg0 listen-port=51820
/ip address add address=10.101.0.1/24 interface=wg0
/interface wireguard print
```
RELAY2:
```
/system identity set name=RELAY2
/ip address add address=203.0.113.102/24 interface=ether1
/interface wireguard add name=wg0 listen-port=51820
/ip address add address=10.102.0.1/24 interface=wg0
/interface wireguard print
```
Write down each relay's `public-key` (RELAY1_PUB, RELAY2_PUB).

## Step 4 — site routers hEX-A, hEX-B, hEX-C (by hand)

hEX-A:
```
/system identity set name=hEX-A
/ip address add address=100.64.1.2/24 interface=ether1
/ip route add dst-address=0.0.0.0/0 gateway=100.64.1.1
/ip address add address=10.10.1.1/24 interface=ether2
/interface wireguard add name=wg-r1 listen-port=13231
/interface wireguard add name=wg-r2 listen-port=13232
/interface wireguard print
/ip address add address=10.101.0.11/24 interface=wg-r1
/ip address add address=10.102.0.11/24 interface=wg-r2
/interface wireguard peers add interface=wg-r1 public-key="RELAY1_PUB" endpoint-address=203.0.113.101 endpoint-port=51820 allowed-address=10.0.0.0/8 persistent-keepalive=25s
/interface wireguard peers add interface=wg-r2 public-key="RELAY2_PUB" endpoint-address=203.0.113.102 endpoint-port=51820 allowed-address=10.0.0.0/8 persistent-keepalive=25s
/ip route add dst-address=10.10.2.0/24 gateway=10.101.0.1 check-gateway=ping distance=1
/ip route add dst-address=10.10.2.0/24 gateway=10.102.0.1 check-gateway=ping distance=2
/ip route add dst-address=10.10.3.0/24 gateway=10.101.0.1 check-gateway=ping distance=1
/ip route add dst-address=10.10.3.0/24 gateway=10.102.0.1 check-gateway=ping distance=2
```
hEX-B:
```
/system identity set name=hEX-B
/ip address add address=100.64.2.2/24 interface=ether1
/ip route add dst-address=0.0.0.0/0 gateway=100.64.2.1
/ip address add address=10.10.2.1/24 interface=ether2
/interface wireguard add name=wg-r1 listen-port=13231
/interface wireguard add name=wg-r2 listen-port=13232
/interface wireguard print
/ip address add address=10.101.0.12/24 interface=wg-r1
/ip address add address=10.102.0.12/24 interface=wg-r2
/interface wireguard peers add interface=wg-r1 public-key="RELAY1_PUB" endpoint-address=203.0.113.101 endpoint-port=51820 allowed-address=10.0.0.0/8 persistent-keepalive=25s
/interface wireguard peers add interface=wg-r2 public-key="RELAY2_PUB" endpoint-address=203.0.113.102 endpoint-port=51820 allowed-address=10.0.0.0/8 persistent-keepalive=25s
/ip route add dst-address=10.10.1.0/24 gateway=10.101.0.1 check-gateway=ping distance=1
/ip route add dst-address=10.10.1.0/24 gateway=10.102.0.1 check-gateway=ping distance=2
/ip route add dst-address=10.10.3.0/24 gateway=10.101.0.1 check-gateway=ping distance=1
/ip route add dst-address=10.10.3.0/24 gateway=10.102.0.1 check-gateway=ping distance=2
```
hEX-C:
```
/system identity set name=hEX-C
/ip address add address=100.64.3.2/24 interface=ether1
/ip route add dst-address=0.0.0.0/0 gateway=100.64.3.1
/ip address add address=10.10.3.1/24 interface=ether2
/interface wireguard add name=wg-r1 listen-port=13231
/interface wireguard add name=wg-r2 listen-port=13232
/interface wireguard print
/ip address add address=10.101.0.13/24 interface=wg-r1
/ip address add address=10.102.0.13/24 interface=wg-r2
/interface wireguard peers add interface=wg-r1 public-key="RELAY1_PUB" endpoint-address=203.0.113.101 endpoint-port=51820 allowed-address=10.0.0.0/8 persistent-keepalive=25s
/interface wireguard peers add interface=wg-r2 public-key="RELAY2_PUB" endpoint-address=203.0.113.102 endpoint-port=51820 allowed-address=10.0.0.0/8 persistent-keepalive=25s
/ip route add dst-address=10.10.1.0/24 gateway=10.101.0.1 check-gateway=ping distance=1
/ip route add dst-address=10.10.1.0/24 gateway=10.102.0.1 check-gateway=ping distance=2
/ip route add dst-address=10.10.2.0/24 gateway=10.101.0.1 check-gateway=ping distance=1
/ip route add dst-address=10.10.2.0/24 gateway=10.102.0.1 check-gateway=ping distance=2
```
Write down each router's two public keys (A_R1_PUB, A_R2_PUB, …).

## Step 5 — relays: peers and LAN routes (by hand)

RELAY1:
```
/interface wireguard peers add interface=wg0 public-key="A_R1_PUB" allowed-address=10.101.0.11/32,10.10.1.0/24
/interface wireguard peers add interface=wg0 public-key="B_R1_PUB" allowed-address=10.101.0.12/32,10.10.2.0/24
/interface wireguard peers add interface=wg0 public-key="C_R1_PUB" allowed-address=10.101.0.13/32,10.10.3.0/24
/ip route add dst-address=10.10.1.0/24 gateway=10.101.0.11
/ip route add dst-address=10.10.2.0/24 gateway=10.101.0.12
/ip route add dst-address=10.10.3.0/24 gateway=10.101.0.13
```
RELAY2:
```
/interface wireguard peers add interface=wg0 public-key="A_R2_PUB" allowed-address=10.102.0.11/32,10.10.1.0/24
/interface wireguard peers add interface=wg0 public-key="B_R2_PUB" allowed-address=10.102.0.12/32,10.10.2.0/24
/interface wireguard peers add interface=wg0 public-key="C_R2_PUB" allowed-address=10.102.0.13/32,10.10.3.0/24
/ip route add dst-address=10.10.1.0/24 gateway=10.102.0.11
/ip route add dst-address=10.10.2.0/24 gateway=10.102.0.12
/ip route add dst-address=10.10.3.0/24 gateway=10.102.0.13
```

## Step 6 — PCs (Alpine)

PC-A:
```
ip link set eth0 up
ip addr add 10.10.1.10/24 dev eth0
ip route add default via 10.10.1.1
```
PC-B:
```
ip link set eth0 up
ip addr add 10.10.2.10/24 dev eth0
ip route add default via 10.10.2.1
```
PC-C:
```
ip link set eth0 up
ip addr add 10.10.3.10/24 dev eth0
ip route add default via 10.10.3.1
```

## Step 7 — check the hand-built network

hEX-A:
```
/ping 203.0.113.101 count=3
/interface wireguard peers print detail
/ping 10.101.0.1 count=3
/ping 10.102.0.1 count=3
/ip route print where dst-address=10.10.2.0/24
```
RELAY1 (endpoints must be the ISP addresses 203.0.113.11/12/13):
```
/interface wireguard peers print detail
```
ISP-A (the NAT translation):
```
/ip firewall connection print detail where protocol=udp
```
PC-A:
```
ping -c 3 10.10.2.10
ping -c 3 10.10.3.10
traceroute 10.10.2.10
```
Failover — PC-A `ping 10.10.2.10` running, then on RELAY1:
```
/interface wireguard disable wg0
```
After 20–30 s, hEX-A and PC-A:
```
/ip route print where dst-address=10.10.2.0/24
traceroute 10.10.2.10
```
RELAY1:
```
/interface wireguard enable wg0
```
Optional: capture on the link ISP-A ↔ INTERNET in Wireshark — only WireGuard UDP, no inner addresses.

## Step 8 — controller node

CONTROLLER (eth0 → INTERNET e5, eth1 → NAT):
```
ip link set eth0 up
ip addr add 203.0.113.200/24 dev eth0
ip link set eth1 up
udhcpc -i eth1
ping -c 3 203.0.113.101
ping -c 3 8.8.8.8
echo "https://dl-cdn.alpinelinux.org/alpine/v3.22/main" > /etc/apk/repositories
echo "https://dl-cdn.alpinelinux.org/alpine/v3.22/community" >> /etc/apk/repositories
apk update
apk add python3 py3-pip py3-yaml git
pip install --break-system-packages librouteros
python3 -c "import librouteros, yaml; print('ok')"
```

## Step 9 — management path and API access

CONTROLLER:
```
ip route add 10.101.0.0/24 via 203.0.113.101
ip route add 10.102.0.0/24 via 203.0.113.102
```
RELAY1 and RELAY2 (same commands on each):
```
/ip firewall nat add chain=srcnat src-address=203.0.113.200 out-interface=wg0 action=masquerade comment=mgmt
/user add name=controller group=full password="admin"
/ip service set api address=203.0.113.200/32 disabled=no
```
hEX-A, hEX-B, hEX-C (same commands on each):
```
/user add name=controller group=full password="admin"
/ip service set api address=10.101.0.1/32,10.102.0.1/32 disabled=no
```
CONTROLLER checks:
```
ping -c 3 10.101.0.11
ping -c 3 10.102.0.11
python3 -c "from librouteros import connect; api=connect(host='203.0.113.101', username='controller', password='admin', port=8728); print(list(api.path('system','identity')))"
python3 -c "from librouteros import connect; api=connect(host='10.101.0.11', username='controller', password='admin', port=8728); print(list(api.path('system','identity')))"
```

## Step 10 — the controller takes over

CONTROLLER (GitHub access by SSH key):
```
ssh-keygen -t ed25519 -C "gns3-controller" -f /root/.ssh/id_ed25519 -N ""
cat /root/.ssh/id_ed25519.pub
ssh -T git@github.com
git clone git@github.com:Starezax/mikrotik-netctl.git /root/netctl
cd /root/netctl
python3 netctl.py status
python3 netctl.py apply
python3 netctl.py apply
```
The first `apply` adopts the hand-made config (`-> comment=netctl`), the second reports
`everything already matches network.yaml`.

Drift demo — hEX-B, then CONTROLLER:
```
/ip route remove [find where dst-address=10.10.1.0/24 and gateway=10.102.0.1]
python3 netctl.py apply
```

## Step 11 — provisioning a new router (hEX-D)

ISP-D:
```
/system identity set name=ISP-D
/ip address add address=203.0.113.14/24 interface=ether1
/ip address add address=100.64.4.1/24 interface=ether2
/ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade
/ip pool add name=isp-pool ranges=100.64.4.2-100.64.4.254
/ip dhcp-server add name=isp-dhcp interface=ether2 address-pool=isp-pool disabled=no
/ip dhcp-server network add address=100.64.4.0/24 gateway=100.64.4.1 dns-server=8.8.8.8
```
hEX-D ("factory" state; `dhcp-client add` only if the list is empty):
```
/ip dhcp-client print
/ip dhcp-client add interface=ether1 disabled=no
/ip address add address=192.168.88.1/24 interface=ether2
/ip firewall nat add chain=srcnat out-interface=ether1 action=masquerade
/ip service print where name=api
```
PC-D:
```
ip link set eth0 up
ip addr add 192.168.88.10/24 dev eth0
ip link set eth1 up
udhcpc -i eth1
ping -c 3 192.168.88.1
echo "https://dl-cdn.alpinelinux.org/alpine/v3.22/main" > /etc/apk/repositories
echo "https://dl-cdn.alpinelinux.org/alpine/v3.22/community" >> /etc/apk/repositories
apk update
apk add python3 py3-pip git
pip install --break-system-packages librouteros
ip route add 203.0.113.0/24 via 192.168.88.1
git clone https://github.com/Starezax/mikrotik-netctl.git /root/netctl
cd /root/netctl
```
CONTROLLER (leave running; `network.yaml` must contain the invite under `invites:`):
```
python3 enroll_server.py
```
PC-D:
```
wget -qO- http://203.0.113.200:8080/relays
python3 provision.py --password ROUTER_PASSWORD --token INVITE-D-7F3K --name hEX-D
ip addr add 10.10.4.10/24 dev eth0
ip route add 10.10.0.0/16 via 10.10.4.1
ip route add 10.101.0.0/24 via 10.10.4.1
ip route add 10.102.0.0/24 via 10.10.4.1
ping -c 3 10.10.1.10
traceroute 10.10.1.10
```
CONTROLLER (commit the state the enroll server wrote):
```
python3 netctl.py status
git add network.yaml
git commit -m "hEX-D enrolled"
git push
```
For the next router: add a new code under `invites:` in `network.yaml` on the controller.

## Step 12 — route optimization

GNS3: right-click the link INTERNET ↔ RELAY1 → Packet filters → Delay 40 ms.

hEX-A (see the difference):
```
/ping 10.101.0.1 count=5
/ping 10.102.0.1 count=5
```
CONTROLLER:
```
python3 netctl.py optimize
python3 netctl.py status
```
PC-A (now via 10.102.0.1, ~3 ms instead of ~164 ms):
```
traceroute 10.10.2.10
```
Adaptive demo — CONTROLLER:
```
python3 netctl.py watch 30
```
Then in GNS3 move the delay from the RELAY1 link to the RELAY2 link; within 30 s `watch` switches all
routes back to RELAY1. Stop with Ctrl+C. (Current lab state: delay on INTERNET ↔ RELAY2.)

## Step 13 — direct P2P tunnels

CONTROLLER (append to the live `network.yaml`):
```
echo "" >> network.yaml
echo "p2p:" >> network.yaml
echo "  enabled: true" >> network.yaml
echo "  net: 10.100.0" >> network.yaml
echo "  interface: wg-p2p" >> network.yaml
echo "  port: 13233" >> network.yaml
tail -8 network.yaml
python3 netctl.py apply
```
First attempt: all pairs `via relay (no handshake)` — NAT collision (see `troubleshooting.md`).
Diagnosis — hEX-A, then ISP-B:
```
/interface wireguard peers print detail where interface=wg-p2p
/ip firewall connection print where dst-address~":13233"
```
Fix: port mapping on the "open" providers. ISP-A:
```
/ip firewall nat add chain=dstnat dst-address=203.0.113.11 protocol=udp dst-port=13233 action=dst-nat to-addresses=100.64.1.2 comment="p2p port mapping"
/ip firewall connection remove [find where dst-address~":13233"]
/ip firewall connection remove [find where src-address~":13233"]
```
ISP-B:
```
/ip firewall nat add chain=dstnat dst-address=203.0.113.12 protocol=udp dst-port=13233 action=dst-nat to-addresses=100.64.2.2 comment="p2p port mapping"
/ip firewall connection remove [find where dst-address~":13233"]
/ip firewall connection remove [find where src-address~":13233"]
```
ISP-D:
```
/ip firewall nat add chain=dstnat dst-address=203.0.113.14 protocol=udp dst-port=13233 action=dst-nat to-addresses=100.64.4.254 comment="p2p port mapping"
/ip firewall connection remove [find where dst-address~":13233"]
/ip firewall connection remove [find where src-address~":13233"]
```
CONTROLLER, then PC-A (3 hops, no relay):
```
python3 netctl.py apply
traceroute 10.10.2.10
traceroute 10.10.3.10
```

## Step 14 — fallback to the relay (both sides closed)

ISP-D:
```
/ip firewall nat remove [find where comment="p2p port mapping"]
/ip firewall connection remove [find where dst-address~":13233"]
/ip firewall connection remove [find where src-address~":13233"]
```
ISP-C:
```
/ip firewall connection remove [find where dst-address~":13233"]
/ip firewall connection remove [find where src-address~":13233"]
```
After ~30 s — PC-C (via relay) and PC-A (direct):
```
traceroute 10.10.4.10
```
After ~3 min — CONTROLLER (expect `hEX-C <-> hEX-D: via relay (no handshake)`):
```
python3 netctl.py apply
```

## Saving the lab

- RouterOS export on every MikroTik (keep in `configs/` if you like): `/export file=lab1`, view with `/export`.
- GNS3: File → Export portable project (to hand the whole lab to a teammate).
- Persisting Alpine settings (installed-to-disk images only), e.g. CONTROLLER `/etc/network/interfaces`:
```
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet static
    address 203.0.113.200/24
    up ip route add 10.101.0.0/24 via 203.0.113.101
    up ip route add 10.102.0.0/24 via 203.0.113.102

auto eth1
iface eth1 inet dhcp
```
