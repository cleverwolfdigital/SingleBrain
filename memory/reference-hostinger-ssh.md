---
name: reference-hostinger-ssh
description: Passwordless SSH/scp access from this machine to the Hostinger server (root@srv1763128.hstgr.cloud)
metadata: 
  node_type: memory
  type: reference
  originSessionId: c86dd7d9-e4a4-45cb-b028-123a624c8d4e
---

Passwordless SSH key auth is set up from this Windows machine to the Hostinger VPS.

- **Host alias:** `hostinger` (defined in `C:\Users\tidas\.ssh\config`) → `root@srv1763128.hstgr.cloud`
- **Key:** `C:\Users\tidas\.ssh\hostinger_singlebrain_ed25519` (ed25519, no passphrase)
- **Usage:** `ssh hostinger` · `scp hostinger:/root/singlebrain/<path> "<local>"` · `scp -r` for folders. No `-i` needed.
- **Server layout:** `/root/singlebrain/` mirrors the SingleBrain repo (AGENTS.md, OFFERS_STATE.md, …) and has a `CWD-Hermes/` subfolder.

**Gotcha that cost an hour:** the server's `~/.ssh/authorized_keys` had no trailing newline, so `echo "<key>" >>` glued the new key onto the end of Hostinger's existing key line and SSH ignored it. Fix was to split it onto its own line. To add keys in future use `ssh-copy-id` or prepend a newline. Not a password/PermitRootLogin problem — root login + pubkey were fine all along.
