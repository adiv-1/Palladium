# Optional private video relay

The approved demo deployment can use a dedicated private relay; its live address
and safe deployment record remain in ignored `data/turn-deployment.json`. The existing HTTPS
Runpod proxy cannot relay WebRTC UDP media; an independently reachable TURN
server gives isolated campus Wi-Fi clients another route. TURN transports video;
it does not increase camera/encoder throughput or guarantee 60 FPS.

A dedicated Ubuntu 24.04 IPv4 host can run `scripts/setup_turn.sh`. It installs
coturn, enables the service, and writes a generated shared secret to
`/etc/safesc-turn/shared-secret` with owner-only permissions. The coturn service
configuration necessarily contains the same secret and is restricted to root
and the turnserver group. Do not paste either file into chat or source control.
The script never provisions a host or changes billing. Run it only after the
specific host, spend and deployment have been authorized.

Supply the directly assigned public IPv4 in `TURN_PUBLIC_IP`. In both the host
and provider firewall allow inbound UDP 3478, TCP 443 and UDP 49160–49200; preserve
SSH access. The script deliberately does not edit either firewall. It binds only
the supplied public IPv4. The initial configuration supports
`turn:PUBLIC_IP:3478?transport=udp` and
`turn:PUBLIC_IP:443?transport=tcp`. This plain TCP 443 fallback is not TLS or HTTPS;
traffic inspection or enterprise firewalls may still block it.

For manually managed TLS, first supply a valid domain name and certificate/private key through
`TURN_DOMAIN`, `TURN_CERT_FILE` and `TURN_KEY_FILE`; rerun the setup with renewed
PEMs when needed. Coturn recognizes TLS on its primary listener, so authenticated
clients can use `turns:YOUR_DOMAIN:443?transport=tcp`. Certificate issuance, DNS changes and renewal automation are outside the base
`setup_turn.sh` script. The optional approved `setup_turn_tls.sh` bootstraps a
publicly trusted six-day IP-address certificate using pinned Certbot 5.10.0,
standalone HTTP-01 on TCP 80, and a twice-daily renewal timer with a coturn
deploy hook. It requires prior authorization for the host, firewall changes and
certificate-authority terms; it never copies provider administration credentials.
This removes the need for DNS when using `turns:PUBLIC_IP:443?transport=tcp`. A certificate for another hostname or an untrusted self-signed certificate
will not provide a working browser TLS fallback.

Copy the shared secret privately to the backend's `TURN_SHARED_SECRET`. Set
`TURN_URLS` there to the comma-separated relay URLs (a JSON string array also
works). Leave the secret out of Replit and all `VITE_` settings. The backend sends
paired cameras and authenticated reviewers only a unique username and HMAC
credential expiring one hour after the signaling connection is established.
The long-lived shared secret is never sent to the browser. Streams requiring new
allocations after that hour must reconnect signaling to obtain fresh credentials;
seamless credential refresh has not been implemented. Changing the shared secret
requires updating both services and reconnecting peers.

The relay permits eight allocations per ephemeral credential, 32 globally, four
million bytes/second per allocation and sixteen million bytes/second globally.
Those limits bound concurrent traffic, not the cloud bill. At high sustained use,
bandwidth allowances can still be exhausted; approve the provider's quoted price,
included traffic and overage terms, monitor actual usage, and schedule explicit
teardown. Private, link-local, loopback and multicast peer addresses are denied.
The server's own public relay address stays eligible so two isolated clients can
both select relay candidates on this same host. Unauthenticated TURN allocation,
TCP peer relaying and the management CLI are disabled.

After deployment, verify authenticated allocation over UDP and TCP, then force
relay ICE in a controlled browser test. Require decoded frames and a stable
connection on the actual campus Wi-Fi before calling it working. Inspect the
transient authenticated `/api/live/status?source_id=…` diagnostics for connection
state and frame counts. `bash -n` and HMAC unit tests verify the prepared source;
they are not a deployed coturn allocation or network-connectivity test.

References: [coturn configuration](https://github.com/coturn/coturn/blob/master/examples/etc/turnserver.conf),
[coturn TURN REST credentials](https://github.com/coturn/coturn/blob/master/examples/scripts/restapi/secure_relay_secret.sh).

Relay validation on November 14, 2026: the dedicated Ubuntu 24.04 coturn 4.6.1
service is active; an external default-CA TLS connection verified the issued
IP-address certificate on TCP 443. Key-only SSH, restricted UFW ports, the renewal
timer and hook syntax were checked; a Certbot staging renewal simulation passed. Relay-only browser/media measurements must
be recorded separately from certificate/service validation.
