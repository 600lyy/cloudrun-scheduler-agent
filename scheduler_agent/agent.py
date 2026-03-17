import datetime
import time
import os
import asyncio

from google.adk.agents import LlmAgent
from google.cloud.monitoring_v3 import MetricServiceAsyncClient, TimeInterval, Aggregation
from google.cloud import run_v2
from google.api_core import exceptions
from google.adk import agents
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from scheduler_agent._prompts import SYSTEM_INSTRUCTION

# --- Defining Tools ---

async def get_cloud_run_metrics(service_name: str) -> dict:
    """
    Expert Tool: Fetches CPU, Memory, and Requests in parallel.
    Includes robust error handling for API timeouts or permission issues.
    """
    client = MetricServiceAsyncClient()
    project_id = os.environ.get("PROJECT_ID", None)
    project_name = f"projects/{project_id}"

    now = time.time()
    interval = TimeInterval(mapping={
        "end_time": {"seconds": int(now)},
        "start_time": {"seconds": int(now - 300)},
        }
    )

    metrics = {
        "cpu": "run.googleapis.com/container/cpu/utilization",
        "memory": "run.googleapis.com/container/memory/utilization",
        "requests": "run.googleapis.com/request_count"
    }

    async def fetch_one(m_type, m_path):
        filter_str = (
            f'resource.type = "cloud_run_revision" AND '
            f'resource.labels.service_name = "{service_name}" AND '
            f'metric.type = "{m_path}"'
        )

        aligner = Aggregation.Aligner.ALIGN_SUM if m_type == "requests" else Aggregation.Aligner.ALIGN_MEAN
        
        results = await client.list_time_series(request={
            "name": project_name,
            "filter": filter_str,
            "interval": interval,
            "aggregation": Aggregation({"alignment_period": {"seconds": 300}, "per_series_aligner": aligner}),
        })

        try:
            pints = []
            async for page in results:
                for point in page.points:
                    val = point.value.double_value if "utilization" in m_path else point.value.int64_value
                    points.append(val)
            
            avg_val = round(sum(points) / len(points), 4) if points else 0.0
            return m_type, avg_val
        except Exception as e:
            return m_type, f"Error: {str(e)}"
        
    try:

        tasks = [fetch_one(k, v) for k, v in metrics.items()]
        results = await asyncio.gather(*tasks)

        # Process results and identify if any specific metric failed
        metrics_summary = {}
        errors = []

        for res in results:
            if isinstance(res, Exception):
                errors.append(f"System Error: {str(res)}")
                continue
            
            m, v = res
            if isinstance(value, str) and value.startswith("Error"):
                errors.append(f"{m}: {v}")
            else:
                metrics_summary[m]=v

        return {
            "status": "partial_success" if errors and metrics_summary else "success" if not errors else "error",
            "service": service_name,
            "data": metrics_summary,
            "errors": errors if errors else None
        }
    except exceptions.Unauthenticated:
        # This stops the tool and tells the LLM EXACTLY what happened
        raise RuntimeError("TERMINAL_AUTH_ERROR: Re-authentication required.")
    except Exception as e:
        return {"status": "error", "message": str(e)}
    

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
    except exceptions.Unauthenticated:
        return {
            "status": "error", 
            "message": "AUTH_FAILED: Your GCP credentials have expired. Run 'gcloud auth application-default login'."
        }
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