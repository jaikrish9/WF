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
    # Run interactively so password prompt and output are visible
    proc = subprocess.run(cmd)
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
        for (cluster_name, api_endpoint), namespaces in clusters.items():
            print(f"\n=========================")
            print(f"Cluster: {cluster_name}")
            print(f"API:     {api_endpoint}")
            print(f"=========================")
            if not authenticate(cluster_name, api_endpoint, username, password):
                print(f" Authentication failed for {cluster_name}. Skipping.")
                continue
            ns_checked = 0
            total_runners = 0
            running_count = 0
            not_running_count = 0
            for ns in namespaces:
                print(f"\nNamespace: {ns}")
                print(f"kubectl get ephemeralrunner -n {ns}")
                try:
                    output = subprocess.check_output([
                        "kubectl", "get", "ephemeralrunner", "-n", ns
                    ], stderr=subprocess.STDOUT, text=True)
                    print(output)
                    ns_checked += 1
                    lines = output.strip().splitlines()
                    if lines and lines[0].strip().startswith("NAME"):
                        runner_lines = lines[1:]
                        count = len(runner_lines)
                        total_runners += count
                        # Try to count running (Ready=1 or READY column)
                        for line in runner_lines:
                            cols = line.split()
                            # Try to find READY or READY_REPLICAS column (4th col by default)
                            ready = None
                            if len(cols) >= 4:
                                ready = cols[3]
                            if ready == "1":
                                running_count += 1
                            else:
                                not_running_count += 1
                except subprocess.CalledProcessError as e:
                    print(f"Error: {e.output.strip()}")
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
    main()#!/bin/bash

CSV_FILE="cluster1_namespaces.csv"
OUTPUT_FILE="ephemeral_runner_report_$(date +%Y%m%d_%H%M).csv"
TEMP_OUTPUT="ephemeral_runners_report.csv"
TMP_DIR="/tmp/runner_data_$$"
mkdir -p "$TMP_DIR"

# Prompt for TKGI credentials
read -rp "Enter username: " USERNAME
if [ -z "$USERNAME" ]; then
    echo " Username is required. Exiting."
    exit 1
fi

read -rsp "Enter password: " PASSWORD
echo

# Check CSV exists
if [ ! -f "$CSV_FILE" ]; then
    echo " CSV file '$CSV_FILE' not found."
    exit 1
fi

# Output CSV Header
echo "Cluster,API_Endpoint,Namespace,Runner_Name,GitHub_Config_URL,Org_Name,Runner_ID,Age,Status" > "$OUTPUT_FILE"


# Get unique cluster/api combinations
mapfile -t clusters < <(awk -F',' '!seen[$1","$2]++ { print $1 "," $2 }' "$CSV_FILE")

for entry in "${clusters[@]}"; do
    IFS=',' read -r cluster_name api_endpoint <<< "$entry"

    echo -e "\n========================="
    echo "Cluster: $cluster_name"
    echo "API:     $api_endpoint"
    echo "========================="

    # Authenticate once per cluster
    echo "$PASSWORD" | tkgi get-kubeconfig "$cluster_name" -u "$USERNAME" -a "$api_endpoint" -k
    if [ $? -ne 0 ]; then
        echo " Authentication failed for $cluster_name. Skipping."
        continue
    fi

    mapfile -t namespaces < <(awk -F',' -v cl="$cluster_name" '$1 == cl { print $3 }' "$CSV_FILE")

    # Run kubectl calls in parallel
    MAX_PARALLEL=20

TMP_DIR="/tmp/ephemeral_runner_data"

mkdir -p "$TMP_DIR"

# Get timestamp once

now=$(date -u +%s)

# Simple semaphore function to limit parallel jobs

