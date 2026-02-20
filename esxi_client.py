import paramiko
import os
import re
import logging
import threading

ESXI_HOST = os.environ.get("ESXI_HOST", "rack1.springfield")
ESXI_USER = os.environ.get("ESXI_USER", "root")
ESXI_KEY_PATH = os.environ.get("ESXI_KEY_PATH", "/app/ssh/id_rsa")
ESXI_PASSWORD = os.environ.get("ESXI_PASSWORD", "")
SHUTDOWN_WAIT = int(os.environ.get("SHUTDOWN_WAIT", "180"))  # seconds to wait for VMs

log = logging.getLogger(__name__)


def _connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(hostname=ESXI_HOST, username=ESXI_USER, timeout=10)
    if os.path.exists(ESXI_KEY_PATH):
        kwargs["key_filename"] = ESXI_KEY_PATH
    elif ESXI_PASSWORD:
        kwargs["password"] = ESXI_PASSWORD
    else:
        raise RuntimeError("No SSH key or password configured for ESXi")
    client.connect(**kwargs)
    return client


def _run(cmd, timeout=30):
    client = _connect()
    try:
        stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode()
        err = stderr.read().decode()
        return out, err
    finally:
        client.close()


def get_vms():
    out, _ = _run("vim-cmd vmsvc/getallvms")
    vms = []
    for line in out.strip().split("\n")[1:]:
        match = re.match(r"^(\d+)\s+(\S+.*?)\s+\[", line)
        if match:
            vmid = match.group(1)
            name = match.group(2).strip()
            vms.append({"vmid": vmid, "name": name})
    # get power state for each
    for vm in vms:
        try:
            out, _ = _run(f"vim-cmd vmsvc/power.getstate {vm['vmid']}")
            state_line = out.strip().split("\n")[-1]
            vm["state"] = state_line
        except Exception:
            vm["state"] = "Unknown"
    return vms


def _get_powered_on_vmids():
    out, _ = _run("vim-cmd vmsvc/getallvms")
    vmids = []
    for line in out.strip().split("\n")[1:]:
        match = re.match(r"^(\d+)\s+", line)
        if match:
            vmids.append(match.group(1))

    powered_on = []
    for vmid in vmids:
        try:
            out, _ = _run(f"vim-cmd vmsvc/power.getstate {vmid}")
            if "Powered on" in out:
                powered_on.append(vmid)
        except Exception:
            pass
    return powered_on


def _fire_and_forget(cmd):
    """Send a command over SSH without waiting for output."""
    client = _connect()
    try:
        transport = client.get_transport()
        channel = transport.open_session()
        channel.exec_command(f"nohup {cmd} >/dev/null 2>&1 &")
        import time
        time.sleep(5)
    finally:
        client.close()


def _do_graceful_shutdown(log_fn=None):
    """Gracefully shut down all VMs, wait, then power off host."""
    def _log(event, details=None):
        try:
            if log_fn:
                log_fn(event, details)
        except Exception:
            pass
        log.info(f"{event}: {details}")

    try:
        _log("shutdown_start", "Connecting to ESXi...")
        powered_on = _get_powered_on_vmids()
        _log("shutdown_progress", f"Found {len(powered_on)} powered-on VMs")

        # Schedule the host poweroff ON the ESXi host FIRST, before shutting down VMs.
        # This app runs as a VM on the ESXi host, so it will die when VMs shut down.
        # The sleep+poweroff runs as a detached process on ESXi and survives VM shutdown.
        _log("shutdown_progress", f"Scheduling host poweroff in {SHUTDOWN_WAIT}s on ESXi")
        _fire_and_forget(f"sh -c 'sleep {SHUTDOWN_WAIT} && /sbin/poweroff'")

        for vmid in powered_on:
            try:
                _run(f"vim-cmd vmsvc/power.shutdown {vmid}")
                _log("shutdown_progress", f"Sent shutdown to VM {vmid}")
            except Exception as e:
                _log("error", f"Failed to shut down VM {vmid}: {e}")

        _log("shutdown_complete", f"Shut down {len(powered_on)} VMs, host will power off in ~{SHUTDOWN_WAIT}s")
    except Exception as e:
        _log("error", f"Shutdown failed: {e}")


def trigger_shutdown(log_fn=None):
    """Run shutdown in a background thread so the API returns immediately."""
    t = threading.Thread(target=_do_graceful_shutdown, args=(log_fn,), daemon=True)
    t.start()
    return t


def test_connection():
    try:
        out, _ = _run("echo ok")
        return "ok" in out
    except Exception as e:
        return str(e)
