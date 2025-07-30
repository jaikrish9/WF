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

    # Function to process a namespace
    process_namespace() {
        ns="$1"
        output=$(kubectl get ephemeralrunner -n "$ns" -o custom-columns=NAME:.metadata.name,CONFIG_URL:.spec.githubConfigUrl,RUNNERID:.status.runnerId,READY:.status.readyReplicas,TOTAL:.status.replicas,AGE:.metadata.creationTimestamp --no-headers 2>/dev/null)
        if [ -z "$output" ]; then
            return
        fi
        now=$(date -u +%s)
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
            echo "$cluster_name,$api_endpoint,$ns,$name,$config_url,$org_name,$runner_id,$age,$status"
        done <<< "$output"
    }

    export -f process_namespace
    export cluster_name api_endpoint

    # Run kubectl calls in parallel using xargs
    printf "%s\n" "${namespaces[@]}" | xargs -n1 -P10 bash -c 'process_namespace "$@"' _ >> "$OUTPUT_FILE"
    cp "$OUTPUT_FILE" "$TEMP_OUTPUT"
done

echo -e "\n Data collection complete. Output saved to: $OUTPUT_FILE"
echo

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
            echo "  -summary         Show summary of all runners by org"
            echo "  -running         Show running runners grouped by org"
            echo "  -pending         Show pending runners grouped by org"
            echo "  -failed          Show failed runners grouped by org"
            echo "  -DeletePending   Delete pending runners (interactive)"
            echo "  -DeleteFailed    Delete failed runners (interactive)"
            echo "  --org <ORG>      Filter by organization"
            echo "  -AllOrgs         Include all organizations (default behavior)"
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

  *)
    echo " Invalid option. Use --help to see usage."
    exit 1
    ;;
esac
