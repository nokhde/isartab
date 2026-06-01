# Deploying the Debate Allocator — beginner-friendly guide

This guide assumes **zero programming background**. If you can use a web
browser and copy/paste, you can do this. It will get the app running on a
small online server that anyone in the world can visit.

The whole thing costs about **5–10 € per month** and takes around 30
minutes the first time.

---

## What you'll end up with

A website like `https://debate.your-club.org` where:
- you (the tabmaster) get a secret admin link to run an event,
- participants scan a QR code or type a 9-digit code to register.

---

## The plan

1. Rent a tiny server (a "VPS") from Hetzner.
2. Install a tool called **Coolify** on it — that's a friendly dashboard
   for hosting apps.
3. Point a domain name at the server.
4. Tell Coolify to grab this project from GitHub and run it.

You will **not** touch any code. You'll click buttons and paste a few
short commands.

---

## Step 1 — Rent a server

1. Make an account at <https://www.hetzner.com/cloud>.
2. Click **+ New Project**, name it `debate`.
3. Inside the project, click **Add Server**.
   - **Location:** any one near you (e.g. Falkenstein for Germany).
   - **Image:** Ubuntu 24.04.
   - **Type:** the cheapest "Shared vCPU" option (CX22 is plenty).
   - **SSH key:** skip this for now — choose **password** instead and
     write the password down somewhere safe.
   - **Name:** `debate`.
4. Click **Create & Buy now**.

After ~30 seconds the server is ready. Note its **IPv4 address** (shown
on the server page). It looks like `203.0.113.42`.

---

## Step 2 — Install Coolify on the server

1. On your own laptop, open the **Terminal** app (macOS/Linux) or
   **PowerShell** (Windows).
2. Connect to the server. Replace the IP with your own:

   ```bash
   ssh root@203.0.113.42
   ```

   Type `yes` if it asks about fingerprints, then paste the password you
   set in Step 1. (On most terminals the password is invisible while
   typing — that's normal.)

3. Once you see a prompt like `root@debate:~#`, paste this single
   command and press Enter:

   ```bash
   curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
   ```

   This downloads and installs Coolify. It takes 3–5 minutes. Wait until
   it prints the URL it's running on (something like
   `http://203.0.113.42:8000`).

4. Open that URL in your browser. Create the admin account (email +
   password) on the welcome screen. **Save these credentials.**

---

## Step 3 — Point a domain at the server (optional but recommended)

If you have a domain (e.g. bought from Namecheap, Cloudflare, etc.):

1. Go to your domain registrar's DNS settings.
2. Add an **A record**:
   - **Name:** `debate` (or whatever subdomain you want)
   - **Value:** your server's IP address from Step 1
3. Save. Wait a few minutes for it to propagate.

You'll use `debate.your-domain.com` in the next step.

If you don't have a domain, you can still use the server's bare IP — but
participants will see a "not secure" warning in their browser because
there's no HTTPS. Get a domain; they cost ~10 €/year.

---

## Step 4 — Deploy the app via Coolify

In the Coolify dashboard:

1. **+ New** → **Resource** → **Public Repository**.
2. Paste this repo's URL (the GitHub link you got this code from).
3. **Build Pack:** choose **Dockerfile**.
4. Click **Continue**.

On the next screen ("Configuration"):

5. **Port:** set to `8000`.
6. **Domains:** type your full URL, e.g. `https://debate.your-domain.com`
   (include `https://`). Coolify will automatically set up the HTTPS
   certificate.
7. **Environment Variables** → add:
   - **Name:** `BASE_URL`
   - **Value:** `https://debate.your-domain.com` (same URL, no trailing
     slash)

   Add a second one for your legal imprint (Impressum):
   - **Name:** `IMPRINT_TEXT`
   - **Value:** your name/club, address, email etc. — each item on its own
     line. If you skip this, the `/legal` page just shows a placeholder
     listing what's required.

   You do **not** need to set up persistent storage. This app keeps all
   data in memory only — nothing is saved to disk, and every event is
   wiped when the app restarts. That's on purpose, for data protection.

8. Click **Deploy** (top right).

Wait 1–2 minutes. The status will go from "Building" → "Running".

---

## Step 5 — Try it

Open your URL in a browser. You should see the landing page. Click
**Create event** — you'll get an admin link and an event code. Done.

Bookmark the admin link **and write it down on paper** — if you lose it,
that event can't be recovered.

---

## Day-to-day use

- **Create an event** from the landing page; share the 9-digit code (or
  the QR shown on `/created`) with participants.
- **Admin panel** lives at the admin link you got at creation time.
- **Updates:** when this project gets new features on GitHub, click
  **Redeploy** in Coolify and you're on the latest version. **Heads up:**
  a redeploy (or any restart) wipes all current events and participants,
  because the data lives in memory only. Redeploy between events, not in
  the middle of one.

---

## Things that go wrong (and what to do)

- **Browser says "site can't be reached":** wait 1–2 more minutes after
  deploy; certificate setup is slow on the first run.
- **Browser warning about an invalid certificate:** the domain DNS
  hasn't propagated yet, or you typed the domain wrong in Coolify.
  Double-check both, then click **Redeploy**.
- **Events disappeared after redeploy:** that's expected. This app stores
  data in memory only, so any restart or redeploy clears all events. Only
  redeploy between events, never during one.
- **Forgot the admin link for a live event:** there's no recovery.
  Always copy the admin link to a safe place when you create the event.

---

## Costs

| Thing | Roughly |
| --- | --- |
| Hetzner server (CX22) | ~4 €/month |
| Domain name | ~10 €/year |
| Coolify | free |
| **Total** | ~5 €/month |

You can shut the server down between debate seasons if you want; just
re-create it later and redeploy. Note there's nothing to back up — the
app keeps no data on disk, so each run starts with a clean, empty
database.
