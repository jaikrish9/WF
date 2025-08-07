import csv
import os
import sys
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

CSV_FILE = "cluster1_namespaces.csv"
OUTPUT_FILE = f"ephemeral_runner_report_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
TEMP_OUTPUT = "ephemeral_runners_report.csv"
TMP_DIR = os.path.join(os.environ.get("TMP", "/tmp"), f"runner_data_{os.getpid()}")
MAX_PARALLEL = 10

def prompt_credentials():
    while True:
        username = input("Enter username: ").strip()
        if username:
            break
        print(" Username is required. Please try again.")
    password = input("Enter password: ")
    return username, password

def read_clusters_and_namespaces(csv_file):
    clusters = {}
    with open(csv_file, newline='') as f:
        reader = csv.reader(f)
        for row in reader:
            cluster, api, ns = row[:3]
            key = (cluster, api)
            clusters.setdefault(key, set()).add(ns)
    return clusters

def authenticate(cluster_name, api_endpoint, username, password):
    cmd = [
        "tkgi", "get-kubeconfig", cluster_name,
        "-u", username, "-a", api_endpoint, "-k"
    ]
    # Pass password via stdin so it is not prompted again
    proc = subprocess.run(cmd, input=password + "\n", text=True)
    if proc.returncode != 0:
        print(f"Authentication failed for {cluster_name}.")
    return proc.returncode == 0

