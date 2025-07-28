#!/bin/bash

CSV_FILE="cluster1_namespaces.csv"

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

    # Only collect pending runners, use arrays, no CSV output
    declare -a pending_runners
    now=$(date -u +%s)

    for ns in "${namespaces[@]}"; do
        output=$(kubectl get ephemeralrunner -n "$ns" -o custom-columns=NAME:.metadata.name,CONFIG_URL:.spec.githubConfigUrl,RUNNERID:.status.runnerId,READY:.status.readyReplicas,TOTAL:.status.replicas,AGE:.metadata.creationTimestamp --no-headers 2>/dev/null)
        if [ -z "$output" ]; then
            continue
        fi
        while IFS= read -r line; do
            read -r name config_url runner_id ready total creation_ts <<< "$line"
            created=$(date -u -d "$creation_ts" +%s 2>/dev/null)
            if [[ -n "$created" && "$created" =~ ^[0-9]+$ ]]; then
                age_min=$(( (now - created) / 60 ))
                age="${age_min}m"
            else
                age="N/A"
            fi
            # Only collect pending runners
            if [[ "$ready" != "$total" && "$ready" != "" && "$total" != "" ]]; then
                status="Pending"
                pending_runners+=("$cluster_name|$api_endpoint|$ns|$org_name|$name|$config_url|$runner_id|$age|$status")
            fi
        done <<< "$output"
    done

    # Print all pending runners for this cluster/org
    if [ ${#pending_runners[@]} -gt 0 ]; then
        echo -e "\nPending Runners:"
        printf "%-20s %-30s %-20s %-20s %-30s %-20s %-10s %-10s %-10s\n" \
            "Cluster" "API_Endpoint" "Namespace" "Org_Name" "Runner_Name" "GitHub_Config_URL" "Runner_ID" "Age" "Status"
        for r in "${pending_runners[@]}"; do
            IFS='|' read -r cl api ns org name url rid age status <<< "$r"
            printf "%-20s %-30s %-20s %-20s %-30s %-20s %-10s %-10s %-10s\n" \
                "$cl" "$api" "$ns" "$org" "$name" "$url" "$rid" "$age" "$status"
        done
    else
        echo "No pending runners found for this cluster/org."
    fi
done
