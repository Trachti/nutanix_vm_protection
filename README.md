# Nutanix VM Protection Script

A Python script for creating Nutanix VM recovery points, Prism Element snapshots, or both.

The script looks up a VM by name in Nutanix Prism Central, creates a Nutanix v4 Data Protection recovery point through Prism Central, and can also create a Prism Element v2.0 snapshot on the correct Prism Element cluster.

## Features

- Finds a VM by name in Nutanix Prism Central
- Creates Nutanix v4 Data Protection recovery points
- Creates Prism Element v2.0 snapshots
- Supports `recovery`, `snapshot`, and `both` modes
- Adds a unique `NTNX-Request-Id` header for v4 API calls
- Maps cluster UUIDs to Prism Element API hosts
- Waits for Prism Element snapshot tasks to complete
- Prints a structured result summary
- Provides basic error classification for common permission and request issues

## Important Authentication Requirement

The same user account must exist on Prism Central and on every configured Prism Element cluster.

The username and password must be identical everywhere. This script uses one shared authentication token for Prism Central and all Prism Element API calls. Different users, different passwords, or separate credentials per Prism Element are not supported by this script.

## Requirements

- Python 3.8 or newer
- Network access to Nutanix Prism Central
- Network access to the configured Prism Element clusters
- A valid Nutanix API token
- Correct cluster UUID to Prism Element host mappings
- The same user with the same password configured on Prism Central and all Prism Element clusters

This project uses only the Python standard library. No external Python packages are required.

## Configuration

Before running the script, update the following values in `nutanix_vm_protection.py`:

```python
NTNX_PRISMCENTRAL_IP = "YOUR_IP:9440"
PE_AND_PC_TOKEN = "YOUR GENERATED TOKEN FROM nutanix_auth.py"
```

The token must be generated for a user that exists with the same password on Prism Central and on all configured Prism Element clusters.

Then configure your Prism Element hosts by cluster UUID:

```python
PE_HOSTS_BY_CLUSTER_UUID = {
    "UUID_FROM_CLUSTER_1": "IP_FROM_ELEMENTS_CLUSTER_1:9440",
    "UUID_FROM_CLUSTER_2": "IP_FROM_ELEMENTS_CLUSTER_2:9440",
    "UUID_FROM_CLUSTER_3": "IP_FROM_ELEMENTS_CLUSTER_3:9440",
}
```

The cluster UUID is read from the VM metadata or spec. The script uses that UUID to find the correct Prism Element host for snapshot creation.

## Usage

Create only a recovery point:

```bash
python nutanix_vm_protection.py \
  --vm my-vm01 \
  --mode recovery \
  --retention-days 7 \
  --name backup-my-vm01
```

Create only a snapshot:

```bash
python nutanix_vm_protection.py \
  --vm my-vm01 \
  --mode snapshot \
  --retention-days 7 \
  --name backup-my-vm01
```

Create both a recovery point and a snapshot:

```bash
python nutanix_vm_protection.py \
  --vm my-vm01 \
  --mode both \
  --retention-days 7 \
  --name backup-my-vm01
```

## Arguments

| Argument | Required | Description |
|---|---:|---|
| `--vm` | Yes | Name of the VM |
| `--mode` | Yes | `recovery`, `snapshot`, or `both` |
| `--retention-days` | Yes | Retention time in days for the recovery point; reserved for snapshot mode |
| `--name` | Yes | Base name used for the recovery point or snapshot |

## Naming

The script automatically appends suffixes to the base name:

| Mode | Generated name |
|---|---|
| Recovery point | `<base-name>-rp` |
| Snapshot | `<base-name>-snap` |

Example:

```bash
--name backup-my-vm01
```

Generated names:

```text
backup-my-vm01-rp
backup-my-vm01-snap
```

## Example Output

```text
Creating recovery point for VM 'my-vm01' ...
API used: Nutanix v4 Data Protection through Prism Central

Result

------------------------------------------------------------
Type: recovery_point
API version: v4
API host: YOUR_IP:9440
VM: my-vm01
Name: backup-my-vm01-rp
Successful: True
VM UUID: 00000000-0000-0000-0000-000000000000
Retention days: 7
ID: 00000000-0000-0000-0000-000000000000
Request ID: 00000000-0000-0000-0000-000000000000
HTTP status: 202

All requested actions completed successfully.
```

## Security Notes

Do not commit real API tokens, passwords, cluster UUIDs, Prism Central addresses, Prism Element addresses, or internal infrastructure details to a public GitHub repository.

The script currently disables SSL certificate verification by using:

```python
ssl._create_unverified_context()
```

This may be useful in lab environments, but it is not recommended for production. For production use, configure proper certificate validation.

## Disclaimer

This script is provided as an example. Test it in a safe environment before using it against production Nutanix infrastructure.
