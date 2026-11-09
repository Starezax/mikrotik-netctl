# mikrotik-netctl

Automated building, configuration and management of a secure, fault-tolerant, distributed network
of MikroTik hEX routers. Semester project, Computer Networks course, Ukrainian Catholic University.

Sites sit behind their providers' NAT and connect to each other through WireGuard tunnels: via two
relays (with automatic failover) and directly (P2P) wherever NAT allows. A controller keeps the desired
state of the network in one YAML file and applies it to every router through the RouterOS API,
measures delays to pick the fastest relay, and lets new routers join with one command.

## What it does

| Feature | Command |
|---|---|
| Show tunnels, handshakes, delays, active routes of the whole network | `python3 netctl.py status` |
| Configure every router to match `network.yaml` (safe to rerun, repairs drift) | `python3 netctl.py apply` |
| Measure RTT site → relay, pick the best relay per site pair, apply | `python3 netctl.py optimize` |
| Keep optimizing every N seconds (monitoring loop) | `python3 netctl.py watch 30` |
| Accept new routers (one-time invite codes) | `python3 enroll_server.py` |
| Join a fresh router to the network (run in its LAN) | `python3 provision.py --password PASS --token INVITE --name hEX-D` |

## Repository

```
netctl.py          controller: status / apply / optimize / watch
enroll_server.py   controller: HTTP :8080 registration service for new routers
provision.py       client: provisions a fresh router from a PC in its LAN
network.yaml       desired state of the network (relays, sites, invites, p2p)
lab/topology.html  interactive GNS3 topology: click a node to see its commands
docs/
  lab-setup.md       rebuild the GNS3 lab from scratch, every command in order
  architecture.md    how it works: tunnels, management path, provisioning, optimization, P2P
  demo.md            defense demo script with expected outputs and report screenshots
  troubleshooting.md problems we hit and how they were fixed
HANDOVER.md        full context for a teammate or an AI assistant picking up the project
```

## Quick start (on the controller)

```
apk add python3 py3-pip py3-yaml git
pip install --break-system-packages librouteros
git clone git@github.com:Starezax/mikrotik-netctl.git /root/netctl
cd /root/netctl
python3 netctl.py status
python3 netctl.py apply
```

Requirements: the controller must reach the relays' API and, through the relays, the sites' tunnel
addresses. See `docs/lab-setup.md`, steps 8–10.

## Topology (GNS3)

```
                 RELAY1 203.0.113.101      RELAY2 203.0.113.102
                          \                  /
 NAT -- CONTROLLER ---- INTERNET switch 203.0.113.0/24
        203.0.113.200     /       |        |       \
                      ISP-A     ISP-B    ISP-C    ISP-D      (NAT; A,B "open", C,D "closed")
                        |         |        |        |
                      hEX-A     hEX-B    hEX-C    hEX-D      (wg-r1, wg-r2, wg-p2p)
                        |         |        |        |
                      PC-A      PC-B     PC-C     PC-D -- NAT-D
                   10.10.1.0  10.10.2.0 10.10.3.0 10.10.4.0 /24
```

Open `lab/topology.html` in a browser for the interactive version.

## Security notes

Lab defaults are deliberately simple: API password `admin`, plain API on 8728. For real devices:
strong password from an environment variable, api-ssl (8729), unused services disabled, input firewall.
API access is already limited to the controller (relays) and to the relay tunnel addresses (sites);
private WireGuard keys never leave the router that generated them.
