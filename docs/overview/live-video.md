# Low-latency phone video

Phone capture requests a native 1920×1080 camera track at 60 FPS. The phone sends
that track to authenticated reviewers over WebRTC. Browser video encoding,
congestion control, jitter buffering and native video rendering replace the
JPEG/acknowledgement loop for dashboard video. The 16 Mbps encoder maximum is a
ceiling, not a promise of constant bitrate. The resolution-preserving preference
can reduce frame rate under congestion; the UI shows actual results.

Replit/Runpod carry only authenticated SDP/ICE signaling at `/ws/live`, plus the
existing API and analysis traffic. Camera video travels directly between peers
or through a configured TURN relay. The GPU and Python image decoding do not
sit in the display path. Each reviewer creates another upload from the phone;
this implementation suits the existing small shared workspace, not a large
broadcast audience.

The separate JPEG evidence stream retains its existing selectable 960px/up-to-12
FPS or 1280px/up-to-8 FPS profile. It feeds original-context buffering, inference
sampling and incident recording. Recorded incident clips therefore do not inherit
the live WebRTC resolution/frame rate. Uploaded videos use the existing context
preview. The context preview also remains available when direct video cannot
connect; the UI identifies its lower frame rate.

## Connectivity

Set `WEBRTC_ICE_SERVERS` on the worker to a JSON array of RTCIceServer objects.
The default is a public STUN server. For reliable connections across cellular
CGNAT, enterprise Wi-Fi and UDP restrictions, supply your own TURN provider with
UDP and TCP/TLS relay support. Runpod's HTTP proxy is not a TURN relay.

Example shape (place actual credentials only in worker configuration):

```dotenv
WEBRTC_ICE_SERVERS=[{"urls":"stun:stun.example.org:3478"},{"urls":["turn:turn.example.org:3478?transport=udp","turns:turn.example.org:443?transport=tcp"],"username":"SHORT_LIVED_USERNAME","credential":"SHORT_LIVED_CREDENTIAL"}]
```

ICE configuration reaches authorized camera/reviewer browsers because they must
use the relay. Use short-lived, scoped TURN credentials and rotate configuration
before expiration; this implementation does not mint provider credentials.
Pairing tokens remain in the phone URL fragment and first signaling message;
reviewers use the existing HttpOnly workspace cookie. Source rooms isolate peers.
No audio is captured.

## Optional camera geotags

The phone capture page has a default-off **Share camera location** toggle. Once the camera is paired, enabling it requests browser location permission and starts a location watch. It does not delay camera startup, frame acknowledgements, or WebRTC playback. Accepted fixes are limited to one every five seconds and include latitude, longitude, the browser's accuracy in meters, and its measurement timestamp. Permission failure leaves the video running. Switching the toggle off, disconnecting, or stopping the camera stops the watch and clears the live position.

The private dashboard shows a location and its reported accuracy in the live monitor. Saved incidents retain a separate geotag from the camera's location history at the event time, including its measurement time and provenance; a usable fix must precede the event and be no more than 60 seconds old. Later movement does not rewrite an incident's saved position. No location is invented for earlier uploads, remote cameras, denied permission, or missing/stale fixes. A camera without a position provider needs a separate configured-location feature; its IP address is not treated as its physical location.

Browser coordinates are user-device reports, not verified addresses or incident locations, and indoor precision varies. No reverse-geocoding service or map tiles are requested automatically. **Open map** is an explicit reviewer action. Incident geotags are private metadata included in the existing incident archive and follow that incident's retention or recall hold. Location sharing does not grant training permission.

The capture protocol advertises `geolocation: true` in its ready message and accepts separate JSON `geolocation` updates only on the paired capture socket. Invalid or stale updates are rejected without interrupting camera frames. Source recovery after a worker restart clears live coordinates; saved incident snapshots remain intact.

Reference: [browser location watches and permissions](https://developer.mozilla.org/en-US/docs/Web/API/Geolocation/watchPosition).

## MOSS delivery

The adapter previously imposed an artificial 12 tokens/second pacing cap. It now
uses upstream's uncapped pacing value, without changing the output token budget,
model, sampling or evidence gates. Native output and verification completion
trigger coalesced source events within a 25 ms scheduling window; they no longer
wait for another phone frame. GPU decode time and competition with Qwen still
apply. While the event connection is healthy the dashboard polls health only,
preventing stale database telemetry from overwriting newer observations.

## Measure

The phone reports negotiated camera settings. The dashboard reports actual
presented FPS (video-frame callback metadata), decoded FPS, resolution, receive
bitrate, decoder drops, presentation drops, jitter buffer and network RTT.
Network RTT is not glass-to-glass latency. A 60 FPS request does not prove 60 FPS
capture or presentation: device capability, low light, thermal throttling,
network capacity and display refresh all matter.

Run `npm run test:video`, `npm run test:gateway`, `pytest -q` and the frontend
production build. Browser acceptance must exercise both peers, actual decoded
video and reviewer reconnection. Test a real phone over Wi-Fi and cellular,
including a forced TURN relay, before claiming cross-network 1080p60 support.

Sources: [WebRTC peer connections](https://webrtc.org/getting-started/peer-connections),
[TURN connectivity](https://webrtc.org/getting-started/turn-server),
[RTCRtpSender parameters](https://developer.mozilla.org/en-US/docs/Web/API/RTCRtpSender/setParameters).

The physical iPhone Wi-Fi test initially remained on the 3.8 FPS context
fallback; the synthetic 60 FPS result did not establish cross-network connectivity.
A readiness bug also let an empty incoming track briefly show a black direct
view. The receiver now waits for decoded frames, positive dimensions and usable
video data before changing views. The fallback remains mounted and stable.
Private TURN setup is documented in [private-turn.md](private-turn.md).

`WEBRTC_ICE_TRANSPORT_POLICY=relay` forces the private relay for a restrictive
Wi-Fi demo, avoiding attempts to connect peers directly across isolated clients.
Use `all` to permit direct connections when appropriate. The UI distinguishes
the selected route and transport. Master relay secrets remain on the backend;
one-hour HMAC credentials reach authenticated peers.
