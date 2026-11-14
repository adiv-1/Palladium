# Host the UI on Replit

Replit runs a small Node 22 gateway and the built React UI. Runpod remains the sole inference backend and authoritative store. Do not install Python, CUDA, model weights, or video datasets on Replit.

```text
Laptop dashboard                 Phone Safari / Chrome
       |                                  |
       +------------- HTTPS --------------+
                         |
               Replit UI + Node gateway
                |                |
          React static files     +-- /api/* and /ws/* (streamed proxy)
                                         |
                                 Runpod HTTPS proxy
                                         |
                            FastAPI / original video / SQLite
                                         |
                              GPU queue: MOSS + verifier
                             FastLLVE / RelayVSR / ReLift
```

## Import and publish

1. Import `https://github.com/adiv-1/SafeSC` into Replit. Switch the Git pane to **reviewed-learning** before running: main predates this hosting configuration and the learning repository. In the Replit Shell, `git switch reviewed-learning` also works after fetching that branch.
2. In Replit Secrets set **only** `SAFESC_BACKEND_ORIGIN` to the current GPU app origin, for example `https://YOUR-POD-ID-8000.proxy.runpod.net`. It is an address, not a provider key. The current origin is recorded locally in `data/runpod-access.txt`.
3. Click Run. The configured command builds the UI and binds the gateway to `0.0.0.0:3000`. Check the preview shows the SafeSC sign-in screen.
4. Open Publishing → Adjust settings. Choose **Reserved VM**, web server, smallest available machine for a single-phone demo. Set build to `npm run build`, run to `npm start`, local port `3000` / external port `80`. Confirm `SAFESC_BACKEND_ORIGIN` is also present in the published environment.
5. Check the displayed publishing charge against the trial's remaining credits before publishing. The trial is not a guarantee of unlimited deployment or video bandwidth. Replit's November 14 pricing documentation lists the shared 0.5 vCPU / 2 GB VM at $15/month before credits. Autoscale can serve the dashboard, but Reserved VM avoids scale-to-zero interruptions for long-lived phone WebSockets. Static hosting cannot run this proxy.
6. Publish, open the HTTPS `.replit.app` URL, and enter the existing SafeSC workspace token. No additional reviewer token is configured in Replit. The login cookie belongs to the Replit app origin; the gateway forwards it to the existing authenticated backend.
7. Click Connect phone **on the published Replit dashboard**, scan the new QR code and start the camera. Pairing links use the dashboard's current origin automatically, so both the phone capture page and its WebSocket use Replit. Keep the phone page foregrounded. Existing links from the Runpod dashboard still point at Runpod.

The GitHub import does not include `.env`, original clips, local databases, model weights, or cloud keys. Do not paste `RUNPOD_TOKEN`, `VULTR_API_KEY`, `HF_TOKEN`, SSH keys, or the Mac's entire `.env` into Replit. No `VITE_` secret is needed. The gateway never bypasses backend authorization, logs request bodies/tokens, or stores video; uploads, playback range responses, WebSocket bytes and cookies pass through it.

Both Runpod and Replit process traffic when this route is used. If the GPU pod stops or its origin changes, the UI still loads, but API/video operations fail until the pod is available or `SAFESC_BACKEND_ORIGIN` is updated and the deployment restarted. Replit hosting does not extend the Runpod budget or its Sunday shutdown deadline.

## Verification

```sh
npm run test:gateway
npm run build
SAFESC_BACKEND_ORIGIN=https://YOUR-POD-ID-8000.proxy.runpod.net npm start
```

Test sign-in, historical map, training-video playback and seeking, incident review, phone QR origin, frame ACKs and dashboard live status on the **published** URL. `/_health` reports only gateway readiness; `/api/health` requires the existing token and checks the actual GPU backend. The public Runpod URL remains a fallback while Replit is configured.

References: [Replit deployment types](https://docs.replit.com/features/publishing/deployment-types), [configuration](https://docs.replit.com/features/project-setup/configuration), [publishing costs](https://docs.replit.com/billing/deployment-pricing).
