# NASA Earthdata Account Setup

The GNSS VTEC service, `hamsci-physics-vtec.service`, downloads Differential
Code Bias (DCB) files from NASA's CDDIS archive.  DCB corrections improve VTEC
accuracy because they remove satellite and receiver biases.  CDDIS asks for a
free NASA Earthdata account.

The IONEX download (Global Ionosphere Maps) moved to hf-timestd in v3.96.
hf-timestd's own docs cover its credentials.

## Why You Need It

Without credentials the DCB download fails.  The service keeps running but
assumes zero bias, and its VTEC accuracy suffers.

## Setup Steps

### 1. Create NASA Earthdata Account

1. Go to <https://urs.earthdata.nasa.gov/users/new>
2. Fill out the registration form
3. Verify your email address
4. **Important**: Request access to CDDIS data:
   - Log in to <https://urs.earthdata.nasa.gov/>
   - Go to "Applications" → "Authorized Apps"
   - Add "NASA GESDISC DATA ARCHIVE"

### 2. Configure Authentication

The VTEC service reads its credentials from `/etc/hamsci-physics/earthdata-netrc`;
its unit sets `NETRC` to that path.

**Option A: Adoption from hf-timestd**

A station that ran hf-timestd's old `timestd-vtec.service` already has
`/etc/hf-timestd/earthdata-netrc`.  When sigmond moves the service here, it
runs `hamsci-physics vtec adopt`, which copies that file to
`/etc/hamsci-physics/earthdata-netrc` with mode 600 and the same owner.  It
never overwrites a file already there.

**Option B: Manual Setup**

```bash
# Create the credential file
sudo tee /etc/hamsci-physics/earthdata-netrc > /dev/null << 'EOF'
machine urs.earthdata.nasa.gov
login YOUR_EARTHDATA_USERNAME
password YOUR_EARTHDATA_PASSWORD
EOF

# Set correct ownership and permissions (CRITICAL - must be 600)
sudo chown timestd:timestd /etc/hamsci-physics/earthdata-netrc
sudo chmod 600 /etc/hamsci-physics/earthdata-netrc
```

**Credential Lookup Order:**

The downloader looks for credentials in this order:
1. the `NETRC` environment variable (the unit sets it)
2. `/etc/hamsci-physics/earthdata-netrc`
3. `~/.netrc` (a fallback for interactive and development use)

### 3. Verify Setup

After creating the credential file, restart the service:

```bash
sudo systemctl restart hamsci-physics-vtec
```

Check the log:

```bash
sudo journalctl -u hamsci-physics-vtec -n 50 | grep -E "DCB|bias|Download"
```

You should see:

```
Downloading CAS0OPSRAP_YYYYDDD0000_01D_01D_DCB.BIA.gz...
Unzipping...
Parsed XXXX bias entries.
Loaded XXXX bias entries.
```

## Troubleshooting

### "Unzip failed: Not a gzipped file"

This means authentication failed — CDDIS returned an HTML login page
instead of the binary file. Check:

1. Credentials are correct in `/etc/hamsci-physics/earthdata-netrc`
2. File permissions are exactly `600` (not `644` or `640`)
3. File is owned by `timestd:timestd`
4. You've authorized CDDIS access in your Earthdata account

### Empty DCB Files (0 bytes)

If files in `/var/lib/timestd/data/dcb/` are 0 bytes:

```bash
# Remove empty files
sudo rm /var/lib/timestd/data/dcb/*.BIA

# Restart the service to re-download
sudo systemctl restart hamsci-physics-vtec
```

### The Service Works Without Credentials

Without CDDIS credentials the VTEC service runs but assumes zero DCB bias,
which reduces its accuracy.  The log shows:

```
Failed to download DCB file. VTEC accuracy will be degraded (0 bias assumed).
```

## Security Notes

- The credential file holds your password in plain text.
- It **must** have `600` permissions (owner read/write only).
- Consider a dedicated Earthdata account for the station.
- Rotate the password from time to time.
