# ESXi Shutdown Manager

A lightweight web dashboard for managing scheduled shutdowns of a VMware ESXi host. Provides a clean UI to control shutdown schedules, skip specific nights, trigger manual shutdowns, and monitor VM power states — all without touching the ESXi host directly.

![Python](https://img.shields.io/badge/python-3.12-blue)
![Docker](https://img.shields.io/badge/docker-compose-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Weekly Schedule** — Set shutdown times per day of the week, enable/disable individual days
- **Skip Tonight** — One-click skip for the next scheduled shutdown (doesn't change the recurring schedule)
- **Shut Down Now** — Manual trigger with confirmation modal; gracefully shuts down all VMs then powers off the host
- **VM Dashboard** — Real-time view of all VMs and their power states (auto-refreshes every 60s)
- **Activity Log** — Timestamped log of all events: scheduled shutdowns, skips, manual triggers, errors
- **SSH Error Reporting** — Connection failures and per-VM shutdown errors appear directly in the activity log

## How It Works

The app runs on a separate machine (or a VM on the ESXi host itself) and communicates with ESXi over SSH using key-based authentication.

**Shutdown sequence:**
1. SSH into the ESXi host
2. Enumerate all VMs via `vim-cmd vmsvc/getallvms`
3. Send graceful shutdown (`vim-cmd vmsvc/power.shutdown`) to each powered-on VM
4. Wait for VMs to power off (configurable, default 180s)
5. Power off the ESXi host (`/sbin/poweroff`)

**Scheduling:** APScheduler checks every minute if the current time matches the schedule for today. If the day is marked as "skipped," the shutdown is skipped and the skip entry is cleared.

## Architecture

```
+------------------+         SSH (key auth)        +------------------+
|   Shutdown Mgr   | ---------------------------> |    ESXi Host     |
|   (Docker)       |                               |                  |
|                  |   vim-cmd vmsvc/power.shutdown |   VM 1 (off)    |
|  Flask + SQLite  |   vim-cmd vmsvc/getallvms     |   VM 2 (on)     |
|  APScheduler     |   /sbin/poweroff              |   VM 3 (on)     |
|  Paramiko        |                               |   ...            |
+------------------+                               +------------------+
    :8080 (web UI)
```

## Prerequisites

- Docker and Docker Compose
- SSH access to the ESXi host (password or key-based)
- ESXi Shell enabled on the host

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USER/esxi-shutdown-manager.git
cd esxi-shutdown-manager
```

### 2. Run the deploy script

```bash
chmod +x deploy.sh
sudo ./deploy.sh
```

This will:
- Install Docker if not present
- Generate an SSH key pair for ESXi access
- Display the public key with instructions to add it to your ESXi host
- Create a `.env` file for configuration
- Build and start the Docker container

### 3. Configure

Edit `.env` with your ESXi host details:

```bash
ESXI_HOST=esxi.local       # Your ESXi hostname or IP
ESXI_USER=root              # SSH username (usually root)
TZ=America/Los_Angeles      # Timezone for schedule display and execution
SHUTDOWN_WAIT=180           # Seconds to wait for VMs to shut down before powering off host
```

Then restart the container:

```bash
docker compose up -d
```

### 4. Add the SSH key to ESXi

The deploy script generates a key pair in `./ssh/`. Add the public key to your ESXi host:

```bash
# SSH into your ESXi host, then:
cat >> /etc/ssh/keys-root/authorized_keys << 'EOF'
<paste contents of ./ssh/id_rsa.pub>
EOF

# Persist across reboots:
/sbin/auto-backup.sh
```

### 5. Access the dashboard

Open `http://<your-host-ip>:8080` in a browser.

## Configuration

All configuration is via environment variables (set in `.env`):

| Variable | Default | Description |
|---|---|---|
| `ESXI_HOST` | `esxi.local` | ESXi hostname or IP address |
| `ESXI_USER` | `root` | SSH username for ESXi |
| `ESXI_KEY_PATH` | `/app/ssh/id_rsa` | Path to SSH private key (inside container) |
| `ESXI_PASSWORD` | *(empty)* | Fallback password if no SSH key (not recommended) |
| `TZ` | `America/Los_Angeles` | Timezone for schedule and logs |
| `SHUTDOWN_WAIT` | `180` | Seconds to wait after sending VM shutdown commands before powering off host |
| `DB_PATH` | `/app/data/shutdown_manager.db` | SQLite database path (inside container) |

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/vms` | List all VMs with power states |
| `GET` | `/api/schedule` | Get the weekly shutdown schedule |
| `POST` | `/api/schedule` | Update the weekly schedule |
| `GET` | `/api/next` | Get the next scheduled shutdown time |
| `POST` | `/api/shutdown` | Trigger immediate graceful shutdown |
| `POST` | `/api/skip` | Skip shutdown for a specific date |
| `DELETE` | `/api/skip` | Remove a skip for a specific date |
| `GET` | `/api/skips` | List all scheduled skips |
| `GET` | `/api/logs` | Get activity log (default: last 50 entries) |
| `GET` | `/api/test` | Test SSH connection to ESXi |

## Data Persistence

- **Schedule and logs** are stored in SQLite at `./data/shutdown_manager.db` (mounted as a Docker volume)
- **SSH keys** are stored in `./ssh/` (mounted read-only into the container)
- Both directories are excluded from git via `.gitignore`

## Running on the Same ESXi Host

If this app runs as a VM on the ESXi host it manages, the shutdown sequence still works correctly:

1. The app triggers the shutdown sequence via SSH
2. All VMs (including this one) receive graceful shutdown commands
3. The app VM shuts down, but the ESXi-side process continues
4. After the wait period, the ESXi host powers off

When the ESXi host is powered back on and this VM starts, the schedule resumes automatically.

## ESXi Host Setup Notes

- **Enable SSH:** Host > Actions > Services > Enable Secure Shell (SSH)
- **Persist SSH keys:** After adding the public key, run `/sbin/auto-backup.sh` on the ESXi host to save the config to the boot bank. Otherwise, keys are lost on reboot.
- **VMware Tools:** Guest VMs need VMware Tools installed for graceful shutdown (`power.shutdown`) to work. Without it, the shutdown command will fail for that VM.

## License

MIT