def process_namespace(cluster_name, api_endpoint, ns, now):
    cmd = [
        "kubectl", "get", "ephemeralrunner", "-n", ns,
        "-o", "custom-columns=NAME:.metadata.name,CONFIG_URL:.spec.githubConfigUrl,RUNNERID:.status.runnerId,READY:.status.readyReplicas,TOTAL:.status.replicas,AGE:.metadata.creationTimestamp",
        "--no-headers"
    ]
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return []
    results = []
    for line in output.strip().splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        name, config_url, runner_id, ready, total, creation_ts = parts
        org_name = config_url.split('/')[3] if '/' in config_url else ""
        try:
            created = datetime.strptime(creation_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_min = int((now - created).total_seconds() / 60)
            age = f"{age_min}m"
        except Exception:
            age = "N/A"
        if ready == total and ready and total and ready != "0":
            status = "Running"
        elif ready == "0":
            status = "Failed"
        else:
            status = "Pending"
        results.append([cluster_name, api_endpoint, ns, name, config_url, org_name, runner_id, age, status])
    return results

def main():
    if not os.path.isfile(CSV_FILE):
        print(f" CSV file '{CSV_FILE}' not found.")
        sys.exit(1)
    os.makedirs(TMP_DIR, exist_ok=True)
    username, password = prompt_credentials()
    clusters = read_clusters_and_namespaces(CSV_FILE)
    now = datetime.now(timezone.utc)


    import argparse
    parser = argparse.ArgumentParser(description="Ephemeral Runner Report Tool")
    parser.add_argument("option", nargs="?", choices=["summary", "-summary", "running", "-running", "pending", "-pending", "failed", "-failed", "DeletePending", "-DeletePending", "DeleteFailed", "-DeleteFailed", "details", "-details"], help="Reporting option")
    parser.add_argument("--org", dest="org_filter", default=None, help="Filter by organization")
    parser.add_argument("-AllOrgs", dest="all_orgs", action="store_true", help="Include all organizations")
    args = parser.parse_args()

    # Data collection phase
    with open(OUTPUT_FILE, "w", newline='') as out_csv:
        writer = csv.writer(out_csv)
        writer.writerow(["Cluster", "API_Endpoint", "Namespace", "Runner_Name", "GitHub_Config_URL", "Org_Name", "Runner_ID", "Age", "Status"])
        for (cluster_name, api_endpoint), namespaces in clusters.items():
            print(f"\n=========================")
            print(f"Cluster: {cluster_name}")
            print(f"API:     {api_endpoint}")
            print(f"=========================")
            if not authenticate(cluster_name, api_endpoint, username, password):
                print(f" Authentication failed for {cluster_name}. Skipping.")
                continue
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
                futures = {executor.submit(process_namespace, cluster_name, api_endpoint, ns, now): ns for ns in namespaces}
                for future in as_completed(futures):
                    results = future.result()
                    for row in results:
                        writer.writerow(row)
    import shutil
    shutil.copyfile(OUTPUT_FILE, TEMP_OUTPUT)
    print(f"\n Data collection complete. Output saved to: {OUTPUT_FILE}\n")

    # Reporting and deletion phase
    if not args.option:
        print("Usage: python clutseratcc2.py [option] [--org <ORG>] [-AllOrgs]")
        print("Options:")
        print("  -summary         Show summary of all runners by org")
        print("  -running         Show running runners grouped by org")
        print("  -pending         Show pending runners grouped by org")
        print("  -failed          Show failed runners grouped by org")
        print("  -DeletePending   Delete pending runners (interactive)")
        print("  -DeleteFailed    Delete failed runners (interactive)")
        print("  -details         Show raw output of kubectl get ephemeralrunner for each namespace")
        print("  --org <ORG>      Filter by organization")
        print("  -AllOrgs         Include all organizations (default behavior)")
        sys.exit(0)
    if args.option in ["details", "-details"]:
        # Print detailed runner info from CSV file, grouped by cluster and namespace
        from collections import defaultdict
        details = defaultdict(lambda: defaultdict(list))  # cluster -> ns -> list of rows
        for row in rows:
            details[(row["Cluster"], row["API_Endpoint"])][row["Namespace"]].append(row)
        for (cluster_name, api_endpoint), ns_map in details.items():
            print(f"\n=========================")
            print(f"Cluster: {cluster_name}")
            print(f"API:     {api_endpoint}")
            print(f"=========================")
            ns_checked = 0
            total_runners = 0
            running_count = 0
            not_running_count = 0
            for ns, runner_rows in ns_map.items():
                print(f"\nNamespace: {ns}")
                print(f"{'Runner_Name':<30} {'GitHub_Config_URL':<40} {'Org_Name':<20} {'Runner_ID':<10} {'Age':<8} {'Status':<10}")
                print("-"*120)
                for row in runner_rows:
                    print(f"{row['Runner_Name']:<30} {row['GitHub_Config_URL']:<40} {row['Org_Name']:<20} {row['Runner_ID']:<10} {row['Age']:<8} {row['Status']:<10}")
                    total_runners += 1
                    if row['Status'].lower() == 'running':
                        running_count += 1
                    else:
                        not_running_count += 1
                ns_checked += 1
            print(f"\n----- Cluster Summary -----")
            print(f"Namespaces checked: {ns_checked}")
            print(f"Total runners: {total_runners}")
            print(f"Count of runners in running state: {running_count}")
            print(f"Count of runners in not running state: {not_running_count}")
            print(f"--------------------------")
        sys.exit(0)

    # Read CSV for reporting
    with open(TEMP_OUTPUT, newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    def print_table():
        print("------------------------------------------------------------------------------------------")
        print(f"{'Runner Name':<40} {'Org Name':<40} {'Age':<20} {'Count':>10}")
        print("------------------------------------------------------------------------------------------")

    if args.option in ["summary", "-summary"]:
        print()
        header_fmt = "{:<35} {:>10} {:>10} {:>10} {:>10}"
        row_fmt = "{:<35} {:>10} {:>10} {:>10} {:>10}"
        print(header_fmt.format("Org Name", "Running", "Failed", "Pending", "Total"))
        print("-------------------------------------------------------------------------------")
        summary = {}
        for row in rows:
            org = row["Org_Name"].lower()
            status = row["Status"].lower()
            summary.setdefault(org, {"running":0, "failed":0, "pending":0, "total":0})
            summary[org]["total"] += 1
            if status == "running": summary[org]["running"] += 1
            elif status == "failed": summary[org]["failed"] += 1
            elif status == "pending": summary[org]["pending"] += 1
        grand = {"running":0, "failed":0, "pending":0, "total":0}
        for org, stats in summary.items():
            print(row_fmt.format(org, stats["running"], stats["failed"], stats["pending"], stats["total"]))
            for k in grand: grand[k] += stats[k]
        print("-------------------------------------------------------------------------------")
        print(row_fmt.format("Total", grand["running"], grand["failed"], grand["pending"], grand["total"]))

    elif args.option in ["running", "-running", "pending", "-pending", "failed", "-failed"]:
        status_lc = args.option.replace("-", "").lower()
        org_filter = args.org_filter.lower() if args.org_filter else ""
        all_orgs = args.all_orgs
        print_table()
        runners = {}
        count = {}
        grand_total = 0
        for row in rows:
            actual_status = row["Status"].lower()
            org = row["Org_Name"]
            org_lc = org.lower()
            if actual_status == status_lc and (all_orgs or not org_filter or org_filter == org_lc):
                key = org
                runners.setdefault(key, []).append((row["Runner_Name"], org, row["Age"]))
                count[key] = count.get(key, 0) + 1
                grand_total += 1
        for org, runner_list in runners.items():
            for fields in runner_list:
                print(f"{fields[0]:<40} {fields[1]:<40} {fields[2]:<40} {'':>10}")
            print(f"{'Total':<100} {count[org]:>8}\n")
        print("------------------------------------------------------------------------------------------")
        print(f"{'Grand Total':<100} {grand_total:>8}")

    elif args.option in ["DeletePending", "-DeletePending", "DeleteFailed", "-DeleteFailed"]:
        status = args.option.replace("-Delete", "").replace("Delete", "").lower()
        org_filter = args.org_filter.lower() if args.org_filter else ""
        all_orgs = args.all_orgs
        print_table()
        runners = {}
        count = {}
        grand_total = 0
        delete_cmds = []
        for row in rows:
            actual_status = row["Status"].lower()
            org = row["Org_Name"]
            org_lc = org.lower()
            if actual_status == status and (all_orgs or not org_filter or org_filter == org_lc):
                cluster = row["Cluster"]
                namespace = row["Namespace"]
                pod_name = row["Runner_Name"]
                age = row["Age"]
                runners.setdefault(org, []).append((pod_name, org, age, cluster, namespace))
                count[org] = count.get(org, 0) + 1
                grand_total += 1
                delete_cmds.append(f"tkgi get-kubeconfig {cluster} && kubectl -n {namespace} delete pod {pod_name}")
        for org, runner_list in runners.items():
            for fields in runner_list:
                print(f"{fields[0]:<35} {fields[1]:<25} {fields[2]:<10} {'':>10}")
            print(f"{'Total':<72} {count[org]:>8}\n")
        print("------------------------------------------------------------------------------------------")
        print(f"{'Grand Total':<72} {grand_total:>8}")
        # Write delete commands to a shell script
        delete_script = f"/tmp/delete_{status}_runners.sh"
        with open(delete_script, "w") as f:
            f.write("\n".join(delete_cmds))
        print(f"Delete commands written to {delete_script}")
        confirm = input("Do you want to delete these runners? (Y/N): ").strip().lower()
        if confirm == "y":
            print("\nExecuting delete commands...")
            print("-------------------------------------------------------------")
            subprocess.run(["bash", delete_script])
            print("-------------------------------------------------------------")
        else:
            print(" Skipping deletion. No runners were deleted.")

if __name__ == "__main__":
    main()