parallel_jobs=0
run_limited() {
    while [ "$parallel_jobs" -ge "$MAX_PARALLEL" ]; do
        wait -n
        ((parallel_jobs--))
    done
    {
        ns="$1"
        STATUS_FILTER=""
        case "$OPTION" in
            -running)
                STATUS_FILTER="Running"
                ;;
            -pending|-DeletePending)
                STATUS_FILTER="Pending"
                ;;
            -failed|-DeleteFailed)
                STATUS_FILTER="Failed"
                ;;
        esac
        output=$(kubectl get ephemeralrunner -n "$ns" -o custom-columns=NAME:.metadata.name,CONFIG_URL:.spec.githubConfigUrl,RUNNERID:.status.runnerId,READY:.status.readyReplicas,TOTAL:.status.replicas,AGE:.metadata.creationTimestamp --no-headers 2>/dev/null)
        if [ -z "$output" ]; then
            exit 0
        fi
        # Use a bash array to store runner details in memory
        declare -a runner_lines
        while IFS= read -r line; do
            read -r name config_url runner_id ready total creation_ts <<< "$line"
            org_name=$(cut -d'/' -f4 <<< "$config_url")
            created=$(date -u -d "$creation_ts" +%s 2>/dev/null)
            if [[ -n "$created" && "$created" =~ ^[0-9]+$ ]]; then
                age_min=$(( (now - created) / 60 ))
                age="${age_min}m"
            else
                age="N/A"
            fi
            if [[ "$ready" == "$total" && "$ready" != "" && "$total" != "" && "$ready" != "0" ]]; then
                status="Running"
            elif [[ "$ready" == "0" ]]; then
                status="Failed"
            else
                status="Pending"
            fi
            if [[ -n "$STATUS_FILTER" ]]; then
                if [[ "$status" == "$STATUS_FILTER" ]]; then
                    runner_lines+=("$cluster_name,$api_endpoint,$ns,$name,$config_url,$org_name,$runner_id,$age,$status")
                fi
            else
                runner_lines+=("$cluster_name,$api_endpoint,$ns,$name,$config_url,$org_name,$runner_id,$age,$status")
            fi
        done <<< "$output"
        # Output all runner lines at once
        for rline in "${runner_lines[@]}"; do
            echo "$rline"
        done
    } > "$TMP_DIR/$ns.csv" &
    ((parallel_jobs++))
}
# Main loop: run each namespace with controlled parallelism

for ns in "${namespaces[@]}"; do
    run_limited "$ns"
done

# Wait for remaining jobs
    wait
done

