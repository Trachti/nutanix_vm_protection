import http.client
import json
import argparse
import time
import ssl
import uuid
from datetime import datetime, timedelta, timezone

NTNX_PRISMCENTRAL_IP = "YOUR_IP:9440"
PE_AND_PC_TOKEN = "YOUR GENERATED TOKEN FROM nutanix_auth.py"

PE_HOSTS_BY_CLUSTER_UUID = {
    "UUID_FROM_CLUSTER_1": "IP_FROM_ELEMENTS_CLUSTER_1:9440",  # data center 1
    "UUID_FROM_CLUSTER_2": "IP_FROM_ELEMENTS_CLUSTER_2:9440",  # data center 2
    "UUID_FROM_CLUSTER_3": "IP_FROM_ELEMENTS_CLUSTER_3:9440",  # data center 3
}

def get_conn(api_host):
    context = ssl._create_unverified_context()
    return http.client.HTTPSConnection(api_host, context=context)

def extract_error_message(data):
    # v4 error format
    err_list = data.get("data", {}).get("error", [])
    if err_list:
        first = err_list[0]
        return {
            "message": first.get("message", "Unknown error"),
            "code": first.get("code", "n/a"),
            "group": first.get("errorGroup", "n/a"),
            "raw": data
        }

    # Cluster API v2
    if "message" in data:
        return {
            "message": data.get("message", "Unknown error"),
            "code": data.get("error_code", {}).get("code", "n/a") if isinstance(data.get("error_code"), dict) else data.get("error_code", "n/a"),
            "group": "n/a",
            "raw": data
        }

    return {
        "message": str(data),
        "code": "n/a",
        "group": "n/a",
        "raw": data
    }

def api_request(api_host, method, url, payload=None, extra_headers=None):
    conn = get_conn(api_host)
    headers = {
        "Accept": "application/json",
        "Authorization": PE_AND_PC_TOKEN,
        "Content-Type": "application/json"
    }

    if extra_headers:
        headers.update(extra_headers)

    body = None
    if payload is not None:
        body = payload if isinstance(payload, str) else json.dumps(payload)

    conn.request(method, url, body=body, headers=headers)
    res = conn.getresponse()
    raw = res.read().decode("utf-8")

    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {"raw": raw}

    if res.status >= 400:
        err = extract_error_message(data)
        raise RuntimeError(
            json.dumps({
                "http_status": res.status,
                "api_host": api_host,
                "url": url,
                "message": err["message"],
                "code": err["code"],
                "group": err["group"],
                "raw": err["raw"]
            }, ensure_ascii=False)
        )

    return data, res.status

def parse_runtime_error(exc):
    try:
        return json.loads(str(exc))
    except Exception:
        return {
            "http_status": "n/a",
            "api_host": "n/a",
            "url": "n/a",
            "message": str(exc),
            "code": "n/a",
            "group": "n/a",
            "raw": {}
        }

def classify_permission_issue(err):
    status = err.get("http_status")
    code = str(err.get("code", ""))
    message = str(err.get("message", "")).lower()
    url = str(err.get("url", "")).lower()

    if status == 403 and code == "PLAT-10007":
        return "IAM/RBAC error on Nutanix v4. The account is authenticated, but not authorized for this recovery point endpoint."

    if status == 400 and code == "DP-10200":
        return "Required NTNX-Request-Id header is missing or invalid. For v4 POST/PUT/DELETE requests, a UUID must be provided in the header."

    if status == 403 and "/api/nutanix/v2.0/snapshots/" in url:
        return "Permission error on Prism Element v2.0. The account is not allowed to create snapshots on this cluster/PE."

    if status == 403 and "access is denied" in message:
        return "Permission error. The endpoint is reachable, but the account does not have sufficient privileges."

    return "General API error."

def get_vm_by_name(server):
    offset = 0
    while offset <= 500:
        payload = {
            "kind": "vm",
            "length": 50,
            "offset": offset
        }
        data, _ = api_request(NTNX_PRISMCENTRAL_IP, "POST", "/api/nutanix/v3/vms/list", payload)
        entities = data.get("entities", [])

        for elem in entities:
            if elem.get("spec", {}).get("name") == server:
                return elem

        if not entities:
            break
        offset += 50

    return None

def get_vm(uuid_):
    data, _ = api_request(NTNX_PRISMCENTRAL_IP, "GET", f"/api/nutanix/v3/vms/{uuid_}")
    return data

