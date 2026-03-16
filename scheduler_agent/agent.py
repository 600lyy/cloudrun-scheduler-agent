import datetime
import time
import os

from google.adk.agents import LlmAgent
from google.cloud import monitoring_v3
from google.cloud import run_v2
from google.api_core import exceptions
from google.adk import agents
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

# --- Defining Tools ---

def get_cloud_run_metrics(service_name: str, metric_type: str) -> dict:
    """
    Fetches real-time metrics from Google Cloud Monitoring.
    Supported types: 'memory_utilization', 'request_count'.
    """
    try:
        client = monitoring_v3.MetricServiceClient()
        project_id = os.environ.get("PROJECT_ID", None)
        project_name = f"projects/{project_id}"

        metric_map = {
            "request_count": "run.googleapis.com/request_count",
            "memory_utilization": "run.googleapis.com/container/memory/utilization",
        }

        gcp_metric = metric_map.get(metric_type, metric_type)

        # Define the time interval (last 5 mins)
        now = time.time()
        interval = monitoring_v3.TimeInterval(mapping={
            "end_time": {"seconds": int(now)},
            "start_time": {"seconds": int(now - 300)},
            }
        )

        filter_str = (
            f'resource.type = "cloud_run_revision" AND '
            f'resource.labels.service_name = "{service_name}" AND '
            f'metric.type = "{gcp_metric}"'
        )

        # Define the aggregation object
        aggregation = monitoring_v3.Aggregation({
            "alignment_period": {"seconds": 300},
            "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
        })

        results = client.list_time_series(
            request={
                "name": project_name,
                "filter": filter_str,
                "interval": interval,
                "aggregation": aggregation, # Pass it inside the request dictionary
            }
        )

        # Parse results
        points = list()
        for r in results:
            for p in r.points:
                val = p.value.double_value if "utilization" in gcp_metric else point.value.int64_value
                points.append(val)
        
        if not points:
            return {"status": "no_data", "value": 0.0, "message": f"No data found for {service_name} in the last 5m."}

        avg_val = sum(points) / len(points)
        print(f"\n[REAL-TIME OBSERVATION] {metric_type} for {service_name}: {avg_val:.2f}")
        
        return {
            "service": service_name,
            "metric": metric_type,
            "value": round(avg_val, 3),
            "status": "success"
        }
    
    except exceptions.PermissionDenied:
        return {"status": "error", "message": "IAM Permission Denied. Check monitoring.viewer role."}
    except exceptions.InvalidArgument as e:
        return {"status": "error", "message": f"Invalid Filter or Project ID: {str(e)}"}
    except Exception as e:
        print(f"DEBUG: Unexpected error in get_cloud_run_metrics: {e}")
        return {"status": "error", "message": f"An unexpected error occurred: {str(e)}"}


def get_cloud_run_config(service_name: str, region: str = "us-central1") -> dict:
    """
    Retrieves the live configuration of a Cloud Run service (v2).
    Returns: min_instances, max_concurrency, and memory limits.
    """
    try:
        client = run_v2.ServicesClient()
        project_id = os.environ.get("PROJECT_ID", None)
        
        # The fully qualified name: projects/{project}/locations/{location}/services/{service}
        resource_name = f"projects/{project_id}/locations/{region}/services/{service_name}"
        
        service = client.get_service(name=resource_name)
        
        # Extracting scaling and resource settings from the first container in the template
        template = service.template
        container = template.containers[0]
        
        scaling = template.scaling
        resources = container.resources
        
        config = {
            "status": "success",
            "service": service_name,
            "min_instances": scaling.min_instance_count or 0,
            "max_instances": scaling.max_instance_count or "default",
            "max_concurrency": template.max_instance_request_concurrency or 80,
            "memory_limit": resources.limits.get("memory", "unknown"),
            "cpu_limit": resources.limits.get("cpu", "unknown")
        }
        
        print(f"\n[REAL-TIME AUDIT] Fetched config for {service_name}: {config}")
        return config

    except exceptions.NotFound:
        return {"status": "error", "message": f"Service '{service_name}' not found in {region}."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to fetch config: {str(e)}"}

def patch_cloud_run_config(service_name: str, min_instances: int, max_concurrency: int) -> dict:
    """
    Updates the Cloud Run service configuration.
    Use this to pre-warm (min_instances) or tune engine performance (max_concurrency).
    """
    # V1: Mocking the execution. In V2, this calls the Cloud Run Admin API.
    print(f"\n[EXECUTION] Patching {service_name}:")
    print(f"  -> min-instances: {min_instances}")
    print(f"  -> max-concurrency: {max_concurrency}")
    
    return {
        "status": "success",
        "service": service_name,
        "applied": {"min_instances": min_instances, "concurrency": max_concurrency}
    }


# --- 4. TOOL: TIME UTILITY ---
def get_current_system_time() -> str:
    """Returns the current system time for context."""
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")

# --- 5. SYSTEM INSTRUCTIONS (OPTION A: LEFT-ALIGNED) ---
SYSTEM_INSTRUCTION = """ROLE:
You are the CloudRun-Scheduler-Agent, a proactive SRE specialized in 
predictive scaling and performance tuning for Google Cloud Run.
You manage multiple microservices.
You respond to both manual queries and automated Cloud Monitoring alerts.

WORKFLOW:
1. EXTRACTION: Identify the target service name from the user's request.
2. ALWAYS check the current configuration using 'get_cloud_run_config'.
3. ALWAYS check current health metrics using 'get_cloud_run_metrics'.
4. ALWAYS check the current system time using 'get_current_system_time'.
5. Compare the live state and metrics against the 'OPERATIONAL LOGIC' rules below.
6. If the current state violates a rule or metrics show high risk, explain the risk and propose the fix.
7. IAM CHECK: If a tool returns an 'error' status (e.g., Permission Denied), do not guess values. Stop and inform the user of the specific error.
8. Only execute 'patch_cloud_run_config' after verification is complete.

OPERATIONAL LOGIC & TUNING RULES:
1. Never guess a service name; if ambiguous, ask the user for clarification.
2. Memory Protection: Because of the 15MB payload, high concurrency leads to 
   Out-of-Memory (OOM) crashes. Never set max_concurrency above 40 for this service.
3. Reactive Memory Tuning: If 'memory_utilization' exceeds 0.80 (80%), immediately 
   drop 'max_concurrency' to 20, regardless of current events, to stop OOM crashes.
4. Predictive Scaling: When a 'Flash Deal' or 'Campaign' is detected, you must 
   set min-instances to 20 BEFORE the event starts to eliminate cold starts.
5. Flash Deal Tuning: During high-stress events, drop max_concurrency to 25 
   to ensure each request has enough memory headroom.

GUARDRAILS:
- Maximum min-instances allowed: 80.
- Minimum min-instances allowed: 10.
- You must explain your reasoning (mentioning the 15MB payload risk and current metrics) 
  before calling the patch_cloud_run_config tool."""

# --- 6. AGENT CREATION ---
def create_agent(memory_bank=None, session_service=None):
    return agents.LlmAgent(
        name="cloudrunSchedulerAgent",
        model="gemini-2.5-flash",
        instruction=SYSTEM_INSTRUCTION,
        tools=[
            get_cloud_run_config, 
            get_cloud_run_metrics, 
            patch_cloud_run_config, 
            get_current_system_time,
            PreloadMemoryTool()
        ]
        # REMOVE memory_bank and session_service from here 
        # if your ADK version throws the ValidationError
    )

root_agent = create_agent()