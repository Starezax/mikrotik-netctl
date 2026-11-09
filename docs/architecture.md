# Architecture

## 1. Layers

**Underlay** — the real network the routers are plugged into: providers, NAT, the internet.
In GNS3: the switch INTERNET (203.0.113.0/24) and four ISPs doing NAT (100.64.N.0/24 behind them).

**Overlay** — our own network on top, built from WireGuard tunnels:
- every site router keeps a tunnel to each relay (wg-r1 → RELAY1, wg-r2 → RELAY2);
- where NAT allows, every pair of sites also has a direct tunnel (wg-p2p);
- site LANs 10.10.N.0/24 reach each other only through the overlay.

**Control plane** — the controller: desired state, configuration, measurements, decisions.
**Data plane** — user traffic between LANs. It keeps flowing if the controller is down, because
every route and tunnel already lives on the routers.

## 2. Why sites dial out

A router behind NAT can open outgoing connections but can't accept incoming ones. So each site
router initiates the WireGuard handshake to the relays (public addresses) and keeps the NAT mapping
alive with `persistent-keepalive=25s`. The relay learns the site's public address from the first
authenticated packet: it sees `203.0.113.11:13231` (ISP-A), never `100.64.1.2`.

## 3. Routing and failover

Each site has, for every remote LAN, one static route per path, all with `check-gateway=ping`:

```
10.10.2.0/24 via 10.100.0.12  distance 1   (direct P2P, if it works)
10.10.2.0/24 via 10.101.0.1   distance 2   (best relay by RTT)
10.10.2.0/24 via 10.102.0.1   distance 3   (other relay)
```

RouterOS pings each gateway every 10 s; after two misses the route goes inactive and the next distance
takes over (~20–30 s). The relay forwards between sites using its own routes
(`10.10.2.0/24 via 10.101.0.12`) and WireGuard cryptokey routing (the peer whose `allowed-address`
contains the destination).

## 4. Management path

The controller (203.0.113.200) talks RouterOS API (TCP 8728) to:
- relays directly (API allowed only from 203.0.113.200);
- sites through the relays: `ip route add 10.101.0.0/24 via 203.0.113.101` on the controller, and on
  the relay `srcnat src-address=203.0.113.200 out-interface=wg0 action=masquerade`. The site sees the
  request from 10.101.0.1 and replies into the tunnel. Site API accepts only 10.101.0.1 and 10.102.0.1.
- if RELAY1 is unreachable, `connect_all()` retries through RELAY2 (10.102.0.X).

## 5. Desired state and reconcile (`netctl.py apply`)

`network.yaml` describes relays, sites (id, lan), invites and the p2p block. `apply` connects to every
router and, for each item it owns (WireGuard interface, address, peer, route):
- adds it if missing (`+`), fixes differing fields (`~`), leaves it if correct;
- marks it `comment=netctl`;
- removes `netctl`-marked items that are no longer desired (`-`). Unmarked manual items are never touched.

Phases: (1) interfaces, keys, tunnel addresses — keys are read from the routers, nobody copies them;
(2) relays: a peer and a LAN route per site; (3) P2P tunnels; (4) site routes ordered by cost.

## 6. Provisioning

```
PC in new LAN                      new router (factory)          controller :8080              relays
provision.py ── API login ───────► create wg-r1, wg-r2
             ◄── public keys ─────  (keys generated here)
             ── GET /relays ──────────────────────────────────►
             ── POST /enroll {token, name, keys} ─────────────► check one-time invite
                                                                assign id, LAN 10.10.N.0/24
                                                                add peer + route ────────────► 
                                                                write network.yaml
             ◄── {addresses, relay keys, API account} ────────
             ── API: identity, user controller, tunnel addresses,
                relay peers, LAN 10.10.N.1, API limited ───────►
                                   tunnel comes up ──────────────────────────────────────────►
                                                                apply (retries until reachable):
                                                                routes to the new LAN everywhere
```

The private key never leaves the router; the invite works once.

## 7. Route optimization (`optimize`, `watch`)

1. Each site pings each relay's tunnel address through the API (3 pings, 200 ms apart).
2. Cost of using relay r between sites a and b: `RTT(a, r) + RTT(b, r)`.
3. Best relay per pair = minimum cost. Hysteresis: keep the current relay unless the new one is
   more than 20% **and** more than 5 ms better (no flapping on noise).
4. Choices and measurements go to `metrics.json`; `apply` orders routes by them, so plain `apply`
   doesn't undo the optimization.
5. `watch N` repeats this every N seconds — a monitoring loop that follows changing delays.

Example (relay delay moved from RELAY1 to RELAY2 during `watch 30`):
```
hEX-A <-> hEX-B: RELAY1 3.0, RELAY2 174.2 -> RELAY1  (was RELAY2)
```
Limitation in the lab: on one switch a relay's delay is the same for every site, so all pairs pick the
same relay. With geographically distributed relays (VPS in different countries) different pairs get
different relays; the formula already supports that.

## 8. Direct P2P tunnels

1. Public address of each site = `current-endpoint-address` seen by the relays (the ISP NAT address).
2. Each site gets `wg-p2p` (port 13233, address 10.100.0.id) with a peer per other site:
   endpoint = other site's public IP : 13233, keepalive 10 s.
3. The controller waits up to 30 s; a pair is "direct" if either side has a handshake younger than 180 s.
4. Direct pairs get the P2P route at distance 1, relays behind it; others stay on relays.

**Why plain hole punching failed through the lab's NATs.** RouterOS (Linux conntrack) creates an
"unreplied" entry for an inbound packet that arrives before the local router has sent anything. When the
local router then sends out, the NAT finds the tuple taken and picks another source port
(observed: hEX-D's 13233 became 46404). The other NAT expects replies from :13233 and drops them.
Continuous keepalives keep the stale entries alive, so the pair never converges.

**Fix used:** "open" providers (ISP-A, ISP-B) map UDP 13233 to their router (what UPnP / NAT-PMP / PCP
do on real home routers). One open side per pair is enough: the closed side's outbound packet reaches the
open side, which replies to the real source port — WireGuard roams to it — and the reply passes the
closed NAT as a reply. Only pairs where **both** sides are closed (ISP-C ↔ ISP-D) stay on a relay.

## 9. Security

- All site-to-site traffic is WireGuard (Curve25519, ChaCha20-Poly1305); the provider sees only UDP.
- Private keys are generated on and never leave each router.
- API reachable only from the controller (relays) or only from relay tunnel addresses (sites).
- One-time invite codes for provisioning.
- Lab shortcuts to fix for real hardware: password `admin`, plain API (use api-ssl 8729), default
  services, MNDP, no input firewall.