def get_vm_cluster_uuid(vm_data):
    return (
        vm_data.get("status", {}).get("cluster_reference", {}).get("uuid")
        or vm_data.get("spec", {}).get("cluster_reference", {}).get("uuid")
    )

def get_pe_host_for_vm(vm_data):
    cluster_uuid = get_vm_cluster_uuid(vm_data)
    if not cluster_uuid:
        raise RuntimeError("Could not determine the VM cluster UUID.")

    pe_host = PE_HOSTS_BY_CLUSTER_UUID.get(cluster_uuid)
    if not pe_host:
        raise RuntimeError(
            f"No Prism Element host configured for cluster UUID {cluster_uuid}. "
            f"Please update PE_HOSTS_BY_CLUSTER_UUID."
        )

    return pe_host

def wait_for_pe_task(pe_host, task_uuid, timeout=300, interval=5):
    url = f"/api/nutanix/v2.0/tasks/{task_uuid}"
    start = time.time()

    while time.time() - start < timeout:
        data, _ = api_request(pe_host, "GET", url)

        status = str(
            data.get("progress_status")
            or data.get("status")
            or data.get("operation_type")
            or ""
        ).upper()

        percentage = data.get("percentage_complete")

        if "FAILED" in status or "ABORTED" in status:
            raise RuntimeError(f"PE task {task_uuid} failed: {data}")

        if any(x in status for x in ["SUCCEEDED", "COMPLETE", "COMPLETED"]):
            return data

        if percentage == 100:
            return data

        time.sleep(interval)

    raise TimeoutError(f"PE task {task_uuid} reached timeout after {timeout}s.")

def create_recovery_point(vm_name, rp_name, retention_days):
    vm = get_vm_by_name(vm_name)
    if not vm:
        raise RuntimeError(f"VM '{vm_name}' was not found.")

    vm_uuid = vm.get("metadata", {}).get("uuid")
    if not vm_uuid:
        raise RuntimeError(f"Could not determine UUID for VM '{vm_name}'.")

    expiration_time = (
        datetime.now(timezone.utc) + timedelta(days=retention_days)
    ).isoformat().replace("+00:00", "Z")

    payload = {
        "name": rp_name,
        "expirationTime": expiration_time,
        "recoveryPointType": "CRASH_CONSISTENT",
        "vmRecoveryPoints": [
            {
                "vmExtId": vm_uuid
            }
        ]
    }

    extra_headers = {
        "NTNX-Request-Id": str(uuid.uuid4())
    }

    response, status_code = api_request(
        NTNX_PRISMCENTRAL_IP,
        "POST",
        "/api/dataprotection/v4.0/config/recovery-points",
        payload,
        extra_headers=extra_headers
    )

    return {
        "success": True,
        "kind": "recovery_point",
        "api_version": "v4",
        "api_host": NTNX_PRISMCENTRAL_IP,
        "http_status": status_code,
        "vm_name": vm_name,
        "vm_uuid": vm_uuid,
        "name": rp_name,
        "retention_days": retention_days,
        "id": response.get("data", {}).get("extId"),
        "request_id": extra_headers["NTNX-Request-Id"],
        "raw_response": response
    }

def create_snapshot(vm_name, snapshot_name):
    vm = get_vm_by_name(vm_name)
    if not vm:
        raise RuntimeError(f"VM '{vm_name}' was not found.")

    vm_uuid = vm.get("metadata", {}).get("uuid")
    if not vm_uuid:
        raise RuntimeError(f"Could not determine UUID for VM '{vm_name}'.")

    full_vm = get_vm(vm_uuid)
    pe_host = get_pe_host_for_vm(full_vm)

    payload = {
        "snapshot_specs": [
            {
                "vm_uuid": vm_uuid,
                "snapshot_name": snapshot_name
            }
        ]
    }

    response, status_code = api_request(
        pe_host,
        "POST",
        "/api/nutanix/v2.0/snapshots/",
        payload
    )

    task_uuid = (
        response.get("task_uuid")
        or response.get("taskUuid")
        or response.get("uuid")
        or response.get("metadata", {}).get("uuid")
    )

    if task_uuid:
        print(f"Snapshot task started: {task_uuid}")
        wait_for_pe_task(pe_host, task_uuid)

    return {
        "success": True,
        "kind": "snapshot",
        "api_version": "v2.0",
        "api_host": pe_host,
        "http_status": status_code,
        "vm_name": vm_name,
        "vm_uuid": vm_uuid,
        "name": snapshot_name,
        "pe_host": pe_host,
        "task_uuid": task_uuid,
        "raw_response": response
    }


