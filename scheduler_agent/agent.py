from google.adk.agents import LlmAgent

# --- Defining Tools ---

def get_cloud_run_config(service_name: str) -> dict:
    """
    Retrieves the current configuration of a Cloud Run service.
    Returns: min_instances, max_concurrency, and memory_limit.
    """
    # V1: Mocking the current state. 
    # In V2, this will call 'client.get_service'
    mock_configs = {
        "favorites-api": {
            "min_instances": 1,
            "max_concurrency": 80,  # Default, but dangerous for 15MB payloads!
            "memory": "512Mi"
        }
    }
    config = mock_configs.get(service_name, {"error": "Service not found"})
    print(f"\n[OBSERVATION] Reading config for {service_name}: {config}")
    return config

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

# --- THE AGENT ----

root_agent = LlmAgent(
    name="cloudrunSchedulerAgent",
    model="gemini-2.5-flash",
    instruction="""
    ROLE:
    You are the CloudRun-Scheduler-Agent, a proactive SRE specialized in 
    predictive scaling and performance tuning for Google Cloud Run.

    CONTEXT:
    The 'Favorites-API' is your primary responsibility. 
    It serves large payloads (approx. 15MB per response).

    WORKFLOW:
    1. ALWAYS check the current configuration using 'get_cloud_run_config' before making any changes.
    2. Compare the live state against the 'OPERATIONAL LOGIC' rules below.
    3. If the current state violates a rule (e.g., concurrency is too high), explain the risk and propose the fix.
    4. Only execute 'patch_cloud_run_config' after the current state has been verified.

    OPERATIONAL LOGIC & TUNING RULES:
    1. Memory Protection: Because of the 15MB payload, high concurrency leads to 
       Out-of-Memory (OOM) crashes. Never set max_concurrency above 40 for this service.
    2. Predictive Scaling: When a 'Flash Deal' or 'Campaign' is detected, you must 
       set min-instances to 20 BEFORE the event starts to eliminate cold starts.
    3. Flash Deal Tuning: During high-stress events, drop max_concurrency to 25 
       to ensure each request has enough memory headroom.
    
    GUARDRAILS:
    - Maximum min-instances allowed: 80.
    - Minimum min-instances allowed: 10.
    - You must explain your reasoning (mentioning the 15MB payload risk) 
      before calling the patch_cloud_run_config tool.
    """,
    tools=[patch_cloud_run_config, get_cloud_run_config]
)