# Merge all per-namespace CSVs
cat "$TMP_DIR"/*.csv >> "$OUTPUT_FILE"
cp "$OUTPUT_FILE" "$TEMP_OUTPUT"
rm -rf "$TMP_DIR"

echo -e "\n Data collection complete. Output saved to: $OUTPUT_FILE"
echo

# Run reporting script with any passed options
#if [ -n "$1" ]; then
 #   ./clusteratc.sh "$@"
#fi

CSV_FILE="ephemeral_runners_report.csv"

if [ ! -f "$CSV_FILE" ]; then
    echo " CSV file '$CSV_FILE' not found."
    exit 1
fi

OPTION=""
ORG_FILTER=""
ALL_ORGS=false

# Parse flags
for arg in "$@"; do
    case $arg in
        -summary|-running|-pending|-failed|-DeletePending|-DeleteFailed)
            OPTION="$arg"
            ;;
        --org)
            shift
            ORG_FILTER="$1"
            ;;
        -AllOrgs)
            ALL_ORGS=true
            ;;
        -h|--help)
            echo "Usage: $0 [option] [--org <ORG>] [-AllOrgs]"
            echo "Options:"
            echo "  -summary             Show summary of all runners by org"
            echo "  -running             Show running runners grouped by org"
            echo "  -pending             Show pending runners grouped by org"
            echo "  -failed              Show failed runners grouped by org"
            echo "  -DeletePending       Delete pending runners (interactive, based on CSV)"
            echo "  -DeleteFailed        Delete failed runners (interactive, based on CSV)"
            echo "  -DeletePendingFast   Real-time, interactive deletion of pending runners (no CSV, acts on current status)"
            echo "  -DeleteFailedFast    Real-time, interactive deletion of failed runners (no CSV, acts on current status)"
            echo "  --org <ORG>          Filter by organization"
            echo "  -AllOrgs             Include all organizations (default behavior)"
            echo "  -h, --help           Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 -summary"
            echo "  $0 -pending --org myorg"
            echo "  $0 -DeletePending"
            echo "  $0 -DeletePendingFast"
            exit 0
            ;;
    esac
    shift
done

if [ -z "$OPTION" ]; then
    echo "Usage: $0 [option] [--org <ORG>] [-AllOrgs]"
    echo "Try '$0 --help' for more information."
    exit 1
fi

print_table() {
    printf "%s\n" "------------------------------------------------------------------------------------------"
    printf "%-40s %-40s %-20s %10s\n" "Runner Name" "Org Name" "Age" "Count"
    printf "%s\n" "------------------------------------------------------------------------------------------"
}

case "$OPTION" in
  -summary)
    echo
    #echo "Summary Option:"
    #echo "Command: GetRunners -summary"
    echo
    HEADER_FMT="%-35s %10s %10s %10s %10s\n"
    ROW_FMT="%-35s %10d %10d %10d %10d\n"

    printf "$HEADER_FMT" "Org Name" "Running" "Failed" "Pending" "Total"
    printf -- "-------------------------------------------------------------------------------\n"

    awk -F',' -v row_fmt="$ROW_FMT" '
    NR > 1 {
        org=tolower($6);
        status=tolower($9);
        total[org]++
        if (status=="running") running[org]++
        else if (status=="failed") failed[org]++
        else if (status=="pending") pending[org]++
    }
    END {
        grand_running=0; grand_failed=0; grand_pending=0; grand_total=0;
        for (org in total) {
            r=running[org]+0;
            f=failed[org]+0;
            p=pending[org]+0;
            t=total[org];
            printf row_fmt, org, r, f, p, t;
            grand_running+=r;
            grand_failed+=f;
            grand_pending+=p;
            grand_total+=t;
        }
        printf "-------------------------------------------------------------------------------\n";
        printf row_fmt, "Total", grand_running, grand_failed, grand_pending, grand_total;
    }' "$CSV_FILE"
    ;;

  -running|-pending|-failed)
    STATUS_LC="${OPTION#-}"
    STATUS_LC="${STATUS_LC,,}"
    #echo "${STATUS_LC^} Option:"
    #echo "Command: GetRunners $OPTION"
    echo
    print_table

    awk -F',' -v status="$STATUS_LC" -v org_filter="${ORG_FILTER,,}" -v all_orgs="$ALL_ORGS" '
    NR > 1 {
        actual_status = tolower($9)
        org = $6
        org_lc = tolower(org)
        if (actual_status == status && (all_orgs || org_filter == "" || org_filter == org_lc)) {
            key = org
            runners[key] = (runners[key] ? runners[key] ORS : "") $4 "\t" org "\t" $8
            count[key]++
            grand_total++
        }
    }
    END {
        for (org in runners) {
            split(runners[org], lines, ORS)
            for (i in lines) {
                split(lines[i], fields, "\t")
                printf "%-40s %-40s %-40s %10s\n", fields[1], fields[2], fields[3], ""
            }
            printf "%-100s %8d\n\n", "Total", count[org]
        }
        printf "%s\n", "------------------------------------------------------------------------------------------"
        printf "%-100s %8d\n", "Grand Total", grand_total
    }' "$CSV_FILE"
    ;;

  -DeletePending|-DeleteFailed)
    STATUS="${OPTION#-Delete}"
    STATUS_LC="${STATUS,,}"

    #echo "Delete ${STATUS^} Option:"
    #echo "Command: GetRunners $OPTION"
    echo
    print_table

    awk -F',' -v status="$STATUS_LC" -v org_filter="${ORG_FILTER,,}" -v all_orgs="$ALL_ORGS" '
    NR > 1 {
        actual_status = tolower($9)
        org = $6
        org_lc = tolower(org)
        if (actual_status == status && (all_orgs || org_filter == "" || org_filter == org_lc)) {
            cluster = $1; namespace = $3; pod_name = $4; age = $8;
            runners[org] = (runners[org] ? runners[org] ORS : "") pod_name "\t" org "\t" age "\t" cluster "\t" namespace
            count[org]++
            grand_total++
        }
    }
    END {
        for (org in runners) {
            split(runners[org], lines, ORS)
            for (i in lines) {
                split(lines[i], fields, "\t")
                printf "%-35s %-25s %-10s %10s\n", fields[1], fields[2], fields[3], ""
                delete_cmds = delete_cmds sprintf("tkgi get-kubeconfig %s && kubectl -n %s delete pod %s\n", fields[4], fields[5], fields[1])
            }
            printf "%-72s %8d\n\n", "Total", count[org]
        }
        printf "%s\n", "------------------------------------------------------------------------------------------"
        printf "%-72s %8d\n", "Grand Total", grand_total
        print delete_cmds > "/tmp/delete_${status}_runners.sh"
    }' "$CSV_FILE"

    echo
    read -rp "Do you want to delete these runners? (Y/N): " confirm
    if [[ "$confirm" == "Y" || "$confirm" == "y" ]]; then
        echo
        echo "Executing delete commands..."
        echo "-------------------------------------------------------------"
        bash "/tmp/delete_${STATUS_LC}_runners.sh"
        echo "-------------------------------------------------------------"
    else
        echo " Skipping deletion. No runners were deleted."
    fi
    ;;

  -DeletePendingFast|-DeleteFailedFast)
    # Add real-time delete options for immediate, interactive deletion
    STATUS_FILTER=""
    if [[ "$OPTION" == "-DeletePendingFast" ]]; then
        STATUS_FILTER="Pending"
    elif [[ "$OPTION" == "-DeleteFailedFast" ]]; then
        STATUS_FILTER="Failed"
    fi
    for entry in "${clusters[@]}"; do
        IFS=',' read -r cluster_name api_endpoint <<< "$entry"
        echo "$PASSWORD" | tkgi get-kubeconfig "$cluster_name" -u "$USERNAME" -a "$api_endpoint" -k
        mapfile -t namespaces < <(awk -F',' -v cl="$cluster_name" '$1 == cl { print $3 }' "$CSV_FILE")
        for ns in "${namespaces[@]}"; do
            output=$(kubectl get ephemeralrunner -n "$ns" -o custom-columns=NAME:.metadata.name,READY:.status.readyReplicas,TOTAL:.status.replicas --no-headers 2>/dev/null)
            if [ -z "$output" ]; then
                continue
            fi
            while IFS= read -r line; do
                read -r name ready total <<< "$line"
                if [[ "$STATUS_FILTER" == "Pending" && "$ready" == "0" ]]; then
                    echo "Pending runner: $name in namespace: $ns (Cluster: $cluster_name)"
                    read -rp "Delete this runner? (Y/N): " confirm
                    if [[ "$confirm" =~ ^[Yy]$ ]]; then
                        kubectl -n "$ns" delete pod "$name"
                    fi
                elif [[ "$STATUS_FILTER" == "Failed" && "$ready" == "0" ]]; then
                    echo "Failed runner: $name in namespace: $ns (Cluster: $cluster_name)"
                    read -rp "Delete this runner? (Y/N): " confirm
                    if [[ "$confirm" =~ ^[Yy]$ ]]; then
                        kubectl -n "$ns" delete pod "$name"
                    fi
                fi
            done <<< "$output"
        done
    done
    exit 0
    ;;

  *)
    echo " Invalid option. Use --help to see usage."
    exit 1
    ;;
esac