def run_recovery(vm_name, base_name, retention_days):
    rp_name = f"{base_name}-rp"
    print(f"Creating recovery point for VM '{vm_name}' ...")
    print("API used: Nutanix v4 Data Protection through Prism Central")
    try:
        return create_recovery_point(vm_name, rp_name, retention_days)
    except Exception as e:
        err = parse_runtime_error(e)
        return {
            "success": False,
            "kind": "recovery_point",
            "api_version": "v4",
            "api_host": err.get("api_host"),
            "vm_name": vm_name,
            "name": rp_name,
            "http_status": err.get("http_status"),
            "error_code": err.get("code"),
            "error_group": err.get("group"),
            "error": err.get("message"),
            "diagnosis": classify_permission_issue(err)
        }

def run_snapshot(vm_name, base_name):
    snapshot_name = f"{base_name}-snap"
    print(f"Creating snapshot for VM '{vm_name}' ...")
    print("API used: Nutanix Prism Element v2.0")
    try:
        return create_snapshot(vm_name, snapshot_name)
    except Exception as e:
        err = parse_runtime_error(e)
        return {
            "success": False,
            "kind": "snapshot",
            "api_version": "v2.0",
            "api_host": err.get("api_host"),
            "vm_name": vm_name,
            "name": snapshot_name,
            "http_status": err.get("http_status"),
            "error_code": err.get("code"),
            "error_group": err.get("group"),
            "error": err.get("message"),
            "diagnosis": classify_permission_issue(err)
        }

def print_result(item):
    print("-" * 60)
    print(f"Type: {item.get('kind')}")
    print(f"API version: {item.get('api_version')}")
    print(f"API host: {item.get('api_host')}")
    print(f"VM: {item.get('vm_name')}")
    print(f"Name: {item.get('name')}")
    print(f"Successful: {item.get('success')}")

    if item.get("success"):
        if item.get("vm_uuid"):
            print(f"VM UUID: {item.get('vm_uuid')}")

        if item.get("kind") == "recovery_point":
            print(f"Retention days: {item.get('retention_days')}")
            print(f"ID: {item.get('id')}")
            print(f"Request ID: {item.get('request_id')}")
            print(f"HTTP status: {item.get('http_status')}")

        elif item.get("kind") == "snapshot":
            print(f"PE host: {item.get('pe_host')}")
            print(f"Task UUID: {item.get('task_uuid')}")
            print(f"HTTP status: {item.get('http_status')}")
    else:
        print(f"HTTP status: {item.get('http_status')}")
        print(f"Error code: {item.get('error_code')}")
        print(f"Error group: {item.get('error_group')}")
        print(f"Error: {item.get('error')}")
        print(f"Diagnosis: {item.get('diagnosis')}")

def main():
    parser = argparse.ArgumentParser(
        description="Creates a recovery point, a snapshot, or both for a VM."
    )

    parser.add_argument("--vm", required=True, type=str, help="VM name")
    parser.add_argument("--mode", required=True, choices=["recovery", "snapshot", "both"],
                        help="recovery = recovery point only, snapshot = snapshot only, both = both actions")
    parser.add_argument("--retention-days", required=True, type=int,
                        help="Retention in days for the recovery point; reserved for snapshot mode")
    parser.add_argument("--name", required=True, type=str, help="Base name for the recovery point or snapshot")

    args = parser.parse_args()

    results = []

    if args.mode == "recovery":
        results.append(run_recovery(args.vm, args.name, args.retention_days))
    elif args.mode == "snapshot":
        results.append(run_snapshot(args.vm, args.name))
    elif args.mode == "both":
        results.append(run_recovery(args.vm, args.name, args.retention_days))
        results.append(run_snapshot(args.vm, args.name))

    print("\nResult\n")
    for item in results:
        print_result(item)

    if all(r.get("success") for r in results):
        print("\nAll requested actions completed successfully.")
    elif any(r.get("success") for r in results):
        print("\nActions completed partially successfully.")
    else:
        print("\nNo action completed successfully.")
        raise SystemExit(1)

if __name__ == "__main__":
    main()
