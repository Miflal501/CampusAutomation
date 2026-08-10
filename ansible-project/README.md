# EE8203 - Ansible Switch Automation

Automates VLANs, trunking, access ports, and STP on all distribution
(SW-D-\*) and access (SW-A-\*) switches, per Section 4.2 of the project spec.

## 1. One-time setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install ansible netaddr

ansible-galaxy collection install -r requirements.yml
```

Confirm the collection is present:

```bash
ansible-galaxy collection list | grep cisco.ios
```

## 2. Generate the inventory from devices.yaml (single source of truth)

`devices.yaml` (already used by `../netmiko-automation`) is where switch
IPs and credentials actually live. This project never hardcodes them -
it generates `inventory/hosts.yml` from it instead:

```bash
python3 scripts/generate_inventory.py
```

By default it looks for `devices.yaml` at
`../netmiko-automation/devices.yaml`. Point it elsewhere with
`--devices-file /path/to/devices.yaml` if your layout differs.

Re-run this any time `devices.yaml` changes (new switch, changed
password, etc.) - `inventory/hosts.yml` is a generated artefact and is
gitignored; `inventory/hosts.yml.example` shows its shape for reference.

## 3. Verify connectivity before touching config

```bash
ansible all -m ping
```

Every host must return `SUCCESS`. If a host fails, fix SSH/VLAN 99
reachability before proceeding (see MOP Section 3 pre-checks) - do not
run the playbooks against unreachable devices.

## 4. Take a rollback baseline (do this FIRST, before site.yml ever runs)

```bash
ansible-playbook playbooks/rollback/backup.yml
```

This saves each switch's current running-config under
`playbooks/rollback/baselines/<hostname>.cfg`. These files are
git-ignored (they contain live device config) - keep a copy outside
git as your actual safety net.

## 5. Dry-run the whole build

```bash
ansible-playbook site.yml --check --diff
```

Read the diff. Nothing should look surprising - if it does, stop and
investigate before applying for real.

## 6. Apply for real

```bash
ansible-playbook site.yml
```

## 7. Prove idempotency (required evidence, Section 7.1)

```bash
ansible-playbook site.yml --check
```

Run this immediately after step 5. It must report `changed=0` for
every host. Capture this output as your idempotency evidence.

## 8. Rollback (if something goes wrong, or for the live demo)

```bash
# one device
ansible-playbook playbooks/rollback/rollback.yml --limit SW-A-DEIE

# everything
ansible-playbook playbooks/rollback/rollback.yml
```

## Project layout

```
ansible.cfg              connection defaults, inventory path
scripts/generate_inventory.py   builds inventory/hosts.yml from devices.yaml
inventory/hosts.yml       GENERATED (gitignored) - distribution + access groups
inventory/hosts.yml.example   checked-in reference showing its shape
group_vars/all.yml        SSH/enable creds, master VLAN list
group_vars/distribution.yml   STP priority scheme for dist switches
group_vars/access.yml    access-layer defaults
host_vars/<hostname>.yml department VLAN, trunk/access interfaces, SVI
roles/vlans/             creates VLANs + department SVIs (dist only)
roles/trunking/          trunk encapsulation, allowed VLANs, native VLAN
roles/access_ports/      switchport mode access + VLAN + portfast/bpduguard
roles/stp/               global STP mode + per-VLAN root priority
site.yml                 orchestrates the four roles, in order
playbooks/rollback/      backup.yml (baseline) + rollback.yml (restore)
```

## Tool selection justification (Netmiko vs Ansible) - summary for report/MOP

- **Nature of the task**: router config (NAT, ACLs, OSPF) is largely
  imperative and stateful, with logic that's easier to express as
  procedural Python (Netmiko) than as declarative resource modules.
  Switch config (VLANs, trunks, access ports, STP) across 7 near-identical
  devices is repetitive and structural - a natural fit for Ansible's
  inventory + role model.
- **Scalability**: adding an 8th switch here means one new host_vars
  file; adding a 3rd router in the Netmiko script means new
  branching logic in Python. Ansible scales better horizontally
  for many similar devices; Netmiko scales better when device
  configs genuinely differ in structure.
- **Idempotency**: `cisco.ios` resource modules (`ios_vlans`,
  `ios_l2_interfaces`, `ios_l3_interfaces`, `ios_interfaces`) read
  current device state and compute a diff before pushing anything,
  so idempotency is built into the module rather than hand-coded.
  The Netmiko scripts approximate the same result manually (read
  `show run`, string-match, only push what's missing) - which works
  but is more error-prone and more code per idempotent check.
- **Limitations**: Ansible's resource modules only cover config
  areas Cisco has written modules for - plain-text `ios_config` lines
  (used here for descriptions, portfast, STP priority) fall back to
  the same "did this line already exist" style of idempotency as the
  Netmiko approach. Netmiko has no built-in concept of roles/inventory
  reuse and no dry-run (`--check`) equivalent, so its idempotency and
  scale must be engineered by hand.
