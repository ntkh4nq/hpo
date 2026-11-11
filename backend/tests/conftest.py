import os

os.environ["LANGFUSE_TRACING_ENABLED"] = "false"  # tests must not send traces to Langfuse
