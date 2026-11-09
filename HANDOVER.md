# HANDOVER — mikrotik-netctl (UCU Computer Networks semester project)

This file is written for an AI assistant (or a teammate) picking up the project cold.
Read it fully before touching anything. Last updated: 2026-11-09.

## 1. The assignment (original wording, Ukrainian, translated)

Goal: build a tool that automatically builds, configures and manages a **secure, fault-tolerant,
distributed network of MikroTik hEX routers**.

Server side:
- centralized network management
- network configuration
- router configuration
- routing optimization: P2P if NAT can be traversed; if servers are geographically
  distributed, pick a server from the pool that gives the shortest route

Client side:
- router provisioning
- UI/UX for the user

Plan from the course: first a simulation in **GNS3**, then real MikroTik hEX devices are handed out.

## 2. Status at a glance

| Requirement | Status | Where |
|---|---|---|
| Secure (WireGuard), distributed (sites behind NAT), fault-tolerant (2 relays, failover) | Done, tested in GNS3 | lab, `docs/lab-setup.md` |
| Router configuration + centralized management (desired state in YAML, idempotent apply) | Done, tested | `netctl.py apply`, `network.yaml` |
| Provisioning of a new router (invite code, keys generated on router) | Done, tested with hEX-D | `provision.py`, `enroll_server.py` |
| Route optimization: relay chosen by measured RTT, hysteresis, watch loop | Done, tested | `netctl.py optimize`, `watch` |
| Route optimization: direct P2P tunnels when NAT allows, relay fallback | Done, tested | `netctl.py` (p2p section), `network.yaml: p2p` |
| UI (web page on the controller) | **Not started** — next task | — |
| Move to real hEX hardware | Not started | see section 9 |

## 3. Architecture in one paragraph

