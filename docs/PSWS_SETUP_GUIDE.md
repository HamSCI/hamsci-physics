# PSWS Upload Setup Guide

> **Operators:** the step-by-step narrative lives in [sigmond `operator/registration.md`](https://github.com/HamSCI/sigmond/blob/main/docs/operator/registration.md); this page covers what GRAPE needs.

## Overview

hamsci-physics produces GRAPE datasets; it uploads nothing.  `grape-daily.timer`
decimates, draws spectrograms and packages each day as Digital RF under
`/var/lib/timestd/upload/<date>/OBS*`.  **hs-uploader** ships those datasets to
the HamSCI PSWS (Personal Space Weather Station) network through its
`grape-psws` pipeline, declared in this repo's `deploy.toml`.

PSWS authenticates each upload as the **station** account over SFTP with an SSH
key.  A station needs three things from the portal and one key from the
machine:

| Item | Where it comes from | Example |
|------|---------------------|---------|
| **Station id** (SITE_ID) | PSWS portal, when you create a site | `S000171` |
| **Instrument id** | PSWS portal, when you add an instrument | `172` |
| **Upload key** | this machine: `smd psws enroll` | `/etc/hs-uploader/keys/id_ed25519_host` |

**One key per uploading machine** (2026-10-01).  A machine that uploads for
several stations uses its one key for all of them, and you register that same
public key on each station's portal account.  A compromised machine then
exposes only the stations it serves, and revoking its key touches only them.

## PSWS server

- **SFTP server**: `pswsnetwork.eng.ua.edu`, port 22, SFTP only
- **Portal**: <https://pswsnetwork.caps.ua.edu/>
- **Authentication**: SSH public key, registered per station in the portal

## Setup

### 1. Create a PSWS account

1. Go to <https://pswsnetwork.caps.ua.edu/>.
2. Create a user account and log in.

### 2. Create a site

In the portal dashboard, create a **Site** for the physical station.  The
portal assigns its **station id** (e.g. `S000171`).

### 3. Add an instrument

Add an instrument to the site (e.g. type "grape").  The portal assigns an
**instrument id**, a short number (e.g. `172`).  It rides in the upload path,
so a wrong value uploads successfully and lands where PSWS cannot match it.

### 4. Give the station its ids

On the station, set `[station] psws_station_id` and `instrument_id` in
`/etc/hamsci-physics/config.toml`.  On an appliance station the wizard does
this from `site-profile.toml`; `smd config hamsci-physics edit` does it by hand.

### 5. Enroll the machine's key

```bash
smd psws enroll     # creates this machine's upload key; prints the public key
```

Paste that public key into the portal for **each** station this machine
uploads for.

### 6. Prove the login

```bash
smd psws verify     # SFTP login as the station id, with the machine's key
```

Nothing is lost while you wait: `grape-daily` keeps packaging, and hs-uploader
ships the backlog once the portal accepts the key.

## Watching delivery

```bash
hamsci-physics grape status          # cursor, pending datasets, recent outcomes
journalctl -u hs-uploader -f         # the uploader itself
journalctl -u grape-daily.service -n 50   # the nightly packaging run
```

## Troubleshooting

**`smd psws verify` fails with "public key not registered"**
- The portal does not hold this machine's key for that station yet.  Compare
  `smd psws enroll`'s printed key with the portal's entry for the station.
- Check the station id: the SFTP user *is* the station id.

**Datasets upload but never appear in PSWS**
- Check `instrument_id` against the portal; a wrong id uploads cleanly and
  matches nothing.

**`grape status` shows datasets pending for days**
- Check `journalctl -u hs-uploader` for the transport's error, and
  `smd config uploads status` in case uploads are disabled site-wide.

## References

- [HamSCI GRAPE Project](https://hamsci.org/grape)
- [PSWS Network Portal](https://pswsnetwork.caps.ua.edu/)
- [Digital RF Format](https://github.com/MITHaystack/digital_rf)
