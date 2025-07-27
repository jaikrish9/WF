#!/bin/bash

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
# Update header to remove Org_Name extraction from config URL
# New CSV format: cluster_name,api_endpoint,namespace,org_name
# Runner output: Cluster,API_Endpoint,Namespace,Org_Name,Runner_Name,GitHub_Config_URL,Runner_ID,Age,Status

echo "Cluster,API_Endpoint,Namespace,Org_Name,Runner_Name,GitHub_Config_URL,Runner_ID,Age,Status" > "$OUTPUT_FILE"

# Get unique cluster/api/org combinations
mapfile -t clusters < <(awk -F',' '!seen[$1","$2","$4]++ { print $1 "," $2 "," $4 }' "$CSV_FILE")

for entry in "${clusters[@]}"; do
    IFS=',' read -r cluster_name api_endpoint org_name <<< "$entry"
    echo -e "\n========================="
    echo "Cluster: $cluster_name"
    echo "API:     $api_endpoint"
    echo "Org:     $org_name"
    echo "========================="
    # Authenticate once per cluster
    echo "$PASSWORD" | tkgi get-kubeconfig "$cluster_name" -u "$USERNAME" -a "$api_endpoint" -k
    if [ $? -ne 0 ]; then
        echo " Authentication failed for $cluster_name. Skipping."
        continue
    fi
    mapfile -t namespaces < <(awk -F',' -v cl="$cluster_name" -v org="$org_name" '$1 == cl && $4 == org { print $3 }' "$CSV_FILE")

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
                    runner_lines+=("$cluster_name,$api_endpoint,$ns,$org_name,$name,$config_url,$runner_id,$age,$status")
                fi
            else
                runner_lines+=("$cluster_name,$api_endpoint,$ns,$org_name,$name,$config_url,$runner_id,$age,$status")
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