Every site router (hEX-A..D) sits behind its provider's NAT and **dials out** WireGuard tunnels to two
relays with public addresses (RELAY1, RELAY2). Relays forward traffic between sites. Each site has a
LAN 10.10.N.0/24. Site routers have one route per remote LAN per path, with `check-gateway=ping`, so
RouterOS fails over on its own in ~20–30 s. A **controller** (Alpine + Python) holds the desired state in
`network.yaml` and pushes it to every MikroTik through the **RouterOS API** (librouteros, port 8728).
The controller reaches relays directly and reaches site routers **through the relays** (relay does
srcnat of the controller's traffic into the tunnel). On top, the controller measures RTT from every site
to every relay and orders routes by cost, and builds direct site-to-site WireGuard tunnels (`wg-p2p`)
that become distance 1 when they handshake. New routers join via `provision.py` (run in the new
router's LAN) + `enroll_server.py` (HTTP :8080 on the controller).

Full details: `docs/architecture.md`. Interactive topology with every command per node: `lab/topology.html`
(also published as a claude.ai artifact: https://claude.ai/artifact/ArLuAddiNPmUtRuSxwykZg).

## 4. Addressing plan (memorize this)

| What | Value |
|---|---|
| "Public internet" (GNS3 Ethernet switch INTERNET) | 203.0.113.0/24 — ISP-A .11, ISP-B .12, ISP-C .13, ISP-D .14, RELAY1 .101, RELAY2 .102, CONTROLLER .200 |
| Provider networks (behind ISP NAT) | 100.64.N.0/24, ISP = .1, hEX = .2 (hEX-D got .254 by DHCP) |
| Site LANs | 10.10.N.0/24, router .1, PC .10 (N = 1..4 for A..D) |
| Tunnels via RELAY1 / RELAY2 | 10.101.0.0/24 / 10.102.0.0/24 — relay .1, site = id (11..14) |
| Direct P2P tunnels | 10.100.0.0/24 — site = id (11..14), interface `wg-p2p`, port 13233 |
| WireGuard ports | relays 51820; sites wg-r1 13231, wg-r2 13232, wg-p2p 13233 |
| API account on every MikroTik | user `controller`, password `admin`, group full (lab only!) |
| Site id ↔ LAN | id = 10 + N, LAN = 10.10.(id-10).0/24 |

## 5. GNS3 lab — current state

Images used: `MikroTik CHR 7.19.4` (all routers, relays, ISPs), `Alpine Linux Virt 3.22.1` (PCs, controller),
built-in `Ethernet switch`, built-in `NAT` nodes.

Nodes: INTERNET (switch), RELAY1, RELAY2, CONTROLLER (+NAT), ISP-A..D, hEX-A..D, PC-A..D, NAT-D (for PC-D).
Switch ports: e0 ISP-A, e1 ISP-B, e2 ISP-C, e3 RELAY1, e4 RELAY2, e5 CONTROLLER, e6 ISP-D.

State worth knowing:
- **Delay filter**: GNS3 packet filter Delay 40 ms on the link INTERNET ↔ RELAY2 (was on RELAY1, moved
  during the watch demo). So RELAY1 is currently the "fast" relay: distance 1 where there is no P2P, distance 2 behind the direct route where there is.
- **ISP-A and ISP-B** have a dst-nat "port mapping" for UDP 13233 → their hEX ("open" NAT, like UPnP).
- **ISP-C and ISP-D** have no port mapping ("closed" NAT). Consequence: the pair hEX-C ↔ hEX-D cannot
  go direct and uses a relay; every other pair is direct.
- **ISP-D** runs a DHCP server; hEX-D got 100.64.4.254.
- **hEX-D** was provisioned automatically; its LAN has both 192.168.88.1 (factory) and 10.10.4.1.
- **CONTROLLER** and all Alpine PCs lose `ip addr` / `ip route` settings on reboot (re-run the commands
  in `docs/lab-setup.md`, or persist them in `/etc/network/interfaces`). RouterOS nodes keep config.
- Repo on CONTROLLER: `/root/netctl` (git, SSH key auth to GitHub `Starezax/mikrotik-netctl`).
  `network.yaml` there is the live source of truth (enroll_server rewrites it).

## 6. Code map

| File | Runs on | Purpose |
|---|---|---|
| `netctl.py` | CONTROLLER | `status`, `apply`, `optimize`, `watch [s]`. Desired state → RouterOS via API. Idempotent; marks everything it owns with `comment=netctl`; prunes only its own items. |
| `enroll_server.py` | CONTROLLER | HTTP :8080. `GET /relays`, `POST /enroll {token,name,keys}`. Validates one-time invite, adds peers+routes on relays, writes the site into `network.yaml`, then retries `apply` until the new router is reachable. |
| `provision.py` | a PC in the new router's LAN | Logs into the fresh router (192.168.88.1, admin), creates WireGuard interfaces (keys generated on the router), registers with the controller, applies returned config (identity, `controller` account, tunnel addresses, relay peers, LAN 10.10.N.1, API restricted). |
| `network.yaml` | CONTROLLER | Desired state: API account, relays, sites (id + lan), one-time `invites`, `p2p` block. |
| `metrics.json` | CONTROLLER (gitignored) | Last RTT measurements and chosen relay per site pair. |
| `lab/topology.html` | browser | Interactive diagram: click a node → GNS3 module, interfaces, current state, all commands by step. |

Key functions in `netctl.py`: `ensure()` (add/fix one RouterOS item), `prune()`, `ensure_wg()`,
`connect_all()` (sites reached via relay tunnel addresses, falls back to the second relay),
`relay_order()` (cost = RTT(a,relay)+RTT(b,relay), keeps stored choice), `optimize()` (hysteresis 20% and
5 ms), `setup_p2p()` (public IP learned from relays' `current-endpoint-address`, waits ≤30 s for
handshakes, pair is "direct" if a handshake is younger than 180 s).

Apply order: phase 1 interfaces/keys/addresses → phase 2 relays (peers + LAN routes) →
phase 3 P2P tunnels (if enabled) → phase 4 site routes (P2P first if working, then relays by cost).

## 7. Decisions and why (do not undo without a reason)

- **WireGuard over IPsec/GRE**: UDP (traversable through NAT), simple key model, built into RouterOS v7.
- **Two WireGuard interfaces per site (wg-r1, wg-r2)** instead of one: in one interface two peers can't
  share the same allowed-address (it is both filter and route), and we want 10.0.0.0/8 via both relays.
- **Static routes + `check-gateway=ping` + distances** instead of OSPF: simpler, deterministic, enough
  for the course. Failover ~20–30 s. OSPF/BFD or controller-driven switching are possible upgrades.
- **Controller reaches sites through relays with srcnat on the relay**: sites are behind NAT and must
  not get a public API. Site API accepts only 10.101.0.1 / 10.102.0.1 (+ 192.168.88.0/24 on hEX-D).
- **Desired state in YAML + idempotent reconcile** (like Ansible/Kubernetes): rerunning is safe, manual
  drift gets repaired, removing a site from YAML removes its config.
- **Keys are generated on the router**; the private key never leaves it. Controller only sees public keys.
- **Port mapping on "open" ISPs for P2P**: plain simultaneous hole punching through RouterOS conntrack
  NATs failed — inbound packets that arrive before the local side sends create an "unreplied" conntrack
  entry, so the later outbound packet collides and gets a different source port (seen: 13233 → 46404).
  Real systems use UPnP/NAT-PMP/PCP for this; we model an "open" provider with a dst-nat rule.
  One open side per pair is enough because WireGuard roams to the real source endpoint.
- **Hysteresis** (switch relay only if >20% and >5 ms better) to avoid route flapping on noisy RTT.

## 8. Gotchas we already hit (save yourself the time)

1. **Pasting multi-line blocks into the RouterOS console in GNS3 drops lines.** Paste one command at a time,
   or use SSH. This is why hEX-A initially had no WireGuard interfaces at all.
2. A WireGuard peer whose `public-key="RELAY1_PUB"` placeholder wasn't replaced → command rejected,
   nothing created. Check with `/interface wireguard peers print detail`.
3. **Alpine rp_filter**: a PC whose default route goes elsewhere (PC-D via NAT-D) drops replies from
   10.101/10.102 → `traceroute` shows `* * *`. Add routes for 10.101.0.0/24 and 10.102.0.0/24 via the
   hEX.
4. **Hole punching through RouterOS NAT fails** (see section 7). Symptom on the site: `tx` grows, `rx=0`
   on `wg-p2p` peers; on the ISP: conntrack entries for :13233 without the `S` flag. Fix: port mapping
   + `/ip firewall connection remove [find where dst-address~":13233"]` (and src-address) to clear stuck
   entries.
5. One NAT node in GNS3 takes one link → separate NAT-D for PC-D.
6. Adapters can only be changed on a stopped node; other nodes can keep running.
7. GitHub rejects passwords for git: use an SSH key (current setup) or a token in the remote URL.
8. A single lost RTT measurement marks a relay `down` for that site (seen once for hEX-B). Harmless so far,
   see TODO.
9. CHR free license caps throughput at 1 Mbit/s — fine for pings/demos, not for iperf.

## 9. What's next (priority order)

1. **UI** (client side). Plan agreed with the user: a web page served by `enroll_server.py` on :8080:
   topology (sites, relays, which pairs are direct vs via relay), RTT table and chosen relay per pair,
   per-router status (handshakes, active routes), buttons "optimize", "apply", "create invite". Reuse the
   data `status`/`optimize` already compute (refactor them to return dicts, print separately).
   Reach it from a browser: PC-D (Alpine has no GUI — consider a small GUI node or GNS3 Cloud to the host).
2. **netctl robustness**: count a relay as down only after 2 failed measurements; more pings per sample;
   passwords from environment variables instead of YAML.
3. **Optional: real symmetric NAT** on one ISP (Alpine with `iptables -t nat -A POSTROUTING -o eth0 -j
   MASQUERADE --random-fully`). Note: if the other side of a pair is "open", P2P will still work thanks
   to WireGuard roaming; the meaningful fallback demo is always "both sides closed" (as with C ↔ D now).
4. **Security hardening for the report**: api-ssl (8729) instead of plain API, disable unused services
   (telnet, ftp, www), MNDP off (`/ip neighbor discovery-settings set discover-interface-list=none`),
   remove the leftover DHCP clients on public-segment ether1, input-chain firewall.
5. **Hardware (real hEX)**: needs at least 3 hEX for sites; providers = real networks (home router, phone
   hotspot = CGNAT), relays = 2 VPS or laptops; a 4th hEX can emulate "internet + 3 ISPs" with three
   src-nat rules. On real hEX the LAN is `bridge` (pass `--lan-if bridge` to provision.py), ether1 is WAN,
   default password is on the sticker.
6. **Report** (Ukrainian, signed "Botsko Arsen"): screenshots listed in `docs/demo.md`.

## 10. How to work with the user

- User: Arsen Botsko, 3rd-year CS at UCU, embedded background. Communicates in Ukrainian and English.
- Prefers step-by-step guidance, one stage at a time, with the expected output of each step.
- **Always give full explicit commands, one per line, no shell loops or wrapper functions** — they are
  pasted into lab reports as-is. For RouterOS, separate code blocks per command help (console paste issue).
- Prefers concise, practical answers with working code.
- Ask for the real output after each step and verify it before moving on; diagnose from outputs.